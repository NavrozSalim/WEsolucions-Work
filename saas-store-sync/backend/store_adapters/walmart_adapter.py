"""
Walmart Marketplace API (v3) adapter.

Only ``client_id`` and ``client_secret`` are required in Store.api_token JSON.
The adapter obtains/refreshes OAuth tokens, sets channel headers automatically,
and picks the correct inventory API (default node or ship-node) when needed.
Lag time (days to ship) is pushed via ``POST /v3/feeds?feedType=lagtime``.

Optional overrides in api_token JSON:
  ``ship_node`` — force a fulfillment center id
  ``channel_type`` — override WM_CONSUMER.CHANNEL.TYPE (defaults to client_id)
"""
import base64
import json
import logging
import os
import time
import uuid
from decimal import Decimal
from urllib.parse import quote

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from .base import BaseStoreAdapter

logger = logging.getLogger(__name__)

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
        self._ship_node_by_sku: dict[str, str] = {}

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

    def _use_store_credentials_only(self) -> bool:
        """True during store create/update validation — do not fall back to env vars."""
        return bool(getattr(self, '_validate_using_store_json_only', False))

    def _client_credentials(self):
        if self._use_store_credentials_only():
            client_id = (self._creds.get("client_id") or "").strip() or None
            client_secret = (self._creds.get("client_secret") or "").strip() or None
            return client_id, client_secret
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
        if self._use_store_credentials_only():
            cid = self._creds.get("consumer_id")
            return str(cid).strip() if cid else None
        return self._creds.get("consumer_id") or os.getenv("WALMART_CONSUMER_ID")

    def _channel_type(self):
        if self._use_store_credentials_only():
            explicit = self._creds.get("channel_type")
            if explicit:
                return str(explicit).strip()
            client_id, _ = self._client_credentials()
            return str(client_id).strip() if client_id else None
        explicit = self._creds.get("channel_type") or os.getenv("WALMART_CHANNEL_TYPE")
        if explicit:
            return str(explicit).strip()
        client_id, _ = self._client_credentials()
        return str(client_id).strip() if client_id else None

    def _configured_ship_node(self):
        node = (
            self._creds.get("ship_node")
            or self._creds.get("shipNode")
            or os.getenv("WALMART_SHIP_NODE")
        )
        return str(node).strip() if node else None

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

    @staticmethod
    def _extract_ship_nodes(payload) -> list[str]:
        if not isinstance(payload, dict):
            return []
        nodes = payload.get("nodes")
        if isinstance(nodes, list):
            out = [str(n.get("shipNode")).strip() for n in nodes if isinstance(n, dict) and n.get("shipNode")]
            if out:
                return out
        inv = payload.get("inventories")
        if isinstance(inv, dict):
            inv_nodes = inv.get("nodes")
            if isinstance(inv_nodes, list):
                return [
                    str(n.get("shipNode")).strip()
                    for n in inv_nodes
                    if isinstance(n, dict) and n.get("shipNode")
                ]
        return []

    def _resolve_ship_node(self, sku: str) -> str | None:
        sku_key = str(sku)
        configured = self._configured_ship_node()
        if configured:
            return configured
        cached = self._ship_node_by_sku.get(sku_key)
        if cached:
            return cached
        sku_q = quote(sku_key, safe="")
        try:
            payload = self._request("GET", f"/v3/inventories/{sku_q}")
        except WalmartAPIError as exc:
            logger.debug("Walmart ship node lookup failed for %s: %s", sku_key, exc)
            return None
        nodes = self._extract_ship_nodes(payload)
        if not nodes:
            return None
        self._ship_node_by_sku[sku_key] = nodes[0]
        return nodes[0]

    def _update_inventory_default_node(self, sku: str, qty: int):
        self._request(
            "PUT",
            "/v3/inventory",
            json_body={"sku": sku, "quantity": {"unit": "EACH", "amount": qty}},
        )

    def _update_inventory_ship_node(self, sku: str, qty: int, ship_node: str):
        sku_q = quote(str(sku), safe="")
        self._request(
            "PUT",
            f"/v3/inventories/{sku_q}",
            json_body={
                "inventories": {
                    "nodes": [
                        {
                            "shipNode": str(ship_node),
                            "inputQty": {"unit": "EACH", "amount": qty},
                        }
                    ]
                }
            },
        )

    def validate_connection(self):
        """Validate OAuth credentials via token exchange and a lightweight inventory list call."""
        self._validate_using_store_json_only = True
        self._access_token = None
        self._token_expires_at = 0
        client_id, client_secret = self._client_credentials()
        if not client_id or not client_secret:
            return False
        try:
            self._request("GET", "/v3/inventories?limit=1")
            return True
        except WalmartAPIError:
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

    def get_lag_time(self, sku: str, *, ship_node: str | None = None) -> int | None:
        """Return configured fulfillmentLagTime for a SKU (optional ship node)."""
        if not sku:
            return None
        sku_q = quote(str(sku), safe="")
        path = f"/v3/lagtime?sku={sku_q}"
        node = (str(ship_node).strip() if ship_node else "") or self._configured_ship_node()
        if node:
            path += f"&shipNode={quote(node, safe='')}"
        try:
            payload = self._request("GET", path)
        except WalmartAPIError:
            return None
        if isinstance(payload, dict):
            val = payload.get("fulfillmentLagTime")
            if val is not None:
                return int(val)
        return None

    def update_lag_time(self, sku: str, days: int, *, ship_node: str | None = None) -> str | None:
        """Submit lag-time feed for one SKU. Returns feedId when present."""
        if not sku:
            raise WalmartAPIError("Missing Walmart SKU for update_lag_time")
        entry = {"sku": str(sku), "fulfillmentLagTime": max(0, int(days))}
        listing_node = (str(ship_node).strip() if ship_node else "") or None
        configured = self._configured_ship_node()
        node = listing_node or configured
        if node:
            entry["shipNode"] = node
        payload = self._request(
            "POST",
            "/v3/feeds?feedType=lagtime",
            json_body={"lagTime": [entry]},
        )
        if isinstance(payload, dict):
            return payload.get("feedId") or payload.get("feedID")
        return None

    def update_product(self, external_id, **kwargs):
        """
        Update listing by seller SKU.
        - price: ``PUT /v3/price``
        - stock: ship-node or default inventory API
        - lag_time: ``POST /v3/feeds?feedType=lagtime``
        """
        if not external_id:
            raise WalmartAPIError("Missing Walmart external_id/SKU for update_product")
        sku = str(external_id)
        price = kwargs.get("price")
        stock = kwargs.get("stock")
        lag_time = kwargs.get("lag_time")

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
            self.update_inventory(sku, stock, ship_node=kwargs.get('ship_node'))
        if lag_time is not None:
            feed_id = self.update_lag_time(
                sku,
                lag_time,
                ship_node=kwargs.get('ship_node'),
            )
            if feed_id:
                logger.info(
                    "Walmart lag time feed submitted sku=%s days=%s feedId=%s",
                    sku,
                    lag_time,
                    feed_id,
                )
        return True

    def update_inventory(self, external_id, stock, *, ship_node=None):
        """Update Walmart inventory — per-listing ship node, store default, or auto-fallback."""
        if not external_id:
            raise WalmartAPIError("Missing Walmart external_id/SKU for update_inventory")
        sku = str(external_id)
        qty = max(0, int(stock or 0))
        listing_node = (str(ship_node).strip() if ship_node else '') or None
        if listing_node:
            self._update_inventory_ship_node(sku, qty, listing_node)
            return True
        configured = self._configured_ship_node()
        if configured:
            self._update_inventory_ship_node(sku, qty, configured)
            return True
        try:
            self._update_inventory_default_node(sku, qty)
            return True
        except WalmartAPIError:
            ship_node = self._resolve_ship_node(sku)
            if not ship_node:
                raise
            self._update_inventory_ship_node(sku, qty, ship_node)
            return True

    def delete_product(self, external_id):
        """Retire/delete Walmart item by SKU."""
        if not external_id:
            raise WalmartAPIError("Missing Walmart external_id/SKU for delete_product")
        sku = str(external_id)
        self._request("DELETE", f"/v3/items/{quote(sku, safe='')}")
        return True
