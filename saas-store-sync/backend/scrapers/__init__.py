"""
Scraper dispatcher.

Routes vendor URLs to the correct scraper (Amazon US, Amazon AU, eBay)
based on domain. Each scraper returns {"price": float|None, "stock": int|None}
and may include "title" (str) when extracted—same shape for Amazon US and eBay.

Usage in tasks:
    from scrapers import get_price_and_stock, close_amazon_session

    session = {}
    try:
        for product in products:
            result = get_price_and_stock(product.vendor_url, store.region, session)
            price, stock = result["price"], result["stock"]
    finally:
        close_amazon_session(session)
"""
import logging

logger = logging.getLogger("scrapers")

# Lazy imports — Selenium is heavy; don't load it until needed.
_scrape_amazon_us = None
_close_amazon_us = None
_scrape_amazon_legacy = None
_close_amazon_legacy = None
_scrape_heb = None
_close_heb = None
_scrape_costco_au = None
_close_costco_au = None


def _get_amazon_us_scraper():
    global _scrape_amazon_us, _close_amazon_us
    if _scrape_amazon_us is None:
        try:
            from .amazon_us_scraper import scrape_amazon_us, close_amazon_us_session
            _scrape_amazon_us = scrape_amazon_us
            _close_amazon_us = close_amazon_us_session
        except ImportError as exc:
            logger.warning("Amazon US scraper unavailable: %s", exc)
            _scrape_amazon_us = _placeholder_scrape
            _close_amazon_us = lambda s: None
    return _scrape_amazon_us, _close_amazon_us


def _get_amazon_legacy_scraper():
    global _scrape_amazon_legacy, _close_amazon_legacy
    if _scrape_amazon_legacy is None:
        try:
            from .amazon_scraper import scrape_amazon, close_amazon_session
            _scrape_amazon_legacy = scrape_amazon
            _close_amazon_legacy = close_amazon_session
        except ImportError as exc:
            logger.warning("Amazon legacy scraper unavailable: %s", exc)
            _scrape_amazon_legacy = _placeholder_scrape
            _close_amazon_legacy = lambda s: None
    return _scrape_amazon_legacy, _close_amazon_legacy


def _get_heb_scraper():
    global _scrape_heb, _close_heb
    if _scrape_heb is None:
        try:
            from .heb_scraper import scrape_heb, close_heb_session
            _scrape_heb = scrape_heb
            _close_heb = close_heb_session
        except ImportError as exc:
            logger.warning("HEB scraper unavailable: %s", exc)
            _scrape_heb = _placeholder_scrape
            _close_heb = lambda s: None
    return _scrape_heb, _close_heb


def _get_costco_au_scraper():
    global _scrape_costco_au, _close_costco_au
    if _scrape_costco_au is None:
        try:
            from .costco_au_scraper import scrape_costco_au, close_costco_au_session
            _scrape_costco_au = scrape_costco_au
            _close_costco_au = close_costco_au_session
        except ImportError as exc:
            logger.warning("Costco AU scraper unavailable: %s", exc)
            _scrape_costco_au = _placeholder_scrape
            _close_costco_au = lambda s: None
    return _scrape_costco_au, _close_costco_au


def _rewrite_url_for_region(vendor_url: str, region: str) -> str:
    """Rewrite vendor URL domain to match the user-selected store region."""
    if not vendor_url or not region:
        return vendor_url
    r = region.upper()
    url_lower = vendor_url.lower()

    if "amazon." in url_lower:
        if r == "AU" and "amazon.com.au" not in url_lower:
            return vendor_url.replace("amazon.com", "amazon.com.au")
        if r == "USA" and "amazon.com.au" in url_lower:
            return vendor_url.replace("amazon.com.au", "amazon.com")

    if "ebay." in url_lower:
        if r == "AU" and "ebay.com.au" not in url_lower:
            return vendor_url.replace("ebay.com", "ebay.com.au")
        if r == "USA" and "ebay.com.au" in url_lower:
            return vendor_url.replace("ebay.com.au", "ebay.com")

    return vendor_url


def get_price_and_stock(vendor_url: str, region: str, session: dict = None) -> dict:
    """
    Main entry point: resolve vendor URL → scraper → return price + stock.

    Routing uses the **URL host/path only** (Amazon, eBay, HEB, …). It does not depend on
    which marketplace the listing is sold on (Reverb, Walmart, Sears, etc.).

    Parameters
    ----------
    vendor_url : str
        Full product URL (Amazon, eBay, etc.)
    region : str
        'USA' or 'AU' — scraping logic can differ by country.
    session : dict, optional
        Shared across multiple calls in the same sync run (reuses browser sessions).

    Returns
    -------
    dict with keys "price" (float|None), "stock" (int|None), and optionally
    "title" (str) when the page exposes a product title.
    """
    vendor_url = _rewrite_url_for_region(vendor_url, region)
    url_lower = (vendor_url or "").lower()

    if "amazon." in url_lower:
        if "amazon.com.au" in url_lower:
            scrape_fn, _ = _get_amazon_legacy_scraper()
            logger.debug("Routing to Amazon AU scraper: %s", vendor_url[:80])
            return _normalize_scrape_payload(scrape_fn(vendor_url, region, session))
        scrape_fn, _ = _get_amazon_us_scraper()
        logger.debug("Routing to Amazon US scraper: %s", vendor_url[:80])
        return _normalize_scrape_payload(scrape_fn(vendor_url, region, session))

    if "ebay." in url_lower:
        from .ebay_scraper import scrape_ebay
        logger.debug("Routing to eBay scraper: %s", vendor_url[:80])
        return _normalize_scrape_payload(scrape_ebay(vendor_url, region, session))

    if "heb.com" in url_lower:
        scrape_fn, _ = _get_heb_scraper()
        logger.debug("Routing to HEB scraper: %s", vendor_url[:80])
        return _normalize_scrape_payload(scrape_fn(vendor_url, region, session))

    if "costco.com.au" in url_lower:
        scrape_fn, _ = _get_costco_au_scraper()
        logger.debug("Routing to Costco AU scraper: %s", vendor_url[:80])
        return _normalize_scrape_payload(scrape_fn(vendor_url, region, session))

    logger.warning("No scraper registered for URL: %s", vendor_url[:80])
    return _placeholder_scrape(vendor_url, region)


def _placeholder_scrape(vendor_url: str, region: str, session: dict = None) -> dict:
    """Fallback for unsupported vendor domains."""
    return {"price": None, "inventory": None, "title": None}


def _normalize_scrape_payload(result: dict | None) -> dict:
    """
    Enforce a minimal, consistent scraper payload across vendors:
    - price
    - inventory
    - title
    """
    result = result or {}
    inventory = result.get("inventory")
    if inventory is None:
        inventory = result.get("stock")
    return {
        "price": result.get("price"),
        "inventory": inventory,
        "title": result.get("title"),
    }


def close_amazon_session(session):
    """Close all browser sessions (Amazon US, Amazon AU, eBay) held in this session dict."""
    if session is None:
        return
    _, close_us = _get_amazon_us_scraper()
    _, close_legacy = _get_amazon_legacy_scraper()
    close_us(session)
    close_legacy(session)
    try:
        from .ebay_scraper import close_ebay_session
        close_ebay_session(session)
    except ImportError:
        pass
    _, close_heb = _get_heb_scraper()
    close_heb(session)
    _, close_costco_au = _get_costco_au_scraper()
    close_costco_au(session)


__all__ = ["get_price_and_stock", "close_amazon_session"]
