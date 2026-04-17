"""
Scraper dispatcher.

Routes vendor URLs to the correct scraper (Amazon US, Amazon AU, eBay, Costco AU)
based on domain. Each scraper returns {"price": float|None, "stock": int|None}
and may include "title" (str) when extracted — same shape for Amazon US and eBay.

HEB is **not** scraped server-side. HEB PDPs are Akamai-protected from datacenter
IPs, so the source of truth for HEB pricing is the desktop runner that POSTs to
``/api/v1/ingest/heb/``. The dispatcher therefore short-circuits HEB URLs with
``error_code=heb_ingest_only``; the catalog scrape task then falls back to the
latest ``VendorPrice`` (written by the ingest endpoint) via
``resolve_vendor_price_for_listing``.

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


def _heb_ingest_only_result() -> dict:
    """HEB has no server-side scraper; data comes from the /api/v1/ingest/heb/ endpoint."""
    return {
        "price": None,
        "inventory": None,
        "title": None,
        "error_code": "heb_ingest_only",
        "error_message": (
            "HEB is ingest-only on the server; the desktop runner POSTs prices to "
            "/api/v1/ingest/heb/. The task will fall back to the latest VendorPrice."
        ),
    }


def get_price_and_stock(vendor_url: str, region: str, session: dict = None) -> dict:
    """
    Main entry point: resolve vendor URL → scraper → return price + stock.

    Routing uses the **URL host/path only** (Amazon, eBay, Costco AU, …). It does not
    depend on which marketplace the listing is sold on (Reverb, Walmart, Sears, etc.).

    HEB URLs are intentionally **not** scraped here: Akamai blocks datacenter IPs,
    so HEB prices arrive via the ingest API instead. We return a sentinel result
    so the catalog task falls back to the latest ``VendorPrice`` row.

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
    dict with keys "price" (float|None), "inventory" (int|None), and optionally
    "title" (str) when the page exposes a product title. May also include
    "error_code" / "error_message" when the row is skipped.
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
        logger.info("HEB URL skipped server-side (ingest-only): %s", vendor_url[:80])
        return _normalize_scrape_payload(_heb_ingest_only_result())

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
    - error_code / error_message (optional, preserved when present)
    """
    result = result or {}
    inventory = result.get("inventory")
    if inventory is None:
        inventory = result.get("stock")
    payload = {
        "price": result.get("price"),
        "inventory": inventory,
        "title": result.get("title"),
    }
    if result.get("error_code"):
        payload["error_code"] = result["error_code"]
    if result.get("error_message"):
        payload["error_message"] = result["error_message"]
    return payload


def close_amazon_session(session):
    """Close all browser sessions (Amazon US, Amazon AU, eBay, Costco AU) held in this session dict."""
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
    _, close_costco_au = _get_costco_au_scraper()
    close_costco_au(session)


__all__ = ["get_price_and_stock", "close_amazon_session"]
