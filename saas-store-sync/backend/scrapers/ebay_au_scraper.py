"""
eBay AU scraper (ebay.com.au).
"""
import logging
import os
import time

from bs4 import BeautifulSoup

from .ebay_au_proxies import proxies_configured
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
    EbayHTTP,
    EbayParser,
    _ebay_http_first_enabled,
    _ebay_sk,
    _is_challenge_or_blocked,
    _looks_like_product_html,
    _normalize_url,
    _parse_html_to_result,
    close_ebay_market_session,
    scrape_ebay_for_market,
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


def _should_skip_fast_selenium(session: dict) -> bool:
    """Skip fast Selenium when HTTP already saw a block/challenge on datacenter IP.

    With residential proxies, HTTP and Selenium use a different IP so fast Selenium
    is still worth trying.
    """
    if proxies_configured():
        return False
    if not session.get(_AU_BLOCKED_KEY):
        return False
    if os.environ.get("EBAY_AU_FORCE_FAST_SELENIUM", "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    return True


def _full_engine_max_attempts(session: dict) -> int | None:
    """Full US-style retry count when proxies are on; otherwise cap after blocks."""
    if proxies_configured():
        return None
    if session.get(_AU_BLOCKED_KEY):
        raw = (os.environ.get("EBAY_AU_FULL_ENGINE_ATTEMPTS") or "1").strip()
        try:
            return max(1, int(raw))
        except ValueError:
            return 1
    return None


def _note_au_html(session: dict, html: str | None, err: str | None = None) -> None:
    if html:
        session[_AU_LAST_HTML_KEY] = html
        blocked, reason = _is_challenge_or_blocked(html)
        if blocked:
            session[_AU_BLOCKED_KEY] = reason
            session.pop(_AU_PRODUCT_HTML_KEY, None)
        elif _looks_like_product_html(html):
            session[_AU_PRODUCT_HTML_KEY] = True
            session.pop(_AU_BLOCKED_KEY, None)
    if err and (err.startswith("http_") or err in ("challenge", "blocked", "not_product_like")):
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


def scrape_ebay_au(vendor_url: str, region: str, session: dict = None) -> dict:
    """Scrape an ebay.com.au product page.

    Strategy mirrors ``ebay_us_scraper`` for parity (HTTP-first, then Selenium
    with cookie handoff and retries), with two AU-specific fast paths layered
    on top for speed when cookies are healthy:

      1. **HTTP-first** with pre-loaded AU cookies (~1-2s typical hit).
      2. **Cookie-based fast Selenium** (eager load + short DOM wait) — skipped when
         HTTP already saw a challenge (same IP/cookies → same block, ~45s wasted).
      3. **Full ``scrape_ebay_for_market`` engine** — the same proven HTTP +
         warm-Selenium + cookie-handoff retry loop the US scraper uses, so a
         single fast-path miss never causes an immediate failure.

    Step 3 is what makes AU as accurate as US: instead of giving up after one
    fast-Selenium attempt, we run the full engine with backoff retries.
    """
    if session is None:
        session = {}

    eff_region = region or "AU"
    url = _normalize_url(vendor_url, eff_region)
    t_start = time.monotonic()

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
            if parsed is not None:
                logger.info(
                    "eBay AU HTTP-first OK %s price=%s total=%.2fs",
                    url[:70], parsed.get("price"), time.monotonic() - t_start,
                )
                return parsed
            if not session.get(_AU_PRODUCT_HTML_KEY):
                session[_AU_BLOCKED_KEY] = session.get(_AU_BLOCKED_KEY) or "not_product_like"

    if fast_scrape_enabled() and not _should_skip_fast_selenium(session):
        t0 = time.monotonic()
        fast = scrape_ebay_au_fast(vendor_url, region, session)
        logger.info(
            "eBay AU step=fast_selenium url=%s dt=%.2fs got_price=%s",
            url[:70],
            time.monotonic() - t0,
            None if fast is None else fast.get("price"),
        )
        if fast is not None:
            logger.info(
                "eBay AU fast-Selenium OK %s price=%s total=%.2fs",
                url[:70], fast.get("price"), time.monotonic() - t_start,
            )
            return fast
    elif fast_scrape_enabled() and _should_skip_fast_selenium(session):
        logger.info(
            "eBay AU skip fast_selenium (HTTP blocked=%s) url=%s",
            session.get(_AU_BLOCKED_KEY),
            url[:70],
        )

    _ensure_cookies_in_http_client(session)
    t0 = time.monotonic()
    result = scrape_ebay_for_market(
        vendor_url,
        eff_region,
        session,
        market=EBAY_MARKET_AU,
        max_attempts=_full_engine_max_attempts(session),
    )
    logger.info(
        "eBay AU step=full_engine url=%s dt=%.2fs got_price=%s total=%.2fs",
        url[:70],
        time.monotonic() - t0,
        (result or {}).get("price"),
        time.monotonic() - t_start,
    )
    if result and result.get("price") is not None:
        return result

    title = (result or {}).get("title") or _title_from_session_html(session)
    return {
        "price": (result or {}).get("price"),
        "stock": (result or {}).get("stock"),
        "title": title,
    }


def close_ebay_au_session(session: dict):
    close_ebay_au_fast_session(session)
    close_ebay_market_session(session, EBAY_MARKET_AU)


__all__ = [
    "SESSION_DEBUG_HTML_KEY",
    "scrape_ebay_au",
    "close_ebay_au_session",
    "fast_scrape_enabled",
]
