"""
eBay AU scraper (ebay.com.au).

Fast path (cookies + eager Selenium, ~2–5s per URL when configured):
  Set ``EBAY_AU_COOKIES_FILE`` or ``EBAY_AU_COOKIES_JSON`` on the AU worker.
  See ``ebay_au_fast`` and ``.env.prod.example``.

Public API:
    scrape_ebay_au(vendor_url, region, session=None)
    close_ebay_au_session(session)
"""
import logging

from .ebay_au_fast import close_ebay_au_fast_session, fast_scrape_enabled, scrape_ebay_au_fast
from .ebay_common import (
    EBAY_MARKET_AU,
    SESSION_DEBUG_HTML_KEY,
    close_ebay_market_session,
    scrape_ebay_for_market,
)

logger = logging.getLogger("scrapers.ebay_au")


def scrape_ebay_au(vendor_url: str, region: str, session: dict = None) -> dict:
    if fast_scrape_enabled():
        fast = scrape_ebay_au_fast(vendor_url, region, session)
        if fast is not None and fast.get("price") is not None:
            return fast
        if fast is not None and fast.get("stock") == 0:
            return fast
        logger.info("eBay AU fast path missed price; falling back to full scraper for %s", vendor_url[:80])

    return scrape_ebay_for_market(
        vendor_url,
        region or "AU",
        session,
        market=EBAY_MARKET_AU,
    )


def close_ebay_au_session(session: dict):
    close_ebay_au_fast_session(session)
    close_ebay_market_session(session, EBAY_MARKET_AU)


__all__ = [
    "SESSION_DEBUG_HTML_KEY",
    "scrape_ebay_au",
    "close_ebay_au_session",
    "fast_scrape_enabled",
]
