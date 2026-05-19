"""
Fast eBay AU scraper: eager Selenium + pre-loaded cookies + short page wait.

Enabled when ``EBAY_AU_COOKIES_FILE`` or ``EBAY_AU_COOKIES_JSON`` is set (and
``EBAY_AU_FAST_SCRAPE`` is not ``0``). Falls back to the full ``ebay_common`` path
when cookies are missing, the page is blocked, or price cannot be parsed.

Cookie JSON format: same as browser export (list of objects with name/value/domain).
Never commit real cookies — mount a file on the AU worker only.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Optional

from bs4 import BeautifulSoup

from .ebay_common import (
    EBAY_MARKET_AU,
    EbayParser,
    _ebay_sk,
    _is_challenge_or_blocked,
    _looks_like_product_html,
    _normalize_url,
    _parse_ebay_display_price_text,
)

logger = logging.getLogger("scrapers.ebay_au_fast")

EBAY_AU_HOME = "https://www.ebay.com.au"

_DRIVER_KEY = _ebay_sk(EBAY_MARKET_AU, "fast_driver")
_COOKIES_LOADED_KEY = _ebay_sk(EBAY_MARKET_AU, "fast_cookies_loaded")
_AU_BLOCKED_KEY = _ebay_sk(EBAY_MARKET_AU, "blocked")
_AU_LAST_HTML_KEY = _ebay_sk(EBAY_MARKET_AU, "last_html")
_AU_PRODUCT_HTML_KEY = _ebay_sk(EBAY_MARKET_AU, "saw_product_html")

_QUANTITY_PATTERN = re.compile(
    r'"NumberValidation","minValue":"(\d+)","maxValue":"(\d+)"'
)

_SELECTORS = {
    "status_message": ".ux-layout-section__textual-display--statusMessage span",
    "price": "[data-testid='x-price-primary'] span",
    "seller_away": ".x-alert--ALERT_SA div.ux-message",
    "stock": "div.x-quantity__availability",
    "stock_fallback": "div.ux-message",
    "error_header": "p.error-header-v2__title",
}


def _trueish(val: str | None, default: bool = True) -> bool:
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def fast_scrape_enabled() -> bool:
    if not _trueish(os.environ.get("EBAY_AU_FAST_SCRAPE"), default=True):
        return False
    return bool((os.environ.get("EBAY_AU_COOKIES_FILE") or "").strip()) or bool(
        (os.environ.get("EBAY_AU_COOKIES_JSON") or "").strip()
    )


def _page_wait_seconds() -> float:
    raw = (os.environ.get("EBAY_AU_PAGE_WAIT_SEC") or "1.2").strip()
    try:
        return max(0.3, float(raw))
    except ValueError:
        return 1.2


def _page_load_timeout() -> int:
    raw = (os.environ.get("EBAY_AU_PAGE_LOAD_TIMEOUT_SEC") or "18").strip()
    try:
        return max(8, int(raw))
    except ValueError:
        return 18


def _load_cookie_list() -> list[dict[str, Any]]:
    inline = (os.environ.get("EBAY_AU_COOKIES_JSON") or "").strip()
    if inline:
        data = json.loads(inline)
        if isinstance(data, list):
            return data
        raise ValueError("EBAY_AU_COOKIES_JSON must be a JSON array")

    path = (os.environ.get("EBAY_AU_COOKIES_FILE") or "").strip()
    if not path:
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"{path}: cookie file must be a JSON array")
    return data


def cookies_configured() -> bool:
    """True when ``EBAY_AU_COOKIES_FILE`` or ``EBAY_AU_COOKIES_JSON`` is set."""
    return bool((os.environ.get("EBAY_AU_COOKIES_FILE") or "").strip()) or bool(
        (os.environ.get("EBAY_AU_COOKIES_JSON") or "").strip()
    )


def load_cookies_for_http() -> list[dict[str, Any]]:
    """Return the configured cookies (file or inline). Empty list on missing config."""
    try:
        return _load_cookie_list()
    except Exception as exc:
        logger.debug("eBay AU cookie load failed: %s", exc)
        return []


def _create_fast_driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    options = Options()
    options.page_load_strategy = "eager"
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=900,700")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--blink-settings=imagesEnabled=false")
    options.add_argument("--log-level=3")
    options.add_argument("--lang=en-AU,en")
    options.add_experimental_option("excludeSwitches", ["enable-logging", "enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    prefs = {
        "profile.managed_default_content_settings.images": 2,
        "profile.managed_default_content_settings.fonts": 2,
    }
    options.add_experimental_option("prefs", prefs)

    chrome_bin = os.environ.get("CHROME_BIN")
    if chrome_bin:
        options.binary_location = chrome_bin
    chromedriver_path = os.environ.get("CHROMEDRIVER_PATH")
    service = (
        Service(executable_path=chromedriver_path)
        if chromedriver_path and os.path.isfile(chromedriver_path)
        else Service()
    )

    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(_page_load_timeout())
    return driver


def _get_driver(session: dict | None):
    if session is not None and _DRIVER_KEY in session:
        driver = session[_DRIVER_KEY]
        try:
            _ = driver.title
            return driver
        except Exception:
            try:
                driver.quit()
            except Exception:
                pass
            session.pop(_DRIVER_KEY, None)
            session.pop(_COOKIES_LOADED_KEY, None)

    driver = _create_fast_driver()
    if session is not None:
        session[_DRIVER_KEY] = driver
    return driver


def inject_au_cookies_into_driver(
    driver,
    session: dict | None,
    *,
    loaded_key: str | None = None,
) -> None:
    """Load ``EBAY_AU_COOKIES_FILE`` / inline JSON into any Selenium driver (once per key).

    Uses a short page-load timeout for the throwaway ``ebay.com.au`` navigation so a
    slow/blocked home page can't burn the whole budget. add_cookie() does not require
    a full DOM, only that we're on the cookie domain.
    """
    if session is None:
        session = {}
    key = loaded_key or _COOKIES_LOADED_KEY
    if session.get(key):
        return

    cookies = load_cookies_for_http()
    if not cookies:
        return

    cookie_pageload_timeout = max(2, int(os.environ.get("EBAY_AU_COOKIE_HOME_TIMEOUT_SEC", "5") or 5))
    prev_timeout = None
    try:
        prev_timeout = driver.timeouts.page_load
    except Exception:
        pass
    try:
        driver.set_page_load_timeout(cookie_pageload_timeout)
    except Exception:
        pass

    try:
        driver.get(EBAY_AU_HOME)
    except Exception:
        try:
            driver.execute_script("window.stop();")
        except Exception:
            pass

    try:
        if prev_timeout is not None:
            driver.set_page_load_timeout(int(prev_timeout))
        else:
            driver.set_page_load_timeout(_page_load_timeout())
    except Exception:
        pass

    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        payload = dict(cookie)
        ss = payload.pop("sameSite", None)
        if ss in ("Strict", "Lax", "None"):
            payload["sameSite"] = ss
        try:
            driver.add_cookie(payload)
        except Exception:
            pass

    session[key] = True
    logger.info("eBay AU: injected %d file cookies into browser", len(cookies))


def _note_au_html(session: dict | None, html: str) -> None:
    if not session or not html:
        return
    session[_AU_LAST_HTML_KEY] = html
    if _looks_like_product_html(html):
        session[_AU_PRODUCT_HTML_KEY] = True
        session.pop(_AU_BLOCKED_KEY, None)
        return
    blocked, reason = _is_challenge_or_blocked(html)
    if blocked:
        session[_AU_BLOCKED_KEY] = reason


def _inject_cookies(driver, session: dict | None) -> None:
    inject_au_cookies_into_driver(driver, session, loaded_key=_COOKIES_LOADED_KEY)


def _clean_text(el) -> str:
    return el.get_text(" ", strip=True) if el else ""


def _wait_for_price_dom(driver, max_wait_sec: float) -> None:
    """Poll for any eBay price/title element rather than blindly sleeping.

    Returns as soon as the primary price selector renders. Caps at
    ``max_wait_sec`` and returns even on miss (parser may still find a fallback).
    """
    from selenium.webdriver.common.by import By

    deadline = time.monotonic() + max(0.4, max_wait_sec)
    selectors = (
        (By.CSS_SELECTOR, "[data-testid='x-price-primary'] span"),
        (By.CSS_SELECTOR, ".x-price-primary span"),
        (By.CSS_SELECTOR, "[data-testid='x-bin-price'] .ux-textspans"),
        (By.CSS_SELECTOR, ".x-buy-box__price-section .ux-textspans"),
        (By.CSS_SELECTOR, ".x-item-title__mainTitle"),
        (By.CSS_SELECTOR, "p.error-header-v2__title"),
    )
    while time.monotonic() < deadline:
        try:
            for by, sel in selectors:
                els = driver.find_elements(by, sel)
                for el in els:
                    txt = (el.text or "").strip()
                    if txt:
                        return
        except Exception:
            pass
        time.sleep(0.15)
    return


def _stock_from_text(text: str) -> Optional[int]:
    if not text:
        return None
    lower = text.lower()
    if any(x in lower for x in ("out of stock", "sold out", "unavailable")):
        return 0
    m = re.search(r"(\d+)\s*(?:left|available)", lower)
    if m:
        return int(m.group(1))
    if "in stock" in lower or "available" in lower:
        return 99
    return EbayParser._stock_from_availability_text(text)


def _parse_fast_html(html: str, url: str) -> Optional[dict]:
    if not html:
        return None

    blocked, reason = _is_challenge_or_blocked(html)
    if blocked:
        logger.info("eBay AU fast: blocked (%s) for %s", reason, url[:80])
        return None

    soup = BeautifulSoup(html, "html.parser")

    err_el = soup.select_one(_SELECTORS["error_header"])
    err_txt = _clean_text(err_el).lower()
    if err_el and any(x in err_txt for x in ("ended", "removed", "unavailable", "sold out", "no longer")):
        title = EbayParser.extract_title(soup)
        return {"price": None, "stock": 0, "title": title}

    status_el = soup.select_one(_SELECTORS["status_message"])
    if status_el and "ended" in _clean_text(status_el).lower():
        title = EbayParser.extract_title(soup)
        return {"price": None, "stock": 0, "title": title}

    price = EbayParser._au_primary_headline_price(soup)
    if price is None:
        price_el = soup.select_one(_SELECTORS["price"])
        price_text = _clean_text(price_el)
        if price_text and "approximately" not in price_text.lower():
            price = _parse_ebay_display_price_text(price_text)
    if price is None:
        price = EbayParser.extract_price(soup, html)

    stock_el = (
        soup.select_one(_SELECTORS["stock"])
        or soup.select_one(_SELECTORS["stock_fallback"])
    )
    stock = _stock_from_text(_clean_text(stock_el))
    if stock is None:
        stock = EbayParser.extract_stock(soup, html)

    title = EbayParser.extract_title(soup)

    if price is None and not EbayParser.is_valid_listing(soup, html):
        return None

    if price is None:
        return None

    return {
        "price": float(price),
        "stock": int(stock) if stock is not None else None,
        "title": title,
    }


def scrape_ebay_au_fast(vendor_url: str, region: str, session: dict | None = None) -> Optional[dict]:
    """
    Fast AU scrape using cookies. Returns ``None`` to signal fallback to ``ebay_common``.
    """
    if not fast_scrape_enabled():
        return None

    if session is None:
        session = {}

    url = _normalize_url(vendor_url, region or "AU")
    try:
        cookies = _load_cookie_list()
        if not cookies:
            raise ValueError("eBay AU fast scrape: no cookies configured")

        driver = _get_driver(session)
        _inject_cookies(driver, session)
        driver.get(url)
        _wait_for_price_dom(driver, _page_wait_seconds())
        html = driver.page_source or ""
        _note_au_html(session, html)

        parsed = _parse_fast_html(html, url)
        if parsed is None and html and session.get(_AU_PRODUCT_HTML_KEY):
            try:
                from .ebay_common import EbayBrowserSession

                html = EbayBrowserSession._finalize_bin_price_hydration(
                    driver, html, region or "AU", url
                )
                _note_au_html(session, html)
                parsed = _parse_fast_html(html, url)
            except Exception as exc:
                logger.debug("eBay AU fast hydrate retry: %s", exc)

        if parsed is not None:
            logger.info(
                "eBay AU fast OK %s price=%s stock=%s",
                url[:70],
                parsed.get("price"),
                parsed.get("stock"),
            )
        else:
            logger.info(
                "eBay AU fast no-parse %s html_len=%d blocked=%s",
                url[:80],
                len(html),
                session.get(_AU_BLOCKED_KEY),
            )
        return parsed
    except Exception as exc:
        logger.warning("eBay AU fast scrape failed for %s: %s", vendor_url[:80], exc)
        return None


def close_ebay_au_fast_session(session: dict | None) -> None:
    if not session:
        return
    driver = session.pop(_DRIVER_KEY, None)
    session.pop(_COOKIES_LOADED_KEY, None)
    if driver:
        try:
            driver.quit()
        except Exception:
            pass
