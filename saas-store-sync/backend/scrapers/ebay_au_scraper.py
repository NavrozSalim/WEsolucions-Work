"""
eBay AU scraper (ebay.com.au).
"""
import logging
import os
import time

from bs4 import BeautifulSoup

from .ebay_au_fast import (
    _AU_BLOCKED_KEY,
    _AU_LAST_HTML_KEY,
    _AU_PRODUCT_HTML_KEY,
    close_ebay_au_fast_session,
    cookies_configured,
    fast_scrape_enabled,
    load_cookies_for_http,
    scrape_ebay_au_fast,
)
from .ebay_common import (
    EBAY_MARKET_AU,
    SESSION_DEBUG_HTML_KEY,
    EbayBrowserSession,
    EbayHTTP,
    EbayParser,
    _ebay_http_first_enabled,
    _ebay_sk,
    _is_challenge_or_blocked,
    _looks_like_product_html,
    _normalize_url,
    _parse_html_to_result,
    close_ebay_market_session,
)

logger = logging.getLogger("scrapers.ebay_au")

_AU_HTTP_COOKIES_LOADED_KEY = _ebay_sk(EBAY_MARKET_AU, "http_cookies_loaded")


def _ensure_cookies_in_http_client(session: dict) -> None:
    """Load AU browser cookies into the curl_cffi session once per worker."""
    if session.get(_AU_HTTP_COOKIES_LOADED_KEY):
        return
    if not cookies_configured():
        session[_AU_HTTP_COOKIES_LOADED_KEY] = True
        return

    client = EbayHTTP._get_client(session, EBAY_MARKET_AU)
    if client is None:
        return

    cookies = load_cookies_for_http()
    loaded = 0
    for c in cookies:
        try:
            name = c.get("name")
            value = c.get("value")
            if not name or value is None:
                continue
            domain = c.get("domain") or ".ebay.com.au"
            path = c.get("path") or "/"
            client.cookies.set(name, value, domain=domain, path=path)
            loaded += 1
        except Exception:
            continue
    session[_AU_HTTP_COOKIES_LOADED_KEY] = True
    if loaded:
        logger.info("eBay AU HTTP: loaded %d cookies into curl_cffi session", loaded)


def _http_first_disabled() -> bool:
    return os.environ.get("EBAY_AU_DISABLE_HTTP_FIRST", "").strip().lower() in ("1", "true", "yes", "on")


def _market_fallback_enabled() -> bool:
    """Whether to run the heavy ``warm_and_fetch`` after the fast Selenium path.

    Default OFF — the fast path already uses Selenium with cookies, so re-running the
    full market browser path almost always fails the same way and wastes 30-60s.
    Set ``EBAY_AU_MARKET_FALLBACK=1`` to re-enable for debugging.
    """
    return os.environ.get("EBAY_AU_MARKET_FALLBACK", "0").strip().lower() in ("1", "true", "yes", "on")


def _total_budget_sec() -> float:
    raw = (os.environ.get("EBAY_AU_TOTAL_BUDGET_SEC") or "").strip()
    if raw:
        try:
            return max(5.0, float(raw))
        except ValueError:
            pass
    return 25.0


def _note_au_html(session: dict, html: str | None, err: str | None = None) -> None:
    if html:
        session[_AU_LAST_HTML_KEY] = html
        if _looks_like_product_html(html):
            session[_AU_PRODUCT_HTML_KEY] = True
            session.pop(_AU_BLOCKED_KEY, None)
        else:
            blocked, reason = _is_challenge_or_blocked(html)
            if blocked:
                session[_AU_BLOCKED_KEY] = reason
    if err and (err.startswith("http_") or err in ("challenge", "blocked")):
        session[_AU_BLOCKED_KEY] = session.get(_AU_BLOCKED_KEY) or err


def _title_from_session_html(session: dict) -> str | None:
    html = session.get(_AU_LAST_HTML_KEY) or ""
    if not html:
        return None
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")
    return EbayParser.extract_title(soup)


def _au_fail_fast(session: dict, url: str) -> dict | None:
    """Return empty result immediately when every path saw a block/challenge (not a PDP)."""
    if session.get(_AU_PRODUCT_HTML_KEY):
        return None
    if not session.get(_AU_BLOCKED_KEY):
        return None
    title = _title_from_session_html(session)
    logger.warning(
        "eBay AU fail-fast (blocked) %s reason=%s",
        url[:70],
        session.get(_AU_BLOCKED_KEY),
    )
    return {"price": None, "stock": None, "title": title}


def scrape_ebay_au(vendor_url: str, region: str, session: dict = None) -> dict:
    if session is None:
        session = {}

    eff_region = region or "AU"
    url = _normalize_url(vendor_url, eff_region)
    t_start = time.monotonic()
    budget = _total_budget_sec()

    def elapsed() -> float:
        return time.monotonic() - t_start

    def budget_left() -> float:
        return max(0.0, budget - elapsed())

    # 1) HTTP-first via curl_cffi (~1-2s when cookies work).
    if _ebay_http_first_enabled(eff_region) and not _http_first_disabled():
        _ensure_cookies_in_http_client(session)
        t0 = time.monotonic()
        html, status, err = EbayHTTP.fetch(url, eff_region, session, EBAY_MARKET_AU)
        _note_au_html(session, html, err)
        logger.info(
            "eBay AU step=http url=%s dt=%.2fs status=%s err=%s",
            url[:70], time.monotonic() - t0, status, err,
        )
        if html and not err:
            parsed = _parse_html_to_result(html, url)
            if parsed is not None and parsed.get("price") is not None:
                logger.info(
                    "eBay AU HTTP-first OK %s price=%s total=%.2fs",
                    url[:70], parsed.get("price"), elapsed(),
                )
                return parsed

    if budget_left() <= 1.0:
        title = _title_from_session_html(session)
        logger.warning("eBay AU budget exhausted before Selenium %s total=%.2fs", url[:70], elapsed())
        return {"price": None, "stock": None, "title": title}

    # 2) Cookie-based Selenium fast path. With expired/blocked cookies returns None fast.
    if fast_scrape_enabled():
        t0 = time.monotonic()
        fast = scrape_ebay_au_fast(vendor_url, region, session)
        logger.info(
            "eBay AU step=fast_selenium url=%s dt=%.2fs got_price=%s",
            url[:70],
            time.monotonic() - t0,
            None if fast is None else fast.get("price"),
        )
        if fast is not None and fast.get("price") is not None:
            return fast
        if fast is not None and fast.get("stock") == 0:
            return fast
        if fast is not None:
            return fast

    # Skip the heavy market warm_and_fetch by default — it uses the same cookies + same
    # Selenium against the same site, so when the fast path failed it almost always also
    # fails, wasting another ~30-60s. Set ``EBAY_AU_MARKET_FALLBACK=1`` to re-enable.
    if not _market_fallback_enabled() or budget_left() <= 2.0:
        title = _title_from_session_html(session)
        logger.warning(
            "eBay AU give up (fast paths missed) %s blocked=%s total=%.2fs",
            url[:70],
            session.get(_AU_BLOCKED_KEY),
            elapsed(),
        )
        return {"price": None, "stock": None, "title": title}

    # 3) Optional final browser attempt (disabled by default).
    t0 = time.monotonic()
    html, browser_err = EbayBrowserSession.warm_and_fetch(url, eff_region, session, EBAY_MARKET_AU)
    _note_au_html(session, html, browser_err)
    logger.info(
        "eBay AU step=market_browser url=%s dt=%.2fs err=%s",
        url[:70], time.monotonic() - t0, browser_err,
    )
    if html:
        parsed = _parse_html_to_result(html, url)
        if parsed is not None and parsed.get("price") is not None:
            logger.info(
                "eBay AU browser OK %s price=%s total=%.2fs",
                url[:70], parsed.get("price"), elapsed(),
            )
            return parsed
        if parsed is not None:
            return parsed

    title = _title_from_session_html(session)
    logger.warning(
        "eBay AU scrape failed %s last_err=%s total=%.2fs",
        url[:70], browser_err, elapsed(),
    )
    return {"price": None, "stock": None, "title": title}


def close_ebay_au_session(session: dict):
    close_ebay_au_fast_session(session)
    close_ebay_market_session(session, EBAY_MARKET_AU)


__all__ = [
    "SESSION_DEBUG_HTML_KEY",
    "scrape_ebay_au",
    "close_ebay_au_session",
    "fast_scrape_enabled",
]
