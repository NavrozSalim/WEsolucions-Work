"""
Scraper dispatcher.

Routes vendor URLs to the correct scraper based on domain. Each scraper returns
``{"price": float|None, "stock": int|None}`` and may include ``"title"`` (str)
when extracted.

Server-side vendors (scraped from this dispatcher):

* **Amazon US / AU** — HTTP-first + Selenium fallback
* **eBay US / AU**   — curl_cffi (Cloudflare TLS) + Selenium fallback
* **Costco AU**      — curl_cffi through residential AU proxies
  (set ``COSTCO_AU_PROXY_URLS`` on the worker; without proxies the dispatcher
  returns an ``ingest_only`` sentinel so the catalog task keeps the existing
  ``ProductMapping`` row untouched.)
* **HEB US**         — desktop runner (``desktop-runners/heb/``) with Chrome + cookies,
  or server scrape when ``HEB_US_PROXY_URLS`` / ``HEB_COOKIES_ONLY`` is set on the US worker

Ingest-only vendors (NOT scraped server-side unless proxies are configured):

* **HEB**       — desktop runner via ``desktop-runners/heb/`` (cookies + Chrome) or
  ``/api/v1/ingest/heb/`` when server scrape is disabled; live server scrape when
  ``HEB_US_PROXY_URLS`` or ``HEB_COOKIES_ONLY`` is set on the US worker
* **Vevor AU**  — refreshed from the public S3 XLSX feed via
  ``catalog.tasks.run_vevor_au_ingest``

The catalog and store-sync tasks detect ingest-only vendors via
``catalog.tasks._is_ingest_only_product`` and skip server-side HTTP scraping
for them so historical ``VendorPrice`` data is never silently re-applied.

Usage in tasks::

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
_scrape_amazon_au = None
_close_amazon_au = None
_scrape_ebay_us = None
_close_ebay_us = None
_scrape_ebay_au = None
_close_ebay_au = None
_scrape_costco_au = None
_close_costco_au = None
_scrape_heb_us = None
_close_heb_us = None


def _get_heb_us_scraper():
    global _scrape_heb_us, _close_heb_us
    if _scrape_heb_us is None:
        try:
            from .heb_us_scraper import scrape_heb, close_heb_session
            _scrape_heb_us = scrape_heb
            _close_heb_us = close_heb_session
        except ImportError:
            logger.exception("Failed to import HEB US scraper")
            _scrape_heb_us = _placeholder_scrape
            _close_heb_us = lambda s: None
    return _scrape_heb_us, _close_heb_us


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


def _get_amazon_au_scraper():
    global _scrape_amazon_au, _close_amazon_au
    if _scrape_amazon_au is None:
        try:
            from .amazon_au_scraper import scrape_amazon_au, close_amazon_au_session
            _scrape_amazon_au = scrape_amazon_au
            _close_amazon_au = close_amazon_au_session
        except ImportError as exc:
            logger.warning("Amazon AU scraper unavailable: %s", exc)
            _scrape_amazon_au = _placeholder_scrape
            _close_amazon_au = lambda s: None
    return _scrape_amazon_au, _close_amazon_au


def _get_ebay_us_scraper():
    global _scrape_ebay_us, _close_ebay_us
    if _scrape_ebay_us is None:
        try:
            from .ebay_us_scraper import scrape_ebay_us, close_ebay_us_session
            _scrape_ebay_us = scrape_ebay_us
            _close_ebay_us = close_ebay_us_session
        except ImportError as exc:
            logger.warning("eBay US scraper unavailable: %s", exc)
            _scrape_ebay_us = _placeholder_scrape
            _close_ebay_us = lambda s: None
    return _scrape_ebay_us, _close_ebay_us


def _get_ebay_au_scraper():
    global _scrape_ebay_au, _close_ebay_au
    if _scrape_ebay_au is None:
        try:
            from .ebay_au_scraper import scrape_ebay_au, close_ebay_au_session
            _scrape_ebay_au = scrape_ebay_au
            _close_ebay_au = close_ebay_au_session
        except ImportError as exc:
            logger.warning("eBay AU scraper unavailable: %s", exc)
            _scrape_ebay_au = _placeholder_scrape
            _close_ebay_au = lambda s: None
    return _scrape_ebay_au, _close_ebay_au


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


def _heb_us_server_scrape_enabled() -> bool:
    """True when HEB should be scraped on the US worker (proxies or cookies-only)."""
    from .heb_us_proxies import load_proxy_urls

    if load_proxy_urls():
        return True
    from .heb_us_scraper import _cookies_only_mode

    return _cookies_only_mode()


def _heb_ingest_only_result() -> dict:
    """Sentinel when HEB server scraping is not enabled (no proxies or cookies-only)."""
    return {
        "price": None,
        "inventory": None,
        "title": None,
        "error_code": "heb_ingest_only",
        "error_message": (
            "HEB server scraping disabled (set HEB_US_PROXY_URLS or "
            "HEB_COOKIES_ONLY=1 with HEB_COOKIES_FILE); "
            "the task will keep the latest VendorPrice and skip this row."
        ),
    }


def _costco_au_server_scrape_enabled() -> bool:
    """True when residential proxies are configured for server-side Costco AU scraping.

    Without proxies we fall back to the legacy ingest-only behavior so a worker
    deployed with the new code but no env update never silently hits Costco
    from a datacenter IP (guaranteed Cloudflare block).
    """
    from .costco_au_proxies import load_proxy_urls
    return bool(load_proxy_urls())


def _costco_ingest_only_result() -> dict:
    """Sentinel returned only when server scraping is not enabled for Costco AU.

    Once ``COSTCO_AU_PROXY_URLS`` is set on the AU worker, the dispatcher routes
    Costco URLs through the live scraper instead of returning this stub.
    """
    return {
        "price": None,
        "inventory": None,
        "title": None,
        "error_code": "costco_ingest_only",
        "error_message": (
            "Costco AU server scraping disabled (no COSTCO_AU_PROXY_URLS configured); "
            "the task will keep the latest VendorPrice and skip this row."
        ),
    }


def _vevor_ingest_only_result() -> dict:
    """Vevor AU is refreshed from the public S3 XLSX feed, not per-URL scraped."""
    from .vevor_au_ingest import _ingest_only_result as _res
    return _res()


def get_price_and_stock(vendor_url: str, region: str, session: dict = None) -> dict:
    """
    Main entry point: resolve vendor URL → scraper → return price + stock.

    Routing uses the **URL host/path only** (Amazon, eBay, Costco AU, …). It does not
    depend on which marketplace the listing is sold on (Reverb, Walmart, Sears, etc.).

    HEB URLs are scraped server-side when ``HEB_US_PROXY_URLS`` is set or when
    cookies-only mode is enabled (``HEB_COOKIES_ONLY=1`` with ``HEB_COOKIES_FILE``).
    Otherwise HEB prices arrive via the ingest API and we return a sentinel so the
    catalog task falls back to the latest ``VendorPrice`` row.

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
            scrape_fn, _ = _get_amazon_au_scraper()
            logger.info("Routing to Amazon AU scraper: %s", vendor_url[:80])
            return _normalize_scrape_payload(scrape_fn(vendor_url, region, session))
        scrape_fn, _ = _get_amazon_us_scraper()
        logger.info("Routing to Amazon US scraper: %s", vendor_url[:80])
        return _normalize_scrape_payload(scrape_fn(vendor_url, region, session))

    if "ebay." in url_lower:
        if "ebay.com.au" in url_lower:
            scrape_fn, _ = _get_ebay_au_scraper()
            logger.info("Routing to eBay AU scraper: %s", vendor_url[:80])
        else:
            scrape_fn, _ = _get_ebay_us_scraper()
            logger.info("Routing to eBay US scraper: %s", vendor_url[:80])
        return _normalize_scrape_payload(scrape_fn(vendor_url, region, session))

    if "heb.com" in url_lower:
        if _heb_us_server_scrape_enabled():
            scrape_fn, _ = _get_heb_us_scraper()
            logger.info("Routing to HEB US scraper: %s", vendor_url[:80])
            return _normalize_scrape_payload(scrape_fn(vendor_url, region, session))
        logger.info(
            "HEB URL skipped server-side (no proxies or cookies-only): %s",
            vendor_url[:80],
        )
        return _normalize_scrape_payload(_heb_ingest_only_result())

    if "costco.com.au" in url_lower:
        if _costco_au_server_scrape_enabled():
            scrape_fn, _ = _get_costco_au_scraper()
            logger.info("Routing to Costco AU scraper: %s", vendor_url[:80])
            return _normalize_scrape_payload(scrape_fn(vendor_url, region, session))
        logger.info("Costco AU URL skipped server-side (no proxies configured): %s", vendor_url[:80])
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
    """Close all browser/HTTP sessions held in ``session``.

    Despite the legacy name, this closes Amazon US/AU, eBay US/AU, Costco AU,
    and HEB US sessions — callers pass a single shared session dict per Celery
    task and this function is the one cleanup hook.
    """
    if session is None:
        return
    _, close_us = _get_amazon_us_scraper()
    _, close_au = _get_amazon_au_scraper()
    close_us(session)
    close_au(session)
    _, close_ebay_us = _get_ebay_us_scraper()
    _, close_ebay_au = _get_ebay_au_scraper()
    close_ebay_us(session)
    close_ebay_au(session)
    _, close_costco = _get_costco_au_scraper()
    close_costco(session)
    _, close_heb = _get_heb_us_scraper()
    close_heb(session)


__all__ = ["get_price_and_stock", "close_amazon_session"]
