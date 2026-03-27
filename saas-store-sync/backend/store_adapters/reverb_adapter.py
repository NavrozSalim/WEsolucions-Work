"""
Reverb API adapter for Wesolutions.
Uses Bearer token auth. Base URL: api.reverb.com (or sandbox.reverb.com for testing).
"""
import logging
from decimal import Decimal

import requests

from .base import BaseStoreAdapter

logger = logging.getLogger(__name__)

REVERB_API_BASE = "https://api.reverb.com"
REVERB_SANDBOX_BASE = "https://sandbox.reverb.com"

HEADERS = {
    "Content-Type": "application/hal+json",
    "Accept": "application/hal+json",
    "Accept-Version": "3.0",
}


class ReverbAPIError(Exception):
    """Reverb API call failed."""
    def __init__(self, message, status_code=None, response_body=None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class ReverbAdapter(BaseStoreAdapter):
    """Reverb API adapter. Update listings, end listings, lookup by SKU."""

    def __init__(self, store):
        super().__init__(store)
        self._base_url = REVERB_SANDBOX_BASE if getattr(store, 'use_sandbox', False) else REVERB_API_BASE
        self._session = requests.Session()
        self._session.headers.update(HEADERS)
        if self._token:
            self._session.headers["Authorization"] = f"Bearer {self._token}"

    def _request(self, method, path, json=None, timeout=30):
        url = f"{self._base_url}{path}"
        try:
            resp = self._session.request(method, url, json=json, timeout=timeout)
        except requests.RequestException as e:
            raise ReverbAPIError(str(e))
        if resp.status_code >= 400:
            raise ReverbAPIError(
                f"Reverb API {method} {path}: {resp.status_code}",
                status_code=resp.status_code,
                response_body=resp.text[:500] if resp.text else None,
            )
        return resp.json() if resp.text else None

    def validate_connection(self):
        """Validate API token by calling /api/shop."""
        if not self._token or len(str(self._token)) < 10:
            return False
        try:
            self._request("GET", "/api/shop")
            return True
        except ReverbAPIError:
            return False

    def lookup_listing_by_sku(self, sku: str) -> str | None:
        """Find listing ID by SKU. Returns listing ID (UUID) or None."""
        try:
            data = self._request("GET", f"/api/my/listings?sku={requests.utils.quote(sku)}&state=all")
        except ReverbAPIError:
            return None
        if not data:
            return None
        listings = data.get("listings")
        if not listings and isinstance(data.get("_embedded"), dict):
            listings = data["_embedded"].get("listings")
        if isinstance(listings, list) and listings:
            first = listings[0]
            lid = first.get("id") or first.get("uuid") if isinstance(first, dict) else None
            return str(lid) if lid else None
        return None

    def create_product(self, sku, title, price, stock, **kwargs):
        """Create listing. v1: deferred; use manual create then sync with Marketplace ID."""
        raise NotImplementedError("Reverb create_product: v1 focuses on updating existing listings")

    def update_product(self, external_id, price=None, stock=None, **kwargs):
        """Update listing price and/or inventory. PUT /api/listings/{id}."""
        body = {}
        if price is not None:
            amt = str(Decimal(str(price)).quantize(Decimal("0.01")))
            body["price"] = {"amount": amt, "currency": kwargs.get("currency", "USD")}
        if stock is not None:
            body["inventory"] = max(0, int(stock))
            body["has_inventory"] = True
        if not body:
            return None
        self._request("PUT", f"/api/listings/{external_id}", json=body)
        return True

    def update_inventory(self, external_id, stock):
        """Update only stock."""
        return self.update_product(external_id, stock=stock)

    def delete_product(self, external_id):
        """End listing on Reverb. PUT /api/my/listings/{id}/state/end."""
        self._request(
            "PUT",
            f"/api/my/listings/{external_id}/state/end",
            json={"reason": "not_sold"},
        )
        return True
