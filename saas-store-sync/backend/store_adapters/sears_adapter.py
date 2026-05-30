"""
Sears Marketplace API adapter.

Expected Store.api_token format (JSON):
{
  "seller_id": "10673110",
  "email": "seller@example.com",
  "secret_key": "base64-or-plain-secret",
  "base_url": "https://seller.marketplace.sears.com/SellerPortal/api"
}

Pricing: PUT /pricing/fbm/v6 (XML v6 — standard-price + optional sale block)
Inventory: PUT /inventory/fbm/v7 (XML v7 — quantity per item-id / Child SKU)
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from xml.sax.saxutils import escape

import requests

from .base import BaseStoreAdapter

SEARS_API_BASE = "https://seller.marketplace.sears.com/SellerPortal/api"
PRICING_NS = "http://seller.marketplace.sears.com/pricing/v6"
INVENTORY_NS = "http://seller.marketplace.sears.com/inventory/v7"
DEFAULT_SALE_DAYS = 365


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


def build_inventory_feed_xml(item_id: str, quantity: int) -> str:
    """Sears inventory v7 XML for one Child SKU."""
    iid = _xml_item_id(item_id)
    qty = max(0, int(quantity))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<inventory-feed xmlns="{INVENTORY_NS}">\n'
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
        """
        sku = str(external_id or "").strip()
        if not sku:
            raise SearsAPIError("Missing Sears Child SKU (item-id) for update_product")
        price = kwargs.get("price")
        rrp = kwargs.get("rrp")
        stock = kwargs.get("stock")

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
        if stock is not None:
            self.update_inventory(sku, stock)
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
        self._request(
            "PUT",
            "/pricing/fbm/v6",
            params={"sellerId": self._seller_id},
            data=xml,
        )
        return True

    def update_price(self, sku, price, currency="USD"):
        """Legacy single-price helper — sets standard-price only."""
        del currency
        return self.update_pricing(str(sku), standard_price=price)

    def update_inventory(self, external_id, stock):
        sku = str(external_id or "").strip()
        if not sku:
            raise SearsAPIError("Missing Sears Child SKU (item-id) for update_inventory")
        xml = build_inventory_feed_xml(sku, stock)
        self._request(
            "PUT",
            "/inventory/fbm/v7",
            params={"sellerId": self._seller_id},
            data=xml,
        )
        return True

    def delete_product(self, external_id):
        raise NotImplementedError("Sears delete_product not implemented yet.")
