"""
Walmart Marketplace API (v3) adapter.

OAuth (recommended): store ``client_id`` + ``client_secret`` from Seller Center.
The adapter obtains a short-lived access token via ``POST /v3/token`` and sends it
on every call as ``WM_SEC.ACCESS_TOKEN`` with ``Authorization: Basic …``.

Expected Store.api_token JSON example:
{
  "client_id": "...",
  "client_secret": "...",
  "channel_type": "...",
  "base_url": "https://marketplace.walmartapis.com"
}

Legacy signed auth (optional): ``consumer_id``, ``private_key_pem``.
"""
import base64
import json
import os
import time
import uuid
from decimal import Decimal
from urllib.parse import quote

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
        return {"access_token": txt}

    def _client_credentials(self):
        client_id = self._creds.get("client_id") or os.getenv("WALMART_CLIENT_ID")
        client_secret = self._creds.get("client_secret") or os.getenv("WALMART_CLIENT_SECRET")
        return client_id, client_secret

    def _basic_auth_header(self):
        client_id, client_secret = self._client_credentials()
        if not client_id or not client_secret:
            return None
        auth = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("utf-8")
        return f"Basic {auth}"

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
        client_id, client_secret = self._client_credentials()
        if not client_id or not client_secret:
            if self._access_token:
                return self._access_token
            raise WalmartAPIError("Missing Walmart client_id/client_secret for token refresh")

        auth = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("utf-8")
        headers = {
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "WM_QOS.CORRELATION_ID": str(uuid.uuid4()),
            "WM_SVC.NAME": WALMART_SERVICE_NAME,
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
        }
        basic = self._basic_auth_header()
        channel_type = self._channel_type()
        if channel_type:
            headers["WM_CONSUMER.CHANNEL.TYPE"] = channel_type

        consumer_id = self._consumer_id()
        if consumer_id:
            headers["WM_CONSUMER.ID"] = consumer_id

        if basic:
            headers["Authorization"] = basic
            headers["WM_SEC.ACCESS_TOKEN"] = token
        else:
            signature = self._build_signature(full_url, method, timestamp_ms)
            if signature:
                headers["WM_SEC.TIMESTAMP"] = timestamp_ms
                headers["WM_SEC.AUTH_SIGNATURE"] = signature
                headers["WM_SEC.ACCESS_TOKEN"] = token
            else:
                headers["Authorization"] = f"Bearer {token}"
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
        """Validate OAuth credentials via a lightweight inventory list call."""
        try:
            self._request("GET", "/v3/inventories?limit=1")
            return True
        except WalmartAPIError:
            client_id, client_secret = self._client_credentials()
            if not client_id or not client_secret:
                token = (self._access_token or "").strip()
                return len(token) > 20
            return False

    def lookup_listing_by_sku(self, sku: str):
        """Verify seller SKU exists in the Walmart catalog."""
        if not sku:
            return None
        sku_q = quote(str(sku), safe="")
        try:
            self._request("GET", f"/v3/items?sku={sku_q}")
            return str(sku)
        except WalmartAPIError:
            return None

    def create_product(self, sku, title, price, stock, **kwargs):
        raise NotImplementedError(
            "Walmart create_product requires your finalized Walmart item payload/upload format."
        )

    def update_product(self, external_id, **kwargs):
        """
        Update listing by seller SKU.
        - price: ``PUT /v3/price``
        - stock: ``PUT /v3/inventory``
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
                            "currentPrice": {
                                "currency": kwargs.get("currency", "USD"),
                                "amount": amt,
                            },
                        }
                    ],
                },
            )
        if stock is not None:
            self.update_inventory(sku, stock)
        return True

    def update_inventory(self, external_id, stock):
        """Update Walmart inventory for a single SKU at the default ship node."""
        if not external_id:
            raise WalmartAPIError("Missing Walmart external_id/SKU for update_inventory")
        sku = str(external_id)
        qty = max(0, int(stock or 0))
        self._request(
            "PUT",
            "/v3/inventory",
            json_body={"sku": sku, "quantity": {"unit": "EACH", "amount": qty}},
        )
        return True

    def delete_product(self, external_id):
        """Retire/delete Walmart item by SKU."""
        if not external_id:
            raise WalmartAPIError("Missing Walmart external_id/SKU for delete_product")
        sku = str(external_id)
        self._request("DELETE", f"/v3/items/{quote(sku, safe='')}")
        return True
