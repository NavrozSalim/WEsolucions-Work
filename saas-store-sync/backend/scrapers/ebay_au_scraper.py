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
    _au_http_shipping_resolved,
    _normalize_url,
    _parse_html_to_result_au,
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


def _http_first_warm_retries() -> int:
    """Extra HTTP-first attempts after a parse miss (warm cookies often unlock BIN price)."""
    raw = (os.environ.get("EBAY_AU_HTTP_FIRST_WARM_RETRIES") or "1").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 1


def _bail_on_http_block_when_proxied() -> bool:
    """When HTTP-first already exhausted the proxy pool with ``http_403``/``blocked``,
    repeating the same HTTP call inside the full engine just burns 20-40s per item
    (proxies are still in cooldown and we can't auth Chrome --proxy-server). Default on.
    """
    raw = (os.environ.get("EBAY_AU_BAIL_ON_HTTP_BLOCK") or "1").strip().lower()
    return raw in ("1", "true", "yes", "on")


_PROXY_BLOCK_ERRORS = {"http_403", "http_401", "http_429", "blocked", "challenge"}


def _should_skip_fast_selenium(session: dict) -> bool:
    """Skip fast Selenium when proxies are on or HTTP already saw a block.

    With Webshare/residential proxies Chrome can't authenticate via ``--proxy-server``
    (no creds on bare ``host:port``), so fast Selenium returns ~39-byte stubs. Skip
    it entirely when proxies are configured.
    """
    if proxies_configured():
        return True
    if not session.get(_AU_BLOCKED_KEY):
        return False
    if os.environ.get("EBAY_AU_FORCE_FAST_SELENIUM", "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    return True


def _full_engine_max_attempts(session: dict) -> int | None:
    """Cap full-engine retries.

    When proxies are on AND we already saw an HTTP block, repeating the full engine
    (which just calls ``EbayHTTP.fetch`` again on the same cooled-down proxies) is
    pure waste. Cap at 1 attempt in that case. Otherwise let the engine pick its
    default (``RETRY_LIMIT``).
    """
    blocked = bool(session.get(_AU_BLOCKED_KEY))
    if blocked:
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

    http_block_err: str | None = None

    if _ebay_http_first_enabled(eff_region) and not _http_first_disabled():
        _ensure_cookies_in_http_client(session)
        http_attempts = 1 + _http_first_warm_retries()
        for http_attempt in range(http_attempts):
            t0 = time.monotonic()
            html, status, err = EbayHTTP.fetch(url, eff_region, session, EBAY_MARKET_AU)
            _note_au_html(session, html, err)
            logger.info(
                "eBay AU step=http attempt=%s url=%s dt=%.2fs status=%s err=%s",
                http_attempt + 1, url[:70], time.monotonic() - t0, status, err,
            )
            if html and not err:
                parsed = _parse_html_to_result_au(html, url)
                if parsed is not None and _au_http_shipping_resolved(html):
                    logger.info(
                        "eBay AU HTTP-first OK %s price=%s total=%.2fs",
                        url[:70], parsed.get("price"), time.monotonic() - t_start,
                    )
                    return parsed
                if parsed is not None:
                    logger.info(
                        "eBay AU HTTP-first skip (postage not hydrated) url=%s price=%s",
                        url[:70], parsed.get("price"),
                    )
                    break
                # HTML looked like a product but parser found no price.
                # Retry once: eBay BIN hydration often needs a warm session.
                if http_attempt + 1 < http_attempts:
                    time.sleep(0.4)
                    continue
                if not session.get(_AU_PRODUCT_HTML_KEY):
                    session[_AU_BLOCKED_KEY] = session.get(_AU_BLOCKED_KEY) or "not_product_like"
            if err and err not in ("not_product_like",):
                # Hard error (block, http_4xx/5xx) — break out and let next stage retry/rotate.
                if err in _PROXY_BLOCK_ERRORS:
                    http_block_err = err
                break

    # When proxies are on and HTTP-first exhausted the pool with a hard block
    # (403/401/429/challenge), the full engine will just repeat the same blocked
    # HTTP fetch on the same cooled-down proxies and waste 20-40s per item. Bail
    # now so the catalog scrape moves on to the next URL quickly.
    if (
        http_block_err
        and proxies_configured()
        and _bail_on_http_block_when_proxied()
    ):
        logger.warning(
            "eBay AU HTTP block (%s) under proxies — skipping full engine url=%s total=%.2fs",
            http_block_err, url[:70], time.monotonic() - t_start,
        )
        return {"price": None, "stock": None, "title": _title_from_session_html(session)}

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
