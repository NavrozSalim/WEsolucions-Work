"""Vendor delivery cost to one postcode, for the Auto Purchasing check.

eBay AU: the item page's own postage calculator (the "Delivery to <postcode> Change"
link) loads ``/itemmodules/<item id>?module_groups=GET_RATES_MODAL...`` with the
country, postcode and quantity. Its JSON has the postage options for that postcode
("Free delivery in 2-4 days", "AU $5.99 delivery in 2-3 days", ...) or "Item doesn't
post to you". The first option is the one the buyer gets by default.

Amazon AU reads the delivery price on the product page itself (``amazon_au_scraper``,
after setting the order postcode). Costco AU prices include delivery. Costway AU uses
the feed price.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

EBAY_AU_RATES_PATH = (
    "/itemmodules/{item_id}?module_groups=GET_RATES_MODAL&co=0&isGetRates=1&rt=nc"
    "&quantity={qty}&shipToCountryCode=AUS&shippingZipCode={postcode}"
)
_ITEM_ID_RE = re.compile(r"/itm/(?:[^/?#]+/)?(\d{9,15})")
_AU_MONEY_RE = re.compile(r"(?:AU\s*)?\$\s*([0-9][0-9,]*\.[0-9]{2})")
_NO_POST = ("doesn't post", "does not post", "doesnt post", "won't be sent", "will not be sent",
            "can't post", "cannot post", "not available to")

# Quote result statuses
FREE = 'free'
PAID = 'paid'
NO_DELIVERY = 'no_delivery'
UNAVAILABLE = 'unavailable'


def ebay_item_id(url: str) -> str:
    m = _ITEM_ID_RE.search(url or '')
    return m.group(1) if m else ''


def _find_key(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = _find_key(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_key(v, key)
            if found is not None:
                return found
    return None


def _spans_text(value) -> str:
    spans = value.get('textSpans') if isinstance(value, dict) else None
    return ''.join(str(s.get('text') or '') for s in spans or [] if isinstance(s, dict)).strip()


def parse_ebay_rates(data) -> dict:
    """``{"status", "amount", "text"}`` from the GET_RATES_MODAL JSON."""
    deliveryto = _find_key(data, 'deliveryto')
    values = deliveryto.get('values') if isinstance(deliveryto, dict) else None
    lines = [t for t in (_spans_text(v) for v in values or []) if t]
    if not lines:
        return {'status': UNAVAILABLE, 'amount': None, 'text': 'eBay gave no postage for this postcode'}
    first = lines[0]
    low = first.lower().replace('’', "'")
    if any(p in low for p in _NO_POST):
        return {'status': NO_DELIVERY, 'amount': None, 'text': first}
    for line in lines:
        l = line.lower()
        if l.startswith('free'):
            return {'status': FREE, 'amount': 0.0, 'text': line}
        m = _AU_MONEY_RE.search(line)
        if m and ('deliver' in l or 'postage' in l or 'post' in l or l.startswith('au')):
            return {'status': PAID, 'amount': float(m.group(1).replace(',', '')), 'text': line}
    return {'status': UNAVAILABLE, 'amount': None, 'text': first[:200]}


def _fetch_json_via_driver(driver, path: str):
    script = """
        const done = arguments[arguments.length - 1];
        fetch(arguments[0], {headers: {accept: 'application/json'}, credentials: 'include'})
          .then(r => r.ok ? r.json() : {__status: r.status})
          .then(done).catch(e => done({__error: String(e)}));
    """
    current = (getattr(driver, 'current_url', '') or '').lower()
    if 'ebay.com.au' not in current:
        return None
    driver.set_script_timeout(25)
    data = driver.execute_async_script(script, path)
    return data if isinstance(data, dict) and '__status' not in data and '__error' not in data else None


def _fetch_json_via_http(session: dict, path: str):
    from .ebay_au_proxies import (
        acquire_proxy, bind_curl_client, proxies_configured, remember_proxy, session_proxy_assignment,
    )
    from .ebay_common import EBAY_MARKET_AU, EbayHTTP

    url = 'https://www.ebay.com.au' + path
    headers = EbayHTTP._get_headers(url, 'AU', session, EBAY_MARKET_AU)
    headers.update({'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest'})
    for attempt in range(2):
        assignment = None
        if proxies_configured():
            assignment = session_proxy_assignment(session) if attempt == 0 else None
            assignment = assignment or acquire_proxy(session, force_rotate=attempt > 0)
            remember_proxy(session, assignment)
        client = EbayHTTP._get_client(session, EBAY_MARKET_AU, proxy_url=assignment.url if assignment else None)
        if client is None:
            return None
        if assignment is not None:
            bind_curl_client(client, assignment)
        try:
            resp = client.get(url, headers=headers, timeout=25, impersonate='chrome131')
        except Exception as exc:
            logger.info('eBay AU postage quote request failed: %s', exc)
            continue
        if getattr(resp, 'status_code', None) == 200:
            try:
                return resp.json()
            except Exception:
                logger.info('eBay AU postage quote: response was not JSON')
                return None
        logger.info('eBay AU postage quote HTTP %s', getattr(resp, 'status_code', None))
    return None


def quote_ebay_au(url: str, postcode: str, qty: int, session: dict | None) -> dict:
    """eBay AU postage for ``qty`` of the item to ``postcode`` (the default postage option)."""
    from .ebay_au_fast import _DRIVER_KEY

    session = session if session is not None else {}
    item_id = ebay_item_id(url)
    postcode = re.sub(r'\D', '', postcode or '')
    if not item_id or len(postcode) != 4:
        return {'status': UNAVAILABLE, 'amount': None, 'text': 'No eBay item id or postcode'}
    path = EBAY_AU_RATES_PATH.format(item_id=item_id, qty=max(1, int(qty or 1)), postcode=postcode)
    data = None
    driver = session.get(_DRIVER_KEY)
    if driver is not None:
        try:
            data = _fetch_json_via_driver(driver, path)
        except Exception as exc:
            logger.info('eBay AU postage quote via browser failed: %s', exc)
    if data is None:
        try:
            data = _fetch_json_via_http(session, path)
        except Exception as exc:
            logger.info('eBay AU postage quote via HTTP failed: %s', exc)
    if data is None:
        return {'status': UNAVAILABLE, 'amount': None, 'text': 'Could not load eBay postage for this postcode'}
    result = parse_ebay_rates(data)
    logger.info('eBay AU postage item %s to %s x%s: %s %s', item_id, postcode, qty, result['status'], result['amount'])
    return result
