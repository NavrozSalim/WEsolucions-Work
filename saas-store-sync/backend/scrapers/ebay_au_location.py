"""eBay AU deliver-to postcode for item pages.

A logged-out visitor's postage is priced for the place eBay guesses from the IP.
Asking the item's postage calculator for a postcode (the "Delivery to" box) makes
eBay remember "<postcode>, Australia" in that visitor's cookies, and every item
page after that prices delivery to it. So each HTTP client and each Chrome asks
once, before its first item page.

  EBAY_AU_POSTCODE      deliver-to postcode (default 2155, North Kellyville NSW)
  EBAY_AU_SET_POSTCODE  0 = leave eBay's own guess
"""
from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger("scrapers.ebay_au_location")

DEFAULT_POSTCODE = "2155"
EBAY_AU_ORIGIN = "https://www.ebay.com.au"
# Set on the curl client / Chrome driver: the postcode that client last told eBay.
_MARK = "_ebay_au_ship_to"


def ebay_au_postcode() -> str:
    digits = re.sub(r"\D", "", os.environ.get("EBAY_AU_POSTCODE") or DEFAULT_POSTCODE)
    if len(digits) == 4:
        return digits
    logger.warning("Invalid EBAY_AU_POSTCODE — using %s", DEFAULT_POSTCODE)
    return DEFAULT_POSTCODE


def postcode_enabled() -> bool:
    return (os.environ.get("EBAY_AU_SET_POSTCODE") or "1").strip().lower() not in ("0", "false", "no", "off")


def rates_path(item_id: str, postcode: str, qty: int = 1) -> str:
    from .shipping_quote import EBAY_AU_RATES_PATH

    return EBAY_AU_RATES_PATH.format(item_id=item_id, qty=max(1, int(qty or 1)), postcode=postcode)


def remember_postcode(target, postcode: str) -> None:
    """Note that ``target`` (curl client or driver) just told eBay ``postcode``."""
    try:
        setattr(target, _MARK, postcode)
    except Exception:
        pass


def _wanted(target, url: str) -> tuple[str, str]:
    """(item id, postcode) when ``target`` still has to tell eBay the postcode, else ('', '')."""
    if target is None or not postcode_enabled():
        return "", ""
    if "ebay.com.au" not in (url or "").lower():
        return "", ""
    postcode = ebay_au_postcode()
    if getattr(target, _MARK, None) == postcode:
        return "", ""
    from .shipping_quote import ebay_item_id

    item_id = ebay_item_id(url)
    return (item_id, postcode) if item_id else ("", "")


def ensure_http_postcode(client, url: str, headers: dict | None = None) -> None:
    """Tell eBay the postcode through this curl client (once per client)."""
    item_id, postcode = _wanted(client, url)
    if not item_id:
        return
    # Tried once per client: a failed call is not repeated before every item.
    remember_postcode(client, postcode)
    req_headers = dict(headers or {})
    req_headers.update({"Accept": "application/json", "X-Requested-With": "XMLHttpRequest", "Referer": url})
    try:
        resp = client.get(
            EBAY_AU_ORIGIN + rates_path(item_id, postcode),
            headers=req_headers,
            timeout=15,
            impersonate="chrome131",
        )
        logger.info("eBay AU deliver-to %s: HTTP %s", postcode, getattr(resp, "status_code", None))
    except Exception as exc:
        logger.info("eBay AU deliver-to %s failed: %s", postcode, exc)


_DRIVER_FETCH_JS = """
const done = arguments[arguments.length - 1];
fetch(arguments[0], {headers: {accept: 'application/json'}, credentials: 'include'})
  .then(r => done(r.status)).catch(e => done(String(e)));
"""


def ensure_driver_postcode(driver, url: str) -> None:
    """Tell eBay the postcode from this Chrome (once per browser).

    Needs the browser on an ebay.com.au page (the call uses that page's cookies).
    """
    item_id, postcode = _wanted(driver, url)
    if not item_id:
        return
    current = (getattr(driver, "current_url", "") or "").lower()
    if "ebay.com.au" not in current:
        return
    remember_postcode(driver, postcode)
    try:
        driver.set_script_timeout(20)
        status = driver.execute_async_script(_DRIVER_FETCH_JS, rates_path(item_id, postcode))
        logger.info("eBay AU deliver-to %s (browser): %s", postcode, status)
    except Exception as exc:
        logger.info("eBay AU deliver-to %s (browser) failed: %s", postcode, exc)
