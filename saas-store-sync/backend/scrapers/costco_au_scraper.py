"""
Costco AU product scraper — Cloudflare Bot Management bypass edition.

Strategy
--------
Costco AU runs Cloudflare Bot Management, which operates at three layers:

  Layer 1 — TLS/JA3 fingerprint  (happens before any JS)
  Layer 2 — HTTP/2 fingerprint    (SETTINGS frame order, HPACK, etc.)
  Layer 3 — JS challenge          (headless detection, canvas, WebGL …)

Playwright's bundled Chromium fails Layers 1 & 2 because its TLS stack
produces a known-bot JA3/JA4 hash regardless of JS stealth patches.

Fix: use `camoufox` (Firefox-based, ships patched NSS for TLS mimicry) when
available, and fall back to Playwright Chromium with all stealth patches when
camoufox is not installed. The fallback still works with high-quality
residential/ISP proxies because CF Bot Management uses proxy reputation as a
secondary signal — if the IP is clean, a partial fingerprint mismatch is
tolerated.

Proxy requirements
------------------
Datacenter IPs (Webshare, etc.) are pre-blocked by CF Bot Management.
Use residential or ISP (static residential) proxies with AU geo-targeting.
Recommended providers:  Oxylabs Residential, Bright Data Residential,
                        IPRoyal Residential, Smartproxy Residential.
Set COSTCO_AU_PROXY_URLS (comma-separated) or COSTCO_AU_PROXY_URL.

Rate limiting
-------------
Multiple Celery workers firing simultaneously share the same proxy IPs and
burn through CF's per-IP request budget quickly.  Set COSTCO_AU_CONCURRENCY=1
in your .env to serialise Costco AU scrapes, or use separate proxy pools per
worker via COSTCO_AU_PROXY_URLS.

Public API (unchanged)
----------------------
  scrape_costco_au(vendor_url, region, session=None)
      -> {"price": float|None, "stock": int|None, "title": str|None}
  close_costco_au_session(session)
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import shutil
import tempfile
import time
import uuid
from typing import Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)
import redis

from .core import ScrapeResult, detect_block, parse_price_text, random_delay

logger = logging.getLogger("scrapers.costco_au")

RETRY_LIMIT = 3
PAGE_TIMEOUT_MS = 55_000
RETRY_BACKOFF_SEC = (4.0, 9.0)
DEFAULT_MIN_REQUEST_GAP_SEC = 8.0
_LAST_COSTCO_REQUEST_AT = 0.0
COSTCO_LOCK_KEY = "scrapers:costco_au:global_lock"
COSTCO_LOCK_TTL_SEC = 120
COSTCO_LOCK_WAIT_SEC = 40

# ---------------------------------------------------------------------------
# camoufox availability check
# ---------------------------------------------------------------------------
try:
    from camoufox.sync_api import Camoufox  # type: ignore
    _CAMOUFOX_AVAILABLE = True
    logger.info("camoufox available — will use Firefox TLS fingerprint for Costco AU")
except ImportError:
    _CAMOUFOX_AVAILABLE = False
    logger.warning(
        "camoufox not installed — falling back to Chromium stealth mode. "
        "Install with: pip install camoufox[geoip] && python -m camoufox fetch"
    )

# ---------------------------------------------------------------------------
# User-agents
# ---------------------------------------------------------------------------
_UA_CHROME = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

_UA_FIREFOX = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
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
# Full stealth init script (Chromium fallback only)
# ---------------------------------------------------------------------------
_STEALTH_SCRIPT = r"""
Object.defineProperty(navigator, 'webdriver', { get: () => undefined, configurable: true });

(function () {
  const plugins = [
    { name: 'Chrome PDF Plugin',  filename: 'internal-pdf-viewer',             description: 'Portable Document Format' },
    { name: 'Chrome PDF Viewer',  filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
    { name: 'Native Client',      filename: 'internal-nacl-plugin',             description: '' },
  ];
  const pa = Object.create(PluginArray.prototype);
  plugins.forEach((p, i) => {
    const pl = Object.create(Plugin.prototype);
    ['name','filename','description'].forEach(k => Object.defineProperty(pl, k, { get: () => p[k] }));
    Object.defineProperty(pl, 'length', { get: () => 0 });
    Object.defineProperty(pa, i, { get: () => pl });
  });
  Object.defineProperty(pa, 'length', { get: () => plugins.length });
  pa.item = i => pa[i]; pa.namedItem = n => plugins.find(p=>p.name===n)||null;
  pa[Symbol.iterator] = function*() { for(let i=0;i<plugins.length;i++) yield this[i]; };
  Object.defineProperty(navigator, 'plugins',  { get: () => pa, configurable: true });
})();

Object.defineProperty(navigator, 'platform',             { get: () => 'Win32',             configurable: true });
Object.defineProperty(navigator, 'language',             { get: () => 'en-AU',             configurable: true });
Object.defineProperty(navigator, 'languages',            { get: () => ['en-AU','en-US','en'], configurable: true });
Object.defineProperty(navigator, 'hardwareConcurrency',  { get: () => 8,                   configurable: true });
try { Object.defineProperty(navigator, 'deviceMemory',   { get: () => 8,                   configurable: true }); } catch(_){}

if (!window.chrome) {
  window.chrome = {
    app: { InstallState:'x', RunningState:'x', getDetails:()=>{}, getIsInstalled:()=>{} },
    csi: ()=>{}, loadTimes: ()=>{},
    runtime: { connect:()=>{}, sendMessage:()=>{} },
  };
}

try { Object.defineProperty(Notification, 'permission', { get: () => 'default' }); } catch(_){}

(function(){
  const orig = HTMLCanvasElement.prototype.toDataURL;
  HTMLCanvasElement.prototype.toDataURL = function(t){
    const ctx = this.getContext('2d');
    if(ctx){ const d=ctx.getImageData(0,0,1,1); d.data[0]^=(Math.random()*3|0); ctx.putImageData(d,0,0); }
    return orig.apply(this, arguments);
  };
})();

(function(){
  const orig = WebGLRenderingContext.prototype.getParameter;
  WebGLRenderingContext.prototype.getParameter = function(p){
    if(p===37445) return 'Google Inc. (Intel)';
    if(p===37446) return 'ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)';
    return orig.call(this,p);
  };
  try {
    const orig2 = WebGL2RenderingContext.prototype.getParameter;
    WebGL2RenderingContext.prototype.getParameter = function(p){
      if(p===37445) return 'Google Inc. (Intel)';
      if(p===37446) return 'ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)';
      return orig2.call(this,p);
    };
  } catch(_){}
})();

(function(){
  if(!navigator.permissions) return;
  const oq = navigator.permissions.query.bind(navigator.permissions);
  navigator.permissions.query = p => p&&p.name==='notifications'
    ? Promise.resolve({state:'prompt',onchange:null})
    : oq(p);
})();

if(!navigator.getBattery){
  navigator.getBattery = ()=>Promise.resolve({charging:true,chargingTime:0,dischargingTime:Infinity,level:1});
}
"""

# ---------------------------------------------------------------------------
# Proxy helpers
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


def _min_request_gap_sec() -> float:
    raw = (os.environ.get("COSTCO_AU_MIN_REQUEST_GAP_SEC") or "").strip()
    if not raw:
        return DEFAULT_MIN_REQUEST_GAP_SEC
    try:
        return max(0.0, float(raw))
    except Exception:
        return DEFAULT_MIN_REQUEST_GAP_SEC


def _respect_min_request_gap() -> None:
    global _LAST_COSTCO_REQUEST_AT
    gap = _min_request_gap_sec()
    if gap <= 0:
        _LAST_COSTCO_REQUEST_AT = time.monotonic()
        return
    now = time.monotonic()
    elapsed = now - _LAST_COSTCO_REQUEST_AT if _LAST_COSTCO_REQUEST_AT else gap
    if elapsed < gap:
        time.sleep(gap - elapsed)
    _LAST_COSTCO_REQUEST_AT = time.monotonic()


def _redis_client() -> Optional[redis.Redis]:
    redis_url = (os.environ.get("REDIS_URL") or "").strip()
    if not redis_url:
        return None
    try:
        return redis.Redis.from_url(redis_url, decode_responses=True, socket_timeout=5)
    except Exception:
        return None


def _costco_lock_wait_sec() -> int:
    raw = (os.environ.get("COSTCO_AU_LOCK_WAIT_SEC") or "").strip()
    if not raw:
        return COSTCO_LOCK_WAIT_SEC
    try:
        return max(1, int(float(raw)))
    except Exception:
        return COSTCO_LOCK_WAIT_SEC


def _acquire_costco_lock() -> tuple[Optional[redis.Redis], Optional[str], bool]:
    """
    Cross-worker lock so only one Costco scrape runs at once.
    Returns (client, token, acquired).
    """
    client = _redis_client()
    if not client:
        return None, None, True

    token = uuid.uuid4().hex
    deadline = time.monotonic() + _costco_lock_wait_sec()
    while time.monotonic() < deadline:
        try:
            ok = client.set(COSTCO_LOCK_KEY, token, nx=True, ex=COSTCO_LOCK_TTL_SEC)
            if ok:
                return client, token, True
        except Exception:
            # If Redis has transient issues, fail open to avoid full scraper outage.
            return None, None, True
        time.sleep(0.35)
    return client, token, False


def _release_costco_lock(client: Optional[redis.Redis], token: Optional[str]) -> None:
    if not client or not token:
        return
    try:
        client.eval(
            """
            if redis.call("get", KEYS[1]) == ARGV[1] then
                return redis.call("del", KEYS[1])
            else
                return 0
            end
            """,
            1,
            COSTCO_LOCK_KEY,
            token,
        )
    except Exception:
        pass


def _proxy_pool() -> list[str]:
    for env_key in ("COSTCO_AU_PROXY_URLS", "PROXY_URLS"):
        raw = (os.environ.get(env_key) or "").strip()
        if raw:
            return _split_proxy_env_list(raw)

    for env_key in ("COSTCO_AU_PROXY_URL", "PROXY_URL"):
        one = (os.environ.get(env_key) or "").strip()
        if one:
            return [one]

    endpoints = (os.environ.get("PROXY_ENDPOINTS") or "").strip()
    user = (os.environ.get("PROXY_USER") or "").strip()
    password = (os.environ.get("PROXY_PASS") or "").strip()
    scheme = (os.environ.get("PROXY_SCHEME") or "http").strip() or "http"
    if endpoints and user and password:
        built: list[str] = []
        for ep in _split_proxy_env_list(endpoints):
            host, _, port_s = ep.rpartition(":")
            host, port_s = host.strip(), port_s.strip()
            if host and port_s.isdigit():
                built.append(f"{scheme}://{user}:{password}@{host}:{port_s}")
        return built

    return []


def _pick_proxy(pool: list[str], attempt: int, blocked: set[str]) -> Optional[str]:
    if not pool:
        return None
    size = len(pool)
    for i in range(size):
        c = _normalize_proxy_url(pool[(attempt + i) % size])
        if c and (c not in blocked or size == 1):
            return c
    return _normalize_proxy_url(pool[attempt % size])


def _normalize_proxy_url(proxy_url: str) -> Optional[str]:
    p = (proxy_url or "").strip()
    if not p:
        return None
    if "://" not in p:
        p = f"http://{p}"
    try:
        parsed = urlparse(p)
        return p if (parsed.hostname and parsed.port) else None
    except Exception:
        return None


def _playwright_proxy_dict(proxy_url: str) -> Optional[dict]:
    n = _normalize_proxy_url(proxy_url)
    if not n:
        return None
    parsed = urlparse(n)
    if not parsed.hostname or not parsed.port:
        return None
    d: dict = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
    if parsed.username:
        d["username"] = parsed.username
    if parsed.password:
        d["password"] = parsed.password
    return d


# ---------------------------------------------------------------------------
# Extraction helpers
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
                    p = parse_price_text(str(node["price"]))
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
        if m:
            p = parse_price_text(m.group(1))
            if p is not None:
                return p
    return None


def _extract_stock(soup: BeautifulSoup) -> int:
    for sel in (
        "button[data-testid*='add' i]", "a[data-testid*='add' i]",
        "[role='button'][data-testid*='add' i]",
        "button.add-to-cart", "a.add-to-cart",
        "button.btn-block", "button.notranslate", "button",
    ):
        for el in soup.select(sel):
            if "add to cart" not in (el.get_text(" ", strip=True) or "").lower():
                continue
            disabled = el.has_attr("disabled")
            aria_dis = (el.get("aria-disabled") or "").lower() == "true"
            cls_dis  = "disabled" in " ".join(el.get("class") or []).lower()
            if not (disabled or aria_dis or cls_dis):
                return 3
    return 0


def _is_blocked(html: str) -> tuple[bool, str]:
    blocked, reason = detect_block(html)
    if blocked:
        low = (html or "").lower()
        if any(k in low for k in ("captcha", "verify you are human", "are you a robot")):
            return True, "captcha"
        return True, "blocked"

    lower = (html or "").lower()
    signals = {
        "captcha": ["captcha", "verify you are human", "are you a robot", "distil_r_captcha", "px-captcha"],
        "blocked": [
            "access denied", "pardon our interruption", "request unsuccessful",
            "cf-challenge", "challenge-platform", "perimeterx", "human verification",
            "just a moment", "checking your browser",
        ],
    }
    for label, needles in signals.items():
        if any(n in lower for n in needles):
            return True, label
    return False, ""


# ---------------------------------------------------------------------------
# Human-like interaction
# ---------------------------------------------------------------------------

def _bezier_move(page, x1: float, y1: float, x2: float, y2: float, steps: int = 14) -> None:
    try:
        cx = (x1 + x2) / 2 + random.uniform(-50, 50)
        cy = (y1 + y2) / 2 + random.uniform(-50, 50)
        for i in range(1, steps + 1):
            t = i / steps
            bx = (1 - t) ** 2 * x1 + 2 * (1 - t) * t * cx + t ** 2 * x2
            by = (1 - t) ** 2 * y1 + 2 * (1 - t) * t * cy + t ** 2 * y2
            page.mouse.move(bx, by)
            time.sleep(random.uniform(0.006, 0.022))
    except Exception:
        pass


def _human_scroll(page) -> None:
    try:
        total = page.evaluate("() => document.body.scrollHeight") or 0
        if total < 200:
            return
        target = int(total * random.uniform(0.25, 0.50))
        pos = 0
        while pos < target:
            pos = min(pos + random.randint(60, 220), target)
            page.evaluate(f"window.scrollTo(0, {pos})")
            time.sleep(random.uniform(0.04, 0.15))
    except Exception:
        pass


def _simulate_human(page) -> None:
    try:
        sx, sy = random.uniform(80, 400), random.uniform(60, 250)
        page.mouse.move(sx, sy)
        time.sleep(random.uniform(0.15, 0.40))
        _bezier_move(page, sx, sy, random.uniform(300, 850), random.uniform(280, 550))
        time.sleep(random.uniform(0.10, 0.25))
        _human_scroll(page)
        time.sleep(random.uniform(0.25, 0.60))
    except Exception:
        pass


def _wait_for_page(page) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT_MS)
    except Exception:
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:
        pass
    time.sleep(random.uniform(1.8, 3.8))


# ---------------------------------------------------------------------------
# camoufox fetch (Firefox + patched TLS — best bypass for CF Bot Management)
# ---------------------------------------------------------------------------

def _fetch_with_camoufox(
    vendor_url: str, proxy_url: Optional[str], attempt: int
) -> tuple[Optional[str], Optional[str]]:
    """
    Uses camoufox (https://github.com/daijro/camoufox) which ships a patched
    Firefox build that spoofs its TLS JA3/JA4 fingerprint and HTTP/2 frame
    order to match real browser traffic.  This beats CF Bot Management at
    Layer 1 and Layer 2.
    """
    proxy_kwargs: dict = {}
    if proxy_url:
        proxy_kwargs["proxy"] = proxy_url  # camoufox accepts plain proxy URL

    with Camoufox(
        headless=True,
        humanize=True,   # built-in human-like timing / mouse
        geoip=True,      # auto geo-IP locale matching
        os="windows",
        **proxy_kwargs,
    ) as browser:
        page = browser.new_page()
        page.set_default_navigation_timeout(PAGE_TIMEOUT_MS)
        try:
            page.goto(vendor_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            page.goto(vendor_url, wait_until="commit", timeout=20_000)

        _wait_for_page(page)
        html = page.content()
        final_url = page.url

        low = (html or "").lower()
        if html and not any(k in low for k in ("captcha", "challenge", "just a moment")):
            _simulate_human(page)
            time.sleep(random.uniform(0.4, 1.0))
            try:
                html = page.content()
            except Exception:
                pass

        return html, final_url


# ---------------------------------------------------------------------------
# Playwright Chromium fetch (fallback — needs residential proxies to work)
# ---------------------------------------------------------------------------

def _chromium_path() -> Optional[str]:
    for p in CHROMIUM_CANDIDATES:
        if p and os.path.exists(p):
            return p
    return None


def _chromium_launch_args() -> list[str]:
    return [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-features=IsolateOrigins,site-per-process",
        "--window-size=1366,900",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        "--disable-background-networking",
        "--disable-hang-monitor",
        "--password-store=basic",
        "--use-mock-keychain",
        "--metrics-recording-only",
    ]


def _fetch_with_chromium(
    vendor_url: str, proxy_url: Optional[str], attempt: int
) -> tuple[Optional[str], Optional[str]]:
    """
    Playwright Chromium with JS stealth patches.
    Works when a high-quality residential/ISP proxy with a clean IP reputation
    is provided.  Will fail CF Bot Management on datacenter IPs.
    """
    tmp_dir = tempfile.mkdtemp(prefix="costco_au_cr_")
    pw = browser = context = page = None
    vp_w = 1366 + random.randint(-20, 40)
    vp_h = 900  + random.randint(-10, 30)

    try:
        pw = sync_playwright().start()
        launch_kw: dict = {"headless": True, "args": _chromium_launch_args()}
        exe = _chromium_path()
        if exe:
            launch_kw["executable_path"] = exe
        proxy = _playwright_proxy_dict(proxy_url) if proxy_url else None
        if proxy:
            launch_kw["proxy"] = proxy

        browser = pw.chromium.launch(**launch_kw)
        context = browser.new_context(
            user_agent=_UA_CHROME[attempt % len(_UA_CHROME)],
            locale="en-AU",
            timezone_id="Australia/Sydney",
            viewport={"width": vp_w, "height": vp_h},
            screen={"width": vp_w, "height": vp_h},
            device_scale_factor=1,
            is_mobile=False,
            has_touch=False,
            ignore_https_errors=True,
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
        )
        # Register BEFORE any page opens so it runs ahead of page scripts
        context.add_init_script(_STEALTH_SCRIPT)

        page = context.new_page()
        page.set_default_navigation_timeout(PAGE_TIMEOUT_MS)
        page.set_default_timeout(PAGE_TIMEOUT_MS)

        try:
            page.goto(vendor_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            page.goto(vendor_url, wait_until="commit", timeout=20_000)

        _wait_for_page(page)
        html = page.content()
        final_url = page.url

        low = (html or "").lower()
        if html and not any(k in low for k in ("captcha", "challenge", "just a moment")):
            _simulate_human(page)
            time.sleep(random.uniform(0.3, 0.8))
            try:
                html = page.content()
            except Exception:
                pass

        return html, final_url

    finally:
        for obj, m in [(page, "close"), (context, "close"), (browser, "close"), (pw, "stop")]:
            if obj:
                try:
                    getattr(obj, m)()
                except Exception:
                    pass
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Unified fetch — tries camoufox first, falls back to Chromium
# ---------------------------------------------------------------------------

def _fetch_html(
    vendor_url: str, proxy_url: Optional[str], attempt: int
) -> tuple[Optional[str], Optional[str]]:
    if _CAMOUFOX_AVAILABLE:
        try:
            return _fetch_with_camoufox(vendor_url, proxy_url, attempt)
        except Exception as exc:
            logger.warning("camoufox fetch error, falling back to Chromium: %s", exc)
    return _fetch_with_chromium(vendor_url, proxy_url, attempt)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scrape_costco_au(vendor_url: str, region: str, session: dict = None) -> dict:
    lock_client, lock_token, lock_ok = _acquire_costco_lock()
    if not lock_ok:
        logger.warning("Costco AU lock wait timeout; skipping scrape url=%s", vendor_url)
        return ScrapeResult.fail(
            "busy",
            "Costco AU scraper busy; try again shortly",
            "",
            "costco_au",
            vendor_url,
        ).to_legacy()

    _respect_min_request_gap()
    last = None
    proxies_pool = _proxy_pool()
    blocked_proxies: set[str] = set()

    if proxies_pool:
        logger.info(
            "Costco AU proxy pool size=%d engine=%s",
            len(proxies_pool),
            "camoufox" if _CAMOUFOX_AVAILABLE else "chromium-stealth",
        )
    else:
        logger.warning(
            "Costco AU: no proxies configured — direct IP will be blocked by Cloudflare Bot Management"
        )

    try:
        for attempt in range(RETRY_LIMIT + 1):
            if attempt:
                random_delay(*RETRY_BACKOFF_SEC)

            proxy_url = _pick_proxy(proxies_pool, attempt, blocked_proxies)

            try:
                html, final_url = _fetch_html(vendor_url, proxy_url, attempt)
            except PlaywrightTimeoutError:
                if proxy_url:
                    blocked_proxies.add(proxy_url)
                logger.warning("Costco AU timeout attempt=%d url=%s proxy=%s", attempt, vendor_url, proxy_url or "none")
                last = ScrapeResult.fail("timeout", "Costco AU browser timeout", "", "costco_au", vendor_url)
                continue
            except Exception as exc:
                err = str(exc)
                is_tunnel = any(k in err.upper() for k in ("ERR_TUNNEL_CONNECTION_FAILED", "TUNNEL", "ECONNREFUSED", "ECONNRESET"))
                if is_tunnel and proxy_url:
                    blocked_proxies.add(proxy_url)
                    logger.warning("Costco AU proxy dead, rotating immediately. attempt=%d proxy=%s", attempt, proxy_url)
                    last = ScrapeResult.fail("proxy_tunnel_error", err, "", "costco_au", vendor_url)
                    continue  # skip sleep — proxy is dead, rotate immediately
                logger.warning("Costco AU browser error attempt=%d url=%s err=%s", attempt, vendor_url, exc)
                last = ScrapeResult.fail("browser_error", err, "", "costco_au", vendor_url)
                continue

            html = html or ""
            blocked, reason = _is_blocked(html)
            if blocked:
                if proxy_url:
                    blocked_proxies.add(proxy_url)
                logger.warning(
                    "Costco AU blocked reason=%s attempt=%d url=%s final=%s engine=%s proxy=%s",
                    reason, attempt, vendor_url, final_url or "",
                    "camoufox" if _CAMOUFOX_AVAILABLE else "chromium",
                    "yes" if proxy_url else "no",
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
    finally:
        _release_costco_lock(lock_client, lock_token)


def close_costco_au_session(session) -> None:
    return