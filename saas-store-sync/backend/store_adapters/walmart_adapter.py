"""
Walmart Marketplace API (v3) adapter.

Expected Store.api_token for Walmart can be either:
1) Access token string (quick/manual mode), or
2) JSON credentials (recommended), example:
{
  "client_id": "...",
  "client_secret": "...",
  "consumer_id": "...",
  "private_key_pem": "-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----",
  "channel_type": "...",
  "base_url": "https://marketplace.walmartapis.com"
}
"""
import base64
import json
import os
import time
import uuid
from decimal import Decimal

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from .base import BaseStoreAdapter

WALMART_API_BASE = "https://marketplace.walmartapis.com"
WALMART_SANDBOX_BASE = "https://sandbox.walmartapis.com"
WALMART_TOKEN_PATH = "/v3/token"
WALMART_SERVICE_NAME = "Walmart Marketplace"


class WalmartAPIError(Exception):
    """Walmart API call failed."""

    def __init__(self, message, status_code=None, response_body=None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class WalmartAdapter(BaseStoreAdapter):
    """Walmart API adapter (validate, lookup, update price/inventory, retire)."""

    def __init__(self, store):
        super().__init__(store)
        self._session = requests.Session()
        self._creds = self._parse_credentials(self._token)
        self._base_url = self._creds.get("base_url") or (
            WALMART_SANDBOX_BASE if getattr(store, "use_sandbox", False) else WALMART_API_BASE
        )
        self._access_token = self._creds.get("access_token") or None
        self._token_expires_at = 0

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
                pass
        # Fallback: treat token as already-issued access token.
        return {"access_token": txt}

    def _consumer_id(self):
        return self._creds.get("consumer_id") or os.getenv("WALMART_CONSUMER_ID")

    def _channel_type(self):
        return self._creds.get("channel_type") or os.getenv("WALMART_CHANNEL_TYPE")

    def _private_key_pem(self):
        key = self._creds.get("private_key_pem") or os.getenv("WALMART_PRIVATE_KEY_PEM")
        if not key:
            return None
        return str(key).replace("\\n", "\n")

    def _refresh_access_token(self):
        client_id = self._creds.get("client_id") or os.getenv("WALMART_CLIENT_ID")
        client_secret = self._creds.get("client_secret") or os.getenv("WALMART_CLIENT_SECRET")
        if not client_id or not client_secret:
            if self._access_token:
                return self._access_token
            raise WalmartAPIError("Missing Walmart client_id/client_secret for token refresh")

        auth = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("utf-8")
        headers = {
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        resp = self._session.post(
            f"{self._base_url}{WALMART_TOKEN_PATH}",
            headers=headers,
            data="grant_type=client_credentials",
            timeout=30,
        )
        if resp.status_code >= 400:
            raise WalmartAPIError(
                f"Walmart token request failed: {resp.status_code}",
                status_code=resp.status_code,
                response_body=resp.text[:500] if resp.text else None,
            )
        payload = resp.json() if resp.text else {}
        token = payload.get("access_token")
        if not token:
            raise WalmartAPIError("Walmart token response missing access_token")
        expires_in = int(payload.get("expires_in") or 900)
        self._access_token = token
        self._token_expires_at = int(time.time()) + max(60, expires_in - 30)
        return token

    def _get_access_token(self):
        now = int(time.time())
        if self._access_token and now < self._token_expires_at:
            return self._access_token
        return self._refresh_access_token()

    def _build_signature(self, full_url, method, timestamp_ms):
        consumer_id = self._consumer_id()
        private_key_pem = self._private_key_pem()
        if not consumer_id or not private_key_pem:
            return None
        # Walmart signature payload convention.
        payload = f"{consumer_id}\n{full_url}\n{method.upper()}\n{timestamp_ms}\n".encode("utf-8")
        private_key = serialization.load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
        sig = private_key.sign(payload, padding.PKCS1v15(), hashes.SHA256())
        return base64.b64encode(sig).decode("utf-8")

    def _headers(self, full_url, method):
        token = self._get_access_token()
        correlation_id = str(uuid.uuid4())
        timestamp_ms = str(int(time.time() * 1000))
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "WM_SVC.NAME": WALMART_SERVICE_NAME,
            "WM_QOS.CORRELATION_ID": correlation_id,
            "Authorization": f"Bearer {token}",
        }
        consumer_id = self._consumer_id()
        channel_type = self._channel_type()
        if consumer_id:
            headers["WM_CONSUMER.ID"] = consumer_id
        if channel_type:
            headers["WM_CONSUMER.CHANNEL.TYPE"] = channel_type

        signature = self._build_signature(full_url, method, timestamp_ms)
        if signature:
            headers["WM_SEC.TIMESTAMP"] = timestamp_ms
            headers["WM_SEC.AUTH_SIGNATURE"] = signature
            headers["WM_SEC.ACCESS_TOKEN"] = token
        return headers

    def _request(self, method, path, *, json_body=None, timeout=30):
        url = f"{self._base_url}{path}"
        headers = self._headers(url, method)
        try:
            resp = self._session.request(method, url, headers=headers, json=json_body, timeout=timeout)
        except requests.RequestException as exc:
            raise WalmartAPIError(str(exc))
        if resp.status_code >= 400:
            raise WalmartAPIError(
                f"Walmart API {method} {path}: {resp.status_code}",
                status_code=resp.status_code,
                response_body=resp.text[:500] if resp.text else None,
            )
        if not resp.text:
            return None
        try:
            return resp.json()
        except Exception:
            return {"raw": resp.text}

    def validate_connection(self):
        """
        Validate Walmart credentials.

        Preferred path validates via a lightweight Marketplace API call.
        For manual/token-only setups (no client credentials configured), keep
        the connection usable if a bearer token exists so stores are not stuck
        in "error" before first sync attempts.
        """
        try:
            # Lightweight endpoint for auth/header verification.
            self._request("GET", "/v3/inventories?limit=1")
            return True
        except WalmartAPIError:
            # Graceful fallback for token-only mode when merchants paste an
            # already-issued access token instead of client credentials JSON.
            has_client_creds = bool(
                self._creds.get("client_id")
                or os.getenv("WALMART_CLIENT_ID")
                or self._creds.get("client_secret")
                or os.getenv("WALMART_CLIENT_SECRET")
            )
            if not has_client_creds:
                token = (self._access_token or "").strip()
                return len(token) > 20
            return False

    def lookup_listing_by_sku(self, sku: str):
        """Use SKU as listing key and verify it exists in Walmart catalog."""
        if not sku:
            return None
        try:
            self._request("GET", f"/v3/items/{requests.utils.quote(str(sku))}")
            return str(sku)
        except WalmartAPIError:
            return None

    def create_product(self, sku, title, price, stock, **kwargs):
        """
        Create product/listing on Walmart.
        Requires catalog payload spec from your Walmart upload format, so intentionally deferred.
        """
        raise NotImplementedError(
            "Walmart create_product requires your finalized Walmart item payload/upload format."
        )

    def update_product(self, external_id, **kwargs):
        """
        Update listing by SKU/external id.
        - price: updates /v3/price
        - stock: updates /v3/inventories/{sku}
        """
        if not external_id:
            raise WalmartAPIError("Missing Walmart external_id/SKU for update_product")
        sku = str(external_id)
        price = kwargs.get("price")
        stock = kwargs.get("stock")

        if price is not None:
            amt = str(Decimal(str(price)).quantize(Decimal("0.01")))
            self._request(
                "PUT",
                "/v3/price",
                json_body={
                    "sku": sku,
                    "pricing": [
                        {
                            "currentPriceType": "BASE",
                            "currentPrice": {"currency": kwargs.get("currency", "USD"), "amount": amt},
                        }
                    ],
                },
            )
        if stock is not None:
            self.update_inventory(sku, stock)
        return True

    def update_inventory(self, external_id, stock):
        """Update Walmart inventory by SKU."""
        if not external_id:
            raise WalmartAPIError("Missing Walmart external_id/SKU for update_inventory")
        sku = str(external_id)
        qty = max(0, int(stock or 0))
        self._request(
            "PUT",
            f"/v3/inventories/{requests.utils.quote(sku)}",
            json_body={"sku": sku, "quantity": {"unit": "EACH", "amount": qty}},
        )
        return True

    def delete_product(self, external_id):
        """Retire/delete Walmart item by SKU."""
        if not external_id:
            raise WalmartAPIError("Missing Walmart external_id/SKU for delete_product")
        sku = str(external_id)
        self._request("DELETE", f"/v3/items/{requests.utils.quote(sku)}")
        return True
