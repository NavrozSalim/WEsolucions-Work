"""
Scraper dispatcher.

Routes vendor URLs to the correct scraper (Amazon US, Amazon AU, eBay) based on
domain. Each scraper returns {"price": float|None, "stock": int|None} and may
include "title" (str) when extracted — same shape for Amazon US and eBay.

HEB, Costco AU and Vevor AU are **not** scraped server-side. Their PDPs are
protected by Akamai / Cloudflare Bot Management from datacenter IPs, so the
source of truth for their pricing is the desktop runner (HEB, Costco) or the
public S3 XLSX feed (Vevor AU). Those flows write directly to both
``VendorPrice`` **and** ``ProductMapping`` (store_price / store_stock /
last_scrape_time) via ``/api/v1/ingest/heb/``, ``/api/v1/ingest/costco/`` and
``catalog.tasks.run_vevor_au_ingest``. The catalog scrape and store-sync
tasks detect these vendors (``_is_ingest_only_product``) and skip them
entirely — they never re-apply an older VendorPrice row as a substitute
for fresh scrape data.

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


def _rewrite_url_for_region(vendor_url: str, region: str) -> str:
    """Promote a bare ``amazon.com`` / ``ebay.com`` URL to the AU TLD when the
    store says it's an AU store.

    Intentionally **non-destructive**: an explicit ``amazon.com.au`` or
    ``ebay.com.au`` URL is never rewritten back to the US TLD, even if the
    store region is ``USA``. This protects the common case where a US store
    still carries AU-sourced products (AmazonAU / EbayAU vendor rows) and
    shouldn't have those URLs silently broken.
    """
    if not vendor_url or not region:
        return vendor_url
    r = region.upper()
    url_lower = vendor_url.lower()

    if "amazon." in url_lower:
        if r == "AU" and "amazon.com.au" not in url_lower:
            return vendor_url.replace("amazon.com", "amazon.com.au")

    if "ebay." in url_lower:
        if r == "AU" and "ebay.com.au" not in url_lower:
            return vendor_url.replace("ebay.com", "ebay.com.au")

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


def _costco_ingest_only_result() -> dict:
    """Costco AU has no server-side scraper; data comes from the /api/v1/ingest/costco/ endpoint."""
    return {
        "price": None,
        "inventory": None,
        "title": None,
        "error_code": "costco_ingest_only",
        "error_message": (
            "Costco AU is ingest-only on the server; the desktop runner POSTs prices to "
            "/api/v1/ingest/costco/. The task will fall back to the latest VendorPrice."
        ),
    }


def _vevor_ingest_only_result() -> dict:
    """Vevor AU is refreshed from the public S3 XLSX feed, not per-URL scraped."""
    from .vevor_au import _ingest_only_result as _res
    return _res()


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
            logger.info("Routing to Amazon AU scraper: %s", vendor_url[:80])
            return _normalize_scrape_payload(scrape_fn(vendor_url, region, session))
        scrape_fn, _ = _get_amazon_us_scraper()
        logger.info("Routing to Amazon US scraper: %s", vendor_url[:80])
        return _normalize_scrape_payload(scrape_fn(vendor_url, region, session))

    if "ebay." in url_lower:
        from .ebay_scraper import scrape_ebay
        region_tag = "AU" if "ebay.com.au" in url_lower else (
            region.upper() if region else "USA"
        )
        logger.info("Routing to eBay %s scraper: %s", region_tag, vendor_url[:80])
        return _normalize_scrape_payload(scrape_ebay(vendor_url, region, session))

    if "heb.com" in url_lower:
        logger.info("HEB URL skipped server-side (ingest-only): %s", vendor_url[:80])
        return _normalize_scrape_payload(_heb_ingest_only_result())

    if "costco.com.au" in url_lower:
        logger.info("Costco AU URL skipped server-side (ingest-only): %s", vendor_url[:80])
        return _normalize_scrape_payload(_costco_ingest_only_result())

    if "vevor.com.au" in url_lower or "vevor.au" in url_lower:
        logger.info("Vevor AU URL skipped server-side (feed ingest): %s", vendor_url[:80])
        return _normalize_scrape_payload(_vevor_ingest_only_result())

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


__all__ = ["get_price_and_stock", "close_amazon_session"]
