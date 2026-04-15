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
import os
import re
import shutil
import tempfile
from typing import Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from .core import ScrapeResult, detect_block, parse_price_text, random_delay

logger = logging.getLogger("scrapers.costco_au")

RETRY_LIMIT = 2
PAGE_TIMEOUT_MS = 45000
RETRY_BACKOFF_SEC = (2.0, 4.5)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
]

CHROMIUM_CANDIDATES = [
    os.environ.get("PLAYWRIGHT_CHROMIUM_PATH", "").strip(),
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    shutil.which("chromium"),
    shutil.which("chromium-browser"),
]


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

    html = html or ""
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
    """
    Costco AU does not expose a numeric quantity. Stock is 3 when an enabled
    Add to cart control is found, otherwise 0.
    """
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
        # Normalize vague reasons to stable labels used by monitoring.
        if reason in ("blocked", "waf_challenge"):
            low = (html or "").lower()
            if "captcha" in low or "verify you are human" in low or "are you a robot" in low:
                return True, "captcha"
            return True, "blocked"
        return True, reason

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
    ):
        if needle in lower:
            return True, "captcha" if "captcha" in needle else "blocked"
    return False, ""


def _page_ready(page) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT_MS)
    except Exception:
        pass

    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass

    try:
        page.wait_for_timeout(2500)
    except Exception:
        pass


def _apply_stealth_overrides(context) -> None:
    try:
        context.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
            Object.defineProperty(navigator, 'language', { get: () => 'en-AU' });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-AU', 'en-US', 'en'] });
            """
        )
    except Exception:
        pass


def _fetch_html_with_browser(vendor_url: str, proxy_url: Optional[str], attempt: int) -> tuple[Optional[str], Optional[str]]:
    user_data_dir = tempfile.mkdtemp(prefix="costco_au_pw_")
    pw = None
    browser = None
    context = None
    page = None

    try:
        pw = sync_playwright().start()
        chromium = pw.chromium

        launch_kwargs = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        }

        executable_path = _chromium_executable_path()
        if executable_path:
            launch_kwargs["executable_path"] = executable_path

        proxy = _playwright_proxy_from_url(proxy_url) if proxy_url else None
        if proxy:
            launch_kwargs["proxy"] = proxy

        browser = chromium.launch(**launch_kwargs)

        context = browser.new_context(
            user_agent=USER_AGENTS[attempt % len(USER_AGENTS)],
            locale="en-AU",
            timezone_id="Australia/Sydney",
            viewport={"width": 1366, "height": 900},
            extra_http_headers={
                "Accept-Language": "en-AU,en-US;q=0.9,en;q=0.8",
                "Upgrade-Insecure-Requests": "1",
            },
            ignore_https_errors=True,
        )
        _apply_stealth_overrides(context)

        page = context.new_page()
        page.set_default_navigation_timeout(PAGE_TIMEOUT_MS)
        page.set_default_timeout(PAGE_TIMEOUT_MS)

        try:
            page.goto(vendor_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            # Retry once with a lighter wait condition before bubbling up.
            page.goto(vendor_url, wait_until="commit", timeout=15000)
        _page_ready(page)

        html = page.content()
        final_url = page.url

        if html and "captcha" not in html.lower():
            try:
                if page.locator("button, a").count() > 0:
                    page.mouse.move(200, 250)
                    page.wait_for_timeout(400)
            except Exception:
                pass

            try:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.35)")
                page.wait_for_timeout(700)
                html = page.content()
            except Exception:
                pass

        return html, final_url

    finally:
        try:
            if page:
                page.close()
        except Exception:
            pass
        try:
            if context:
                context.close()
        except Exception:
            pass
        try:
            if browser:
                browser.close()
        except Exception:
            pass
        try:
            if pw:
                pw.stop()
        except Exception:
            pass
        shutil.rmtree(user_data_dir, ignore_errors=True)


def scrape_costco_au(vendor_url: str, region: str, session: dict = None) -> dict:
    last = None
    proxies_pool = _proxy_pool()
    blocked_proxies: set[str] = set()
    if proxies_pool:
        logger.info("Costco AU proxy pool configured (size=%d)", len(proxies_pool))

    for attempt in range(RETRY_LIMIT + 1):
        if attempt:
            random_delay(*RETRY_BACKOFF_SEC)

        proxy_url = _pick_proxy(proxies_pool, attempt, blocked_proxies)

        try:
            html, final_url = _fetch_html_with_browser(vendor_url, proxy_url, attempt)
        except PlaywrightTimeoutError:
            if proxy_url:
                blocked_proxies.add(proxy_url)
            last = ScrapeResult.fail("timeout", "Costco AU browser timeout", "", "costco_au", vendor_url)
            continue
        except Exception as exc:
            logger.warning("Costco AU browser error attempt %d url=%s err=%s", attempt, vendor_url, exc)
            last = ScrapeResult.fail("browser_error", str(exc), "", "costco_au", vendor_url)
            continue

        html = html or ""
        blocked, reason = _is_blocked(html)
        if blocked:
            if proxy_url:
                blocked_proxies.add(proxy_url)
            logger.warning(
                "Costco AU blocked (%s) attempt %d url=%s final=%s proxy=%s",
                reason,
                attempt,
                vendor_url,
                final_url or "",
                "yes" if proxy_url else "no",
            )
            last = ScrapeResult.fail(f"blocked_{reason}", f"Blocked: {reason}", html, "costco_au", final_url or vendor_url)
            continue

        soup = BeautifulSoup(html, "lxml")
        title = _extract_title(soup)
        price = _extract_price(soup, html)
        stock = _extract_stock(soup)

        if price is None:
            last = ScrapeResult.fail("no_price", "Price not found on Costco AU page", html, "costco_au", final_url or vendor_url)
            continue

        return ScrapeResult.ok(price=price, stock=stock, title=title).to_legacy()

    return (
        last or ScrapeResult.fail("max_retries", "Costco AU retries exhausted", "", "costco_au", vendor_url)
    ).to_legacy()


def close_costco_au_session(session):
    # No persistent session kept for browser mode.
    return

