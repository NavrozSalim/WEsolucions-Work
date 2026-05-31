"""
Sears Marketplace API adapter.

Expected Store.api_token format (JSON):
{
  "seller_id": "10673110",
  "email": "seller@example.com",
  "secret_key": "base64-or-plain-secret",
  "location_id": "12345",
  "base_url": "https://seller.marketplace.sears.com/SellerPortal/api"
}

``location_id`` is required for FBM-LMP inventory (Seller Portal → Fulfillment Location).

Pricing: PUT /pricing/fbm/v6 (XML v6 — standard-price + optional sale block)
Inventory: PUT /inventory/fbm-lmp/v7 (FBM-LMP, default) or /inventory/fbm/v7 (legacy FBM)

After each feed PUT, polls the processing report until Sears accepts or rejects the feed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from xml.sax.saxutils import escape

import requests

from .base import BaseStoreAdapter

SEARS_API_BASE = "https://seller.marketplace.sears.com/SellerPortal/api"
PRICING_NS = "http://seller.marketplace.sears.com/pricing/v6"
INVENTORY_NS = "http://seller.marketplace.sears.com/inventory/v7"
STORE_INVENTORY_NS = "http://seller.marketplace.sears.com/catalog/v7"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
INVENTORY_XSD = (
    "https://seller.marketplace.sears.com/SellerPortal/s/schema/rest/"
    "inventory/import/v7/inventory.xsd"
)
STORE_INVENTORY_XSD = (
    "https://seller.marketplace.sears.com/SellerPortal/s/schema/rest/"
    "inventory/import/v7/store-inventory.xsd"
)
INVENTORY_PATH_LMP = "/inventory/fbm-lmp/v7"
INVENTORY_PATH_FBM = "/inventory/fbm/v7"
PROCESSING_REPORT_PATH = "/reports/v1/processing-report"
DEFAULT_SALE_DAYS = 365
REPORT_POLL_INTERVAL_SEC = 2
REPORT_POLL_MAX_ATTEMPTS = 15

logger = logging.getLogger(__name__)


class SearsAPIError(Exception):
    """Sears API call failed."""

    def __init__(self, message, status_code=None, response_body=None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


def _format_amount(amount) -> str:
    return str(
        Decimal(str(amount)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    )


def _xml_item_id(item_id: str) -> str:
    return escape(str(item_id).strip(), {'"': '&quot;', "'": '&apos;'})


def _xml_attr_value(value: str) -> str:
    return escape(str(value).strip(), {'"': '&quot;', "'": '&apos;'})


def parse_document_id(response_body: str) -> str | None:
    """Extract ``document-id`` from a Sears feed PUT response."""
    if not response_body:
        return None
    match = re.search(r'<document-id>(\d+)</document-id>', response_body)
    return match.group(1) if match else None


def processing_report_pending(response_body: str) -> bool:
    """True while Sears has accepted the feed but not finished processing."""
    if not response_body:
        return True
    if '<records-accepted>' in response_body or '<records-with-errors>' in response_body:
        return False
    return '<status>Submitted</status>' in response_body or '<report>' not in response_body


def parse_processing_report_summary(response_body: str) -> dict:
    """Parse accepted/error counts and messages from a processing report."""
    accepted = re.search(r'<records-accepted>(\d+)</records-accepted>', response_body or '')
    errors = re.search(r'<records-with-errors>(\d+)</records-with-errors>', response_body or '')
    status = re.search(r'<status>([^<]+)</status>', response_body or '')
    return {
        'status': status.group(1) if status else None,
        'accepted': int(accepted.group(1)) if accepted else 0,
        'errors': int(errors.group(1)) if errors else 0,
        'error_infos': re.findall(r'<error-info>([^<]+)</error-info>', response_body or ''),
    }


def build_pricing_feed_xml(
    item_id: str,
    *,
    standard_price,
    sale_price=None,
    sale_start_date: date | None = None,
    sale_end_date: date | None = None,
) -> str:
    """
    Sears pricing v6 XML for one Child SKU (``item-id``).

    When ``sale_price`` is set and below ``standard_price``, includes a ``<sale>`` block
    (posted price + RRP strike-through on Sears). Otherwise only ``standard-price``.
    """
    std = _format_amount(standard_price)
    iid = _xml_item_id(item_id)
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<pricing-feed xmlns="{PRICING_NS}">',
        '  <fbm-pricing>',
        f'    <item item-id="{iid}">',
        f'      <standard-price>{std}</standard-price>',
    ]
    use_sale = sale_price is not None
    if use_sale:
        posted = Decimal(std)
        sale_dec = Decimal(_format_amount(sale_price))
        use_sale = sale_dec < posted
    if use_sale:
        start = sale_start_date or date.today()
        end = sale_end_date or (start + timedelta(days=DEFAULT_SALE_DAYS))
        parts.extend([
            '      <sale>',
            f'        <sale-price>{_format_amount(sale_price)}</sale-price>',
            f'        <sale-start-date>{start.isoformat()}</sale-start-date>',
            f'        <sale-end-date>{end.isoformat()}</sale-end-date>',
            '      </sale>',
        ])
    parts.extend([
        '    </item>',
        '  </fbm-pricing>',
        '</pricing-feed>',
    ])
    return '\n'.join(parts)


def build_inventory_feed_xml(
    item_id: str,
    quantity: int,
    *,
    lmp: bool = True,
    location_id: str | None = None,
    pick_up_now_eligible: bool = False,
    inventory_timestamp: str | None = None,
) -> str:
    """
    Sears inventory v7 XML for one Child SKU.

    FBM-LMP (default): ``store-inventory`` root (``catalog/v7`` namespace) with per-location
    quantity → PUT /inventory/fbm-lmp/v7. Requires ``location_id`` (Seller Portal fulfillment
    location).

    Legacy FBM: ``inventory-feed`` + ``fbm-inventory`` → PUT /inventory/fbm/v7.
    """
    iid = _xml_item_id(item_id)
    qty = max(0, int(quantity))
    if lmp:
        loc = (location_id or "").strip()
        if not loc:
            raise SearsAPIError(
                "Sears FBM-LMP inventory requires location_id in store API credentials "
                "(Seller Portal → Account Settings → Fulfillment Location)."
            )
        loc_attr = _xml_attr_value(loc)
        pick = "true" if pick_up_now_eligible else "false"
        ts_line = ""
        if inventory_timestamp:
            ts_line = f"        <inventory-timestamp>{inventory_timestamp}</inventory-timestamp>\n"
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<store-inventory xmlns="{STORE_INVENTORY_NS}"\n'
            f'    xmlns:xsi="{XSI_NS}"\n'
            f'    xsi:schemaLocation="{STORE_INVENTORY_NS} {STORE_INVENTORY_XSD}">\n'
            f'  <item item-id="{iid}">\n'
            '    <locations>\n'
            f'      <location location-id="{loc_attr}">\n'
            f'        <quantity>{qty}</quantity>\n'
            f'        <pick-up-now-eligible>{pick}</pick-up-now-eligible>\n'
            f'{ts_line}'
            '      </location>\n'
            '    </locations>\n'
            '  </item>\n'
            '</store-inventory>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<inventory-feed xmlns="{INVENTORY_NS}"\n'
        f'    xmlns:xsi="{XSI_NS}"\n'
        f'    xsi:schemaLocation="{INVENTORY_NS} {INVENTORY_XSD}">\n'
        '  <fbm-inventory>\n'
        f'    <item item-id="{iid}">\n'
        f'      <quantity>{qty}</quantity>\n'
        '    </item>\n'
        '  </fbm-inventory>\n'
        '</inventory-feed>'
    )


class SearsAdapter(BaseStoreAdapter):
    """Sears API adapter: price (standard + sale/RRP) and inventory by Child SKU."""

    def __init__(self, store):
        super().__init__(store)
        self._session = requests.Session()
        self._creds = self._parse_credentials(self._token)
        self._seller_id = (self._creds.get("seller_id") or "").strip()
        self._email = (self._creds.get("email") or "").strip()
        self._secret_key = (self._creds.get("secret_key") or "").strip()
        self._base_url = (self._creds.get("base_url") or SEARS_API_BASE).rstrip("/")
        self._inventory_lmp = self._resolve_inventory_lmp()
        self._location_id = self._resolve_location_id()
        self._pick_up_now_eligible = self._resolve_pick_up_now_eligible()

    @staticmethod
    def _resolve_inventory_lmp_from_creds(creds: dict) -> bool:
        """True = FBM-LMP inventory API (default for newer Sears seller accounts)."""
        for key in ("inventory_program", "sears_inventory_program"):
            val = (creds.get(key) or "").strip().lower()
            if val in ("fbm", "legacy"):
                return False
            if val in ("fbm-lmp", "lmp", "fbm_lmp"):
                return True
        if creds.get("use_fbm_inventory") is True:
            return False
        if creds.get("use_lmp_inventory") is False:
            return False
        return True

    def _resolve_inventory_lmp(self) -> bool:
        return self._resolve_inventory_lmp_from_creds(self._creds)

    @staticmethod
    def _resolve_location_id_from_creds(creds: dict) -> str:
        for key in ("location_id", "sears_location_id", "warehouse_location_id"):
            val = (creds.get(key) or "").strip()
            if val:
                return val
        return ""

    def _resolve_location_id(self) -> str:
        return self._resolve_location_id_from_creds(self._creds)

    @staticmethod
    def _resolve_pick_up_now_eligible_from_creds(creds: dict) -> bool:
        for key in ("pick_up_now_eligible", "pickup_now_eligible"):
            if key in creds:
                return bool(creds.get(key))
        return False

    def _resolve_pick_up_now_eligible(self) -> bool:
        return self._resolve_pick_up_now_eligible_from_creds(self._creds)

    @staticmethod
    def _parse_credentials(raw_token):
        if not raw_token:
            return {}
        txt = str(raw_token).strip()
        if txt.startswith("{") and txt.endswith("}"):
            try:
                data = json.loads(txt)
                if isinstance(data, dict):
                    return data
            except Exception:
                return {}
        return {}

    def _has_minimum_creds(self):
        return bool(self._seller_id and self._email and self._secret_key)

    def _signature(self, timestamp):
        payload = f"{self._seller_id}:{self._email}:{timestamp}".encode("utf-8")
        key = self._secret_key.encode("utf-8")
        return hmac.new(key, payload, hashlib.sha256).hexdigest()

    def _headers(self, timestamp):
        sig = self._signature(timestamp)
        return {
            "Authorization": (
                f"HMAC-SHA256 emailaddress={self._email},"
                f"timestamp={timestamp},signature={sig}"
            ),
            "Accept": "application/xml",
        }

    def _request(self, method, path, *, params=None, data=None, timeout=30):
        from django.utils import timezone

        timestamp = timezone.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        url = f"{self._base_url}{path}"
        headers = self._headers(timestamp)
        if data is not None:
            headers["Content-Type"] = "application/xml"
        try:
            resp = self._session.request(
                method, url, params=params, data=data, headers=headers, timeout=timeout
            )
        except requests.RequestException as exc:
            raise SearsAPIError(str(exc))
        if resp.status_code >= 400:
            raise SearsAPIError(
                f"Sears API {method} {path}: {resp.status_code}",
                status_code=resp.status_code,
                response_body=resp.text[:500] if resp.text else None,
            )
        return resp.text or ""

    def _wait_for_processing_report(self, document_id: str) -> str:
        """Poll until Sears finishes async feed processing or raise."""
        path = f"{PROCESSING_REPORT_PATH}/{document_id}"
        params = {"sellerId": self._seller_id}
        last_body = ""
        for attempt in range(REPORT_POLL_MAX_ATTEMPTS):
            if attempt:
                time.sleep(REPORT_POLL_INTERVAL_SEC)
            last_body = self._request("GET", path, params=params)
            if not processing_report_pending(last_body):
                break
        else:
            raise SearsAPIError(
                f"Sears processing report timed out for document {document_id}",
                response_body=last_body[:500] if last_body else None,
            )

        summary = parse_processing_report_summary(last_body)
        if summary["errors"] > 0 or summary["accepted"] < 1:
            detail = summary["error_infos"][0] if summary["error_infos"] else "feed rejected"
            raise SearsAPIError(
                f"Sears feed rejected (document {document_id}): {detail[:400]}",
                response_body=last_body[:500] if last_body else None,
            )
        return last_body

    def _put_feed_and_verify(self, path: str, xml: str, *, feed_label: str) -> str:
        """PUT XML feed, then poll processing report until accepted or failed."""
        resp = self._request(
            "PUT",
            path,
            params={"sellerId": self._seller_id},
            data=xml,
        )
        document_id = parse_document_id(resp)
        if not document_id:
            raise SearsAPIError(
                f"Sears {feed_label} PUT did not return document-id",
                response_body=resp[:500] if resp else None,
            )
        self._wait_for_processing_report(document_id)
        return document_id

    def validate_connection(self):
        if not self._has_minimum_creds():
            return False
        try:
            self._request(
                "GET",
                "/oms/purchaseorder/v19",
                params={"sellerId": self._seller_id, "status": "New"},
            )
            return True
        except SearsAPIError:
            return False

    def lookup_listing_by_sku(self, sku: str):
        """Child SKU is the Sears ``item-id`` for price/inventory feeds."""
        return str(sku).strip() if sku else None

    def create_product(self, sku, title, price, stock, **kwargs):
        raise NotImplementedError(
            "Sears create_product requires your finalized Sears listing upload template format."
        )

    def update_product(self, external_id, **kwargs):
        """
        Update listing by Marketplace Child SKU (``item-id``).

        kwargs:
            price: posted/sale price (``store_price``)
            rrp: standard price for strike-through (computed RRP)
            stock: inventory quantity

        When pricing succeeds but inventory fails, pricing is kept and
        ``last_inventory_warning`` is set — push still returns True.

        Pricing and inventory PUTs wait for Sears processing reports; rejected feeds
        raise ``SearsAPIError`` (inventory-only failure after successful pricing
        becomes a warning, not a hard failure).
        """
        self.last_inventory_warning = None
        sku = str(external_id or "").strip()
        if not sku:
            raise SearsAPIError("Missing Sears Child SKU (item-id) for update_product")
        price = kwargs.get("price")
        rrp = kwargs.get("rrp")
        stock = kwargs.get("stock")
        price_updated = False

        if price is not None:
            posted = _format_amount(price)
            if rrp is not None:
                std = _format_amount(rrp)
                if Decimal(std) > Decimal(posted):
                    self.update_pricing(
                        sku,
                        standard_price=std,
                        sale_price=posted,
                    )
                else:
                    self.update_pricing(sku, standard_price=posted)
            else:
                self.update_pricing(sku, standard_price=posted)
            price_updated = True

        if stock is not None:
            try:
                self.update_inventory(sku, stock)
            except SearsAPIError as exc:
                if not price_updated:
                    raise
                warn = f'Price updated on Sears; inventory not updated ({exc})'
                self.last_inventory_warning = warn
                logger.warning('Sears inventory push failed for %s after pricing OK: %s', sku, exc)
        return True

    def update_pricing(
        self,
        item_id: str,
        *,
        standard_price,
        sale_price=None,
        sale_start_date: date | None = None,
        sale_end_date: date | None = None,
    ):
        xml = build_pricing_feed_xml(
            item_id,
            standard_price=standard_price,
            sale_price=sale_price,
            sale_start_date=sale_start_date,
            sale_end_date=sale_end_date,
        )
        self._put_feed_and_verify("/pricing/fbm/v6", xml, feed_label="pricing")
        return True

    def update_price(self, sku, price, currency="USD"):
        """Legacy single-price helper — sets standard-price only."""
        del currency
        return self.update_pricing(str(sku), standard_price=price)

    def update_inventory(self, external_id, stock):
        from django.utils import timezone

        sku = str(external_id or "").strip()
        if not sku:
            raise SearsAPIError("Missing Sears Child SKU (item-id) for update_inventory")
        lmp = self._inventory_lmp
        ts = timezone.now().strftime("%Y-%m-%dT%H:%M:%S")
        xml = build_inventory_feed_xml(
            sku,
            stock,
            lmp=lmp,
            location_id=self._location_id if lmp else None,
            pick_up_now_eligible=self._pick_up_now_eligible,
            inventory_timestamp=ts if lmp else None,
        )
        path = INVENTORY_PATH_LMP if lmp else INVENTORY_PATH_FBM
        self._put_feed_and_verify(path, xml, feed_label="inventory")
        return True

    def delete_product(self, external_id):
        raise NotImplementedError("Sears delete_product not implemented yet.")
