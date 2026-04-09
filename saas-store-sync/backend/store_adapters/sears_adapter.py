"""
Sears Marketplace API adapter.

Expected Store.api_token format (JSON):
{
  "seller_id": "10673110",
  "email": "seller@example.com",
  "secret_key": "base64-or-plain-secret",
  "base_url": "https://seller.marketplace.sears.com/SellerPortal/api"
}
"""
import hashlib
import hmac
import json
from decimal import Decimal

import requests

from .base import BaseStoreAdapter

SEARS_API_BASE = "https://seller.marketplace.sears.com/SellerPortal/api"


class SearsAPIError(Exception):
    """Sears API call failed."""

    def __init__(self, message, status_code=None, response_body=None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class SearsAdapter(BaseStoreAdapter):
    """Sears API adapter for validating credentials and updating price/inventory."""

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
        """
        Validate Sears credentials by calling purchaseorder endpoint with a valid status.
        """
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
        """
        Sears SKU lookup endpoint isn't wired yet in v1.
        Use marketplace_id from catalog mapping when available.
        """
        return str(sku) if sku else None

    def create_product(self, sku, title, price, stock, **kwargs):
        raise NotImplementedError(
            "Sears create_product requires your finalized Sears listing upload template format."
        )

    def update_product(self, external_id, **kwargs):
        if not external_id:
            raise SearsAPIError("Missing Sears external_id/SKU for update_product")
        price = kwargs.get("price")
        stock = kwargs.get("stock")
        if price is not None:
            amt = str(Decimal(str(price)).quantize(Decimal("0.01")))
            self.update_price(str(external_id), amt, kwargs.get("currency", "USD"))
        if stock is not None:
            self.update_inventory(str(external_id), stock)
        return True

    def update_price(self, sku, price, currency="USD"):
        xml = (
            f"<priceFeed><sku>{sku}</sku><price currency=\"{currency}\">{price}</price>"
            "</priceFeed>"
        )
        self._request(
            "PUT",
            "/pricing/fbm/v6",
            params={"sellerId": self._seller_id},
            data=xml,
        )
        return True

    def update_inventory(self, external_id, stock):
        # Inventory endpoint can vary by Sears account setup; keep explicit for later template step.
        raise NotImplementedError(
            "Sears inventory endpoint payload will be wired after you share your Sears inventory template."
        )

    def delete_product(self, external_id):
        raise NotImplementedError("Sears delete_product not implemented yet.")
