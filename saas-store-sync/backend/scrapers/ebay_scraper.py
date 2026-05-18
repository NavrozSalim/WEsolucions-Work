"""
Backward-compatible eBay imports.

Prefer ``ebay_us_scraper`` / ``ebay_au_scraper`` for new code.
"""
from .ebay_au_scraper import close_ebay_au_session, scrape_ebay_au
from .ebay_common import (
    EbayParser,
    SESSION_DEBUG_HTML_KEY,
    _ebay_bin_hydrate_max_seconds,
    _effective_ebay_region,
    _normalize_url,
    _strip_price_suffix,
    close_ebay_session,
)
from .ebay_us_scraper import close_ebay_us_session, scrape_ebay_us


def scrape_ebay(vendor_url: str, region: str, session: dict = None) -> dict:
    url_lower = (vendor_url or "").lower()
    if "ebay.com.au" in url_lower:
        return scrape_ebay_au(vendor_url, region, session)
    return scrape_ebay_us(vendor_url, region, session)


__all__ = [
    "EbayParser",
    "SESSION_DEBUG_HTML_KEY",
    "_ebay_bin_hydrate_max_seconds",
    "_effective_ebay_region",
    "_normalize_url",
    "_strip_price_suffix",
    "close_ebay_session",
    "close_ebay_au_session",
    "close_ebay_us_session",
    "scrape_ebay",
    "scrape_ebay_au",
    "scrape_ebay_us",
]
