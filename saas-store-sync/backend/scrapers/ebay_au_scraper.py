"""
eBay AU scraper (ebay.com.au).

Public API:
    scrape_ebay_au(vendor_url, region, session=None)
    close_ebay_au_session(session)
"""
import logging

from .ebay_common import (
    EBAY_MARKET_AU,
    SESSION_DEBUG_HTML_KEY,
    close_ebay_market_session,
    scrape_ebay_for_market,
)

logger = logging.getLogger("scrapers.ebay_au")


def scrape_ebay_au(vendor_url: str, region: str, session: dict = None) -> dict:
    return scrape_ebay_for_market(
        vendor_url,
        region or "AU",
        session,
        market=EBAY_MARKET_AU,
    )


def close_ebay_au_session(session: dict):
    close_ebay_market_session(session, EBAY_MARKET_AU)


__all__ = [
    "SESSION_DEBUG_HTML_KEY",
    "scrape_ebay_au",
    "close_ebay_au_session",
]
