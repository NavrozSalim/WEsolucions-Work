"""
Costco AU product scraper (Playwright + Chromium).

Public API:
  scrape_costco_au(vendor_url, region, session=None) -> {"price": float|None, "stock": int|None, "title": str|None}
  close_costco_au_session(session)

Notes:
- Keeps the same public API as your current scraper.
- Uses Playwright browser instead of requests for Costco AU.
- Rotates proxies from COSTCO_AU_PROXY_URLS / COSTCO_AU_PROXY_URL / PROXY_URLS / PROXY_URL / PROXY_ENDPOINTS.
- Returns legacy ScrapeResult payload like your current pipeline expects.
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import re
import shutil
import tempfile
import time
from typing import Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from .core import ScrapeResult, detect_block, parse_price_text, random_delay

logger = logging.getLogger("scrapers.costco_au")

RETRY_LIMIT = 3          # one extra attempt vs before — pays off given proxy rotation
PAGE_TIMEOUT_MS = 50_000
RETRY_BACKOFF_SEC = (3.0, 7.0)

# ---------------------------------------------------------------------------
# User-agents — keep in sync with a real Chrome stable release
# ---------------------------------------------------------------------------
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

CHROMIUM_CANDIDATES = [
    os.environ.get("PLAYWRIGHT_CHROMIUM_PATH", "").strip(),
    "/usr/bin/google-chrome-stable",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    shutil.which("google-chrome-stable"),
    shutil.which("google-chrome"),
    shutil.which("chromium"),
    shutil.which("chromium-browser"),
]

# ---------------------------------------------------------------------------
# Heavy stealth init script — patches all major headless fingerprint leaks
# ---------------------------------------------------------------------------
_STEALTH_SCRIPT = """
// 1. Remove webdriver flag
Object.defineProperty(navigator, 'webdriver', { get: () => undefined, configurable: true });

// 2. Restore plugins array (headless Chrome has 0 plugins)
const _plugins = [
  { name: 'Chrome PDF Plugin',     filename: 'internal-pdf-viewer',   description: 'Portable Document Format' },
  { name: 'Chrome PDF Viewer',     filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
  { name: 'Native Client',         filename: 'internal-nacl-plugin',  description: '' },
];
const pluginArray = Object.create(PluginArray.prototype);
_plugins.forEach((p, i) => {
  const plugin = Object.create(Plugin.prototype);
  Object.defineProperty(plugin, 'name',        { get: () => p.name });
  Object.defineProperty(plugin, 'filename',    { get: () => p.filename });
  Object.defineProperty(plugin, 'description', { get: () => p.description });
  Object.defineProperty(plugin, 'length',      { get: () => 0 });
  Object.defineProperty(pluginArray, i, { get: () => plugin });
});
Object.defineProperty(pluginArray, 'length', { get: () => _plugins.length });
pluginArray.item = (i) => pluginArray[i];
pluginArray.namedItem = (n) => _plugins.find(p => p.name === n) || null;
pluginArray[Symbol.iterator] = function*() { for (let i = 0; i < _plugins.length; i++) yield this[i]; };
Object.defineProperty(navigator, 'plugins', { get: () => pluginArray, configurable: true });

// 3. Restore mimeTypes
const mimeArray = Object.create(MimeTypeArray.prototype);
const _mimes = [
  { type: 'application/pdf',               description: 'Portable Document Format', suffixes: 'pdf' },
  { type: 'text/pdf',                       description: 'Portable Document Format', suffixes: 'pdf' },
];
_mimes.forEach((m, i) => {
  const mime = Object.create(MimeType.prototype);
  Object.defineProperty(mime, 'type',        { get: () => m.type });
  Object.defineProperty(mime, 'description', { get: () => m.description });
  Object.defineProperty(mime, 'suffixes',    { get: () => m.suffixes });
  Object.defineProperty(mimeArray, i, { get: () => mime });
  Object.defineProperty(mimeArray, m.type, { get: () => mime });
});
Object.defineProperty(mimeArray, 'length', { get: () => _mimes.length });
mimeArray.item = (i) => mimeArray[i];
mimeArray.namedItem = (n) => _mimes.find(m => m.type === n) || null;
Object.defineProperty(navigator, 'mimeTypes', { get: () => mimeArray, configurable: true });

// 4. Platform / language
Object.defineProperty(navigator, 'platform',  { get: () => 'Win32', configurable: true });
Object.defineProperty(navigator, 'language',  { get: () => 'en-AU', configurable: true });
Object.defineProperty(navigator, 'languages', { get: () => ['en-AU', 'en-US', 'en'], configurable: true });

// 5. Hardware concurrency / device memory — match a mid-range laptop
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8, configurable: true });
try { Object.defineProperty(navigator, 'deviceMemory', { get: () => 8, configurable: true }); } catch(_) {}

// 6. Chrome runtime object (missing in headless)
if (!window.chrome) {
  window.chrome = {
    app: { InstallState: 'hehe', RunningState: 'hehe', getDetails: function(){}, getIsInstalled: function(){} },
    csi: function(){},
    loadTimes: function(){},
    runtime: {
      OnInstalledReason: {},
      OnRestartRequiredReason: {},
      PlatformArch: {},
      PlatformNaclArch: {},
      PlatformOs: {},
      RequestUpdateCheckStatus: {},
      connect: function(){},
      sendMessage: function(){},
    },
  };
}

// 7. Notification.permission — headless returns 'denied' by default
try {
  const _origQuery = window.Notification
    ? Notification.requestPermission
    : undefined;
  Object.defineProperty(Notification, 'permission', { get: () => 'default' });
} catch(_) {}

// 8. Canvas fingerprint noise — tiny per-session jitter so every run differs
(function() {
  const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
  HTMLCanvasElement.prototype.toDataURL = function(type) {
    const ctx = this.getContext('2d');
    if (ctx) {
      const id = ctx.getImageData(0, 0, 1, 1);
      id.data[0] = id.data[0] ^ (Math.random() * 3 | 0);
      ctx.putImageData(id, 0, 0);
    }
    return origToDataURL.apply(this, arguments);
  };
  const origGetContext = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function(type, attribs) {
    const ctx = origGetContext.apply(this, arguments);
    if (type === '2d' && ctx) {
      const origFillText = ctx.fillText.bind(ctx);
      ctx.fillText = function(text, x, y, maxWidth) {
        origFillText(text, x + (Math.random() * 0.1), y + (Math.random() * 0.1), maxWidth);
      };
    }
    return ctx;
  };
})();

// 9. WebGL vendor / renderer — expose a plausible GPU string
(function() {
  const origGetParam = WebGLRenderingContext.prototype.getParameter;
  WebGLRenderingContext.prototype.getParameter = function(param) {
    if (param === 37445) return 'Google Inc. (Intel)';        // UNMASKED_VENDOR_WEBGL
    if (param === 37446) return 'ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)';
    return origGetParam.call(this, param);
  };
  try {
    const origGetParam2 = WebGL2RenderingContext.prototype.getParameter;
    WebGL2RenderingContext.prototype.getParameter = function(param) {
      if (param === 37445) return 'Google Inc. (Intel)';
      if (param === 37446) return 'ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)';
      return origGetParam2.call(this, param);
    };
  } catch(_) {}
})();

// 10. screen / window sizing — keep consistent with viewport arg
Object.defineProperty(screen, 'availWidth',  { get: () => 1366 });
Object.defineProperty(screen, 'availHeight', { get: () => 900  });
Object.defineProperty(screen, 'width',       { get: () => 1366 });
Object.defineProperty(screen, 'height',      { get: () => 900  });
Object.defineProperty(screen, 'colorDepth',  { get: () => 24   });
Object.defineProperty(screen, 'pixelDepth',  { get: () => 24   });

// 11. Permissions API — headless returns 'denied' for notifications, real Chrome 'prompt'
(function() {
  if (!navigator.permissions) return;
  const origQuery = navigator.permissions.query.bind(navigator.permissions);
  navigator.permissions.query = (params) => {
    if (params && params.name === 'notifications') {
      return Promise.resolve({ state: 'prompt', onchange: null });
    }
    return origQuery(params);
  };
})();

// 12. Battery API stub — some bots check for it
if (!navigator.getBattery) {
  navigator.getBattery = () => Promise.resolve({
    charging: true, chargingTime: 0, dischargingTime: Infinity,
    level: 1.0, onchargingchange: null, onchargingtimechange: null,
    ondischargingtimechange: null, onlevelchange: null,
  });
}
"""


# ---------------------------------------------------------------------------
# Proxy helpers (unchanged logic, minor type hints added)
# ---------------------------------------------------------------------------

def _split_proxy_env_list(raw: str) -> list[str]:
    if not raw or not raw.strip():
        return []
    out: list[str] = []
    for line in raw.replace(";", "\n").split("\n"):
        for chunk in line.split(","):
            c = chunk.strip()
            if c:
                out.append(c)
    return out


def _proxy_pool() -> list[str]:
    raw = (os.environ.get("COSTCO_AU_PROXY_URLS") or "").strip()
    if raw:
        return _split_proxy_env_list(raw)

    one = (os.environ.get("COSTCO_AU_PROXY_URL") or "").strip()
    if one:
        return [one]

    raw_global = (os.environ.get("PROXY_URLS") or "").strip()
    if raw_global:
        return _split_proxy_env_list(raw_global)

    one_global = (os.environ.get("PROXY_URL") or "").strip()
    if one_global:
        return [one_global]

    endpoints = (os.environ.get("PROXY_ENDPOINTS") or "").strip()
    user = (os.environ.get("PROXY_USER") or "").strip()
    password = (os.environ.get("PROXY_PASS") or "").strip()
    scheme = (os.environ.get("PROXY_SCHEME") or "http").strip() or "http"
    if endpoints and user and password:
        built: list[str] = []
        for ep in _split_proxy_env_list(endpoints):
            host, _, port_s = ep.rpartition(":")
            host = host.strip()
            port_s = port_s.strip()
            if host and port_s.isdigit():
                built.append(f"{scheme}://{user}:{password}@{host}:{port_s}")
        return built

    return []


def _pick_proxy(proxies_pool: list[str], attempt: int, blocked_proxies: set[str]) -> Optional[str]:
    if not proxies_pool:
        return None
    size = len(proxies_pool)
    for i in range(size):
        candidate = _normalize_proxy_url(proxies_pool[(attempt + i) % size])
        if not candidate:
            continue
        if candidate in blocked_proxies and size > 1:
            continue
        return candidate
    return _normalize_proxy_url(proxies_pool[attempt % size])


def _normalize_proxy_url(proxy_url: str) -> Optional[str]:
    p = (proxy_url or "").strip()
    if not p:
        return None
    if "://" not in p:
        p = f"http://{p}"
    try:
        parsed = urlparse(p)
        if not parsed.hostname or not parsed.port:
            return None
        return p
    except Exception:
        return None


def _playwright_proxy_from_url(proxy_url: str) -> Optional[dict]:
    normalized = _normalize_proxy_url(proxy_url)
    if not normalized:
        return None
    parsed = urlparse(normalized)
    if not parsed.hostname or not parsed.port:
        return None
    server = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
    proxy: dict = {"server": server}
    if parsed.username:
        proxy["username"] = parsed.username
    if parsed.password:
        proxy["password"] = parsed.password
    return proxy


def _chromium_executable_path() -> Optional[str]:
    for path in CHROMIUM_CANDIDATES:
        if path and os.path.exists(path):
            return path
    return None


# ---------------------------------------------------------------------------
# Extraction helpers (unchanged)
# ---------------------------------------------------------------------------

def _extract_title(soup: BeautifulSoup) -> Optional[str]:
    for sel in ("h1", "meta[property='og:title']", "meta[name='twitter:title']"):
        el = soup.select_one(sel)
        if not el:
            continue
        text = (el.get("content") or "").strip() if el.name == "meta" else el.get_text(" ", strip=True)
        if text and len(text) > 2:
            return text[:500]
    return None


def _extract_price(soup: BeautifulSoup, html: str) -> Optional[float]:
    for sel in (
        "meta[property='product:price:amount']",
        "meta[itemprop='price']",
        "[itemprop='price']",
        "[data-testid*='price']",
        "span[class*='price' i]",
    ):
        el = soup.select_one(sel)
        if not el:
            continue
        raw = (el.get("content") or "").strip() if el.name == "meta" else el.get_text(" ", strip=True)
        p = parse_price_text(raw)
        if p is not None:
            return p

    for script in soup.select("script[type='application/ld+json']"):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue

        def walk(node):
            if isinstance(node, dict):
                if "price" in node:
                    p = parse_price_text(str(node.get("price")))
                    if p is not None:
                        return p
                for v in node.values():
                    got = walk(v)
                    if got is not None:
                        return got
            elif isinstance(node, list):
                for v in node:
                    got = walk(v)
                    if got is not None:
                        return got
            return None

        parsed = walk(data)
        if parsed is not None:
            return parsed

    for pat in (
        r'"product:price:amount"\s*content\s*=\s*"([^"]+)"',
        r'"price"\s*:\s*"?\$?(\d+(?:\.\d{1,2})?)',
        r'"finalPrice"\s*:\s*"?\$?(\d+(?:\.\d{1,2})?)',
    ):
        m = re.search(pat, html, re.IGNORECASE)
        if not m:
            continue
        p = parse_price_text(m.group(1))
        if p is not None:
            return p
    return None


def _extract_stock(soup: BeautifulSoup) -> int:
    for sel in (
        "button[data-testid*='add' i]",
        "a[data-testid*='add' i]",
        "[role='button'][data-testid*='add' i]",
        "button.add-to-cart",
        "a.add-to-cart",
        "button.btn-block",
        "button.notranslate",
        "button",
    ):
        for el in soup.select(sel):
            btn_text = (el.get_text(" ", strip=True) or "").lower()
            if "add to cart" not in btn_text:
                continue
            disabled = el.has_attr("disabled")
            aria_disabled = (el.get("aria-disabled") or "").lower() == "true"
            cls = " ".join(el.get("class") or []).lower()
            class_disabled = "disabled" in cls
            if not (disabled or aria_disabled or class_disabled):
                return 3
    return 0


def _is_blocked(html: str) -> tuple[bool, str]:
    blocked, reason = detect_block(html)
    if blocked:
        low = (html or "").lower()
        if "captcha" in low or "verify you are human" in low or "are you a robot" in low:
            return True, "captcha"
        return True, "blocked"

    lower = (html or "").lower()
    for needle in (
        "access denied",
        "pardon our interruption",
        "request unsuccessful",
        "captcha",
        "verify you are human",
        "are you a robot",
        "cf-challenge",
        "challenge-platform",
        "distil_r_captcha",
        "perimeterx",
        "px-captcha",
        "human verification",
    ):
        if needle in lower:
            return True, "captcha" if "captcha" in needle else "blocked"
    return False, ""


# ---------------------------------------------------------------------------
# Human-like interaction helpers
# ---------------------------------------------------------------------------

def _human_curve_move(page, x1: float, y1: float, x2: float, y2: float, steps: int = 12) -> None:
    """Move mouse along a slight bezier curve to mimic human movement."""
    try:
        cx = (x1 + x2) / 2 + random.uniform(-40, 40)
        cy = (y1 + y2) / 2 + random.uniform(-40, 40)
        for i in range(1, steps + 1):
            t = i / steps
            # Quadratic bezier
            bx = (1 - t) ** 2 * x1 + 2 * (1 - t) * t * cx + t ** 2 * x2
            by = (1 - t) ** 2 * y1 + 2 * (1 - t) * t * cy + t ** 2 * y2
            page.mouse.move(bx, by)
            time.sleep(random.uniform(0.008, 0.025))
    except Exception:
        pass


def _human_scroll(page) -> None:
    """Scroll down in uneven increments like a human scanning a page."""
    try:
        total_height = page.evaluate("() => document.body.scrollHeight")
        if not total_height or total_height < 200:
            return
        target = int(total_height * random.uniform(0.30, 0.55))
        scrolled = 0
        while scrolled < target:
            step = random.randint(80, 260)
            scrolled = min(scrolled + step, target)
            page.evaluate(f"window.scrollTo(0, {scrolled})")
            time.sleep(random.uniform(0.05, 0.18))
    except Exception:
        pass


def _simulate_human_presence(page) -> None:
    """Run a short sequence of human-like interactions after page load."""
    try:
        # Randomised starting mouse position (top-left quadrant of viewport)
        start_x = random.uniform(80, 400)
        start_y = random.uniform(60, 300)
        page.mouse.move(start_x, start_y)
        time.sleep(random.uniform(0.2, 0.5))

        # Move toward a plausible product area
        _human_curve_move(page, start_x, start_y,
                          random.uniform(300, 900), random.uniform(300, 600))
        time.sleep(random.uniform(0.1, 0.3))

        _human_scroll(page)
        time.sleep(random.uniform(0.3, 0.7))

        # Small random mouse wiggle
        cx, cy = random.uniform(300, 800), random.uniform(200, 500)
        _human_curve_move(page, cx, cy,
                          cx + random.uniform(-30, 30), cy + random.uniform(-20, 20),
                          steps=6)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Page-ready wait
# ---------------------------------------------------------------------------

def _page_ready(page) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT_MS)
    except Exception:
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:
        pass
    # Variable post-load pause — avoids the fixed 2500 ms bot fingerprint
    time.sleep(random.uniform(1.5, 3.5))


# ---------------------------------------------------------------------------
# Browser fetch — one launch per attempt, no persistent session
# ---------------------------------------------------------------------------

def _build_launch_args() -> list[str]:
    """
    Chrome launch flags that reduce headless detection.
    Key additions vs original:
      - --disable-features=IsolateOrigins removes a common headless tell.
      - --window-size keeps screen geometry consistent with viewport.
      - Removed --disable-gpu (can cause rendering differences detectable by canvas fingerprinting).
    """
    return [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-features=IsolateOrigins,site-per-process",
        "--window-size=1366,900",
        "--start-maximized",
        "--disable-extensions-except=",
        "--disable-component-extensions-with-background-pages",
        "--disable-default-apps",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        "--disable-background-networking",
        "--disable-client-side-phishing-detection",
        "--disable-hang-monitor",
        "--disable-popup-blocking",
        "--metrics-recording-only",
        "--safebrowsing-disable-auto-update",
        "--password-store=basic",
        "--use-mock-keychain",
    ]


def _fetch_html_with_browser(
    vendor_url: str,
    proxy_url: Optional[str],
    attempt: int,
) -> tuple[Optional[str], Optional[str]]:
    """
    Launches a headless Chromium, applies full stealth patches, navigates to
    vendor_url and returns (html, final_url).

    Key improvements:
    1. Stealth script is registered as an init_script on the context, so it
       runs before ANY page script — not after.
    2. Human-like mouse + scroll after load.
    3. Two-phase navigation with fallback.
    4. Per-session random viewport jitter so every launch has a unique
       screen geometry (within a plausible range).
    """
    user_data_dir = tempfile.mkdtemp(prefix="costco_au_pw_")
    pw = None
    browser = None
    context = None
    page = None

    # Slight viewport jitter per session
    vp_w = 1366 + random.randint(-20, 40)
    vp_h = 900 + random.randint(-10, 30)

    try:
        pw = sync_playwright().start()

        launch_kwargs: dict = {
            "headless": True,
            "args": _build_launch_args(),
        }

        executable_path = _chromium_executable_path()
        if executable_path:
            launch_kwargs["executable_path"] = executable_path

        proxy = _playwright_proxy_from_url(proxy_url) if proxy_url else None
        if proxy:
            launch_kwargs["proxy"] = proxy

        browser = pw.chromium.launch(**launch_kwargs)

        context = browser.new_context(
            user_agent=USER_AGENTS[attempt % len(USER_AGENTS)],
            locale="en-AU",
            timezone_id="Australia/Sydney",
            viewport={"width": vp_w, "height": vp_h},
            screen={"width": vp_w, "height": vp_h},
            device_scale_factor=1,
            is_mobile=False,
            has_touch=False,
            java_script_enabled=True,
            accept_downloads=False,
            extra_http_headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-AU,en-US;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Cache-Control": "max-age=0",
            },
            ignore_https_errors=True,
        )

        # *** Critical fix: register stealth BEFORE any page is opened ***
        context.add_init_script(_STEALTH_SCRIPT)

        page = context.new_page()
        page.set_default_navigation_timeout(PAGE_TIMEOUT_MS)
        page.set_default_timeout(PAGE_TIMEOUT_MS)

        # Phase 1: navigate
        try:
            page.goto(vendor_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            # Phase 2: lighter wait — take whatever loaded
            try:
                page.goto(vendor_url, wait_until="commit", timeout=20_000)
            except PlaywrightTimeoutError:
                raise

        _page_ready(page)

        html = page.content()
        final_url = page.url

        # Only do human simulation if the page looks real
        if html and not any(k in html.lower() for k in ("captcha", "challenge", "blocked")):
            _simulate_human_presence(page)
            time.sleep(random.uniform(0.4, 0.9))
            # Re-capture after interaction in case lazy content loaded
            try:
                html = page.content()
            except Exception:
                pass

        return html, final_url

    finally:
        for obj, method in [(page, "close"), (context, "close"), (browser, "close"), (pw, "stop")]:
            if obj:
                try:
                    getattr(obj, method)()
                except Exception:
                    pass
        shutil.rmtree(user_data_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scrape_costco_au(vendor_url: str, region: str, session: dict = None) -> dict:
    last = None
    proxies_pool = _proxy_pool()
    blocked_proxies: set[str] = set()

    if proxies_pool:
        logger.info("Costco AU proxy pool configured (size=%d)", len(proxies_pool))
    else:
        logger.warning("Costco AU: no proxies configured — direct IP requests likely to be blocked")

    for attempt in range(RETRY_LIMIT + 1):
        if attempt:
            random_delay(*RETRY_BACKOFF_SEC)

        proxy_url = _pick_proxy(proxies_pool, attempt, blocked_proxies)

        try:
            html, final_url = _fetch_html_with_browser(vendor_url, proxy_url, attempt)
        except PlaywrightTimeoutError:
            if proxy_url:
                blocked_proxies.add(proxy_url)
            logger.warning("Costco AU timeout attempt=%d url=%s proxy=%s", attempt, vendor_url, proxy_url or "none")
            last = ScrapeResult.fail("timeout", "Costco AU browser timeout", "", "costco_au", vendor_url)
            continue
        except Exception as exc:
            err_str = str(exc)
            # ERR_TUNNEL_CONNECTION_FAILED → proxy is dead, rotate immediately
            if "ERR_TUNNEL_CONNECTION_FAILED" in err_str or "TUNNEL" in err_str.upper():
                if proxy_url:
                    blocked_proxies.add(proxy_url)
                    logger.warning(
                        "Costco AU proxy tunnel failure, rotating. attempt=%d proxy=%s", attempt, proxy_url
                    )
                last = ScrapeResult.fail("proxy_tunnel_error", err_str, "", "costco_au", vendor_url)
                # Do NOT sleep — the proxy is dead, retry immediately with next one
                continue
            logger.warning("Costco AU browser error attempt=%d url=%s err=%s", attempt, vendor_url, exc)
            last = ScrapeResult.fail("browser_error", err_str, "", "costco_au", vendor_url)
            continue

        html = html or ""
        blocked, reason = _is_blocked(html)
        if blocked:
            if proxy_url:
                blocked_proxies.add(proxy_url)
            logger.warning(
                "Costco AU blocked reason=%s attempt=%d url=%s final=%s proxy=%s",
                reason, attempt, vendor_url, final_url or "", "yes" if proxy_url else "no",
            )
            last = ScrapeResult.fail(
                f"blocked_{reason}", f"Blocked: {reason}", html, "costco_au", final_url or vendor_url
            )
            continue

        soup = BeautifulSoup(html, "lxml")
        title = _extract_title(soup)
        price = _extract_price(soup, html)
        stock = _extract_stock(soup)

        if price is None:
            logger.debug("Costco AU no_price attempt=%d url=%s", attempt, vendor_url)
            last = ScrapeResult.fail(
                "no_price", "Price not found on Costco AU page", html, "costco_au", final_url or vendor_url
            )
            continue

        return ScrapeResult.ok(price=price, stock=stock, title=title).to_legacy()

    return (
        last or ScrapeResult.fail("max_retries", "Costco AU retries exhausted", "", "costco_au", vendor_url)
    ).to_legacy()


def close_costco_au_session(session):
    # No persistent session kept for browser mode.
    return