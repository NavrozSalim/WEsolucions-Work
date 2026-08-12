"""
Etsy Open API v3 adapter for inventory-only (and basic listing) sync.

Credentials live in ``Store.api_token`` as JSON, same pattern as Walmart/Sears:

    {
      "api_key": "keystring:shared_secret",
      "access_token": "...",
      "refresh_token": "...",
      "shop_id": "12345678"
    }

``api_key`` may also be split as ``keystring`` + ``shared_secret``.
``shop_id`` is optional — discovered via ``GET /v3/application/users/me``.
``refresh_token`` is recommended so scheduled syncs can renew the 1-hour access token.
"""
from __future__ import annotations

import json
import logging
import time
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import quote

import requests

from .base import BaseStoreAdapter

logger = logging.getLogger(__name__)

ETSY_API_BASE = "https://openapi.etsy.com/v3"
ETSY_TOKEN_URL = "https://api.etsy.com/v3/public/oauth/token"

MSG_ETSY_CONNECTED = "Etsy account connected successfully."
MSG_ETSY_INVALID_CREDS = "Invalid Etsy API credentials."

# Fields Etsy rejects on updateListingInventory when echoed from GET.
_INVENTORY_PRODUCT_DROP = frozenset({
    "product_id",
    "is_deleted",
    "value_pairs",
})
_INVENTORY_PROPERTY_DROP = frozenset({"scale_name"})
_INVENTORY_OFFERING_DROP = frozenset({"offering_id", "is_deleted"})


class EtsyAPIError(Exception):
    """Etsy API call failed."""

    def __init__(self, message, status_code=None, response_body=None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class EtsyAdapter(BaseStoreAdapter):
    """Etsy Open API v3 — validate, lookup by SKU, update price/inventory."""

    def __init__(self, store):
        super().__init__(store)
        self._session = requests.Session()
        self._creds = self._parse_credentials(self._token)
        self._base_url = (self._creds.get("base_url") or ETSY_API_BASE).rstrip("/")
        self._access_token = (self._creds.get("access_token") or "").strip() or None
        self._token_expires_at = float(self._creds.get("expires_at") or 0) or 0.0
        self._shop_id = self._normalize_shop_id(self._creds.get("shop_id"))
        self._sku_listing_cache: dict[str, str] = {}

    # ------------------------------------------------------------------ auth / credentials

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
        # Plain OAuth token only — still needs api_key in JSON for real calls.
        return {"access_token": txt}

    @staticmethod
    def _normalize_shop_id(value) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _keystring_and_secret(self) -> tuple[str | None, str | None]:
        api_key = (
            self._creds.get("api_key")
            or self._creds.get("x_api_key")
            or self._creds.get("x-api-key")
            or ""
        ).strip()
        if api_key and ":" in api_key:
            keystring, _, secret = api_key.partition(":")
            return keystring.strip() or None, secret.strip() or None
        keystring = (
            self._creds.get("keystring")
            or self._creds.get("client_id")
            or api_key
            or ""
        ).strip() or None
        secret = (
            self._creds.get("shared_secret")
            or self._creds.get("client_secret")
            or ""
        ).strip() or None
        return keystring, secret

    def _x_api_key_header(self) -> str | None:
        keystring, secret = self._keystring_and_secret()
        if keystring and secret:
            return f"{keystring}:{secret}"
        if keystring:
            return keystring
        return None

    def _persist_token_fields(self, updates: dict) -> None:
        """Merge token fields into store.api_token when the store is a real model."""
        if not updates:
            return
        self._creds.update(updates)
        store = self.store
        if store is None or not hasattr(store, "api_token"):
            return
        try:
            raw = getattr(store, "api_token", None) or ""
            data = {}
            if isinstance(raw, str) and raw.strip().startswith("{"):
                data = json.loads(raw)
            if not isinstance(data, dict):
                data = {}
            data.update({k: v for k, v in updates.items() if v is not None})
            store.api_token = json.dumps(data, separators=(",", ":"))
            if hasattr(store, "save") and getattr(store, "pk", None):
                store.save(update_fields=["api_token"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not persist Etsy token refresh on store: %s", exc)

    def _refresh_access_token(self) -> bool:
        refresh = (self._creds.get("refresh_token") or "").strip()
        keystring, _secret = self._keystring_and_secret()
        if not refresh or not keystring:
            return False
        try:
            resp = requests.post(
                ETSY_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": keystring,
                    "refresh_token": refresh,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise EtsyAPIError(f"Etsy token refresh failed: {exc}") from exc
        if resp.status_code >= 400:
            detail = (resp.text or "")[:300]
            raise EtsyAPIError(
                f"Etsy token refresh failed: {resp.status_code} — {detail}",
                status_code=resp.status_code,
                response_body=resp.text[:500] if resp.text else None,
            )
        body = resp.json() if resp.text else {}
        access = (body.get("access_token") or "").strip()
        if not access:
            raise EtsyAPIError("Etsy token refresh returned no access_token")
        new_refresh = (body.get("refresh_token") or refresh).strip()
        expires_in = int(body.get("expires_in") or 3600)
        self._access_token = access
        self._token_expires_at = time.time() + max(60, expires_in - 60)
        self._persist_token_fields({
            "access_token": access,
            "refresh_token": new_refresh,
            "expires_at": self._token_expires_at,
        })
        return True

    def _ensure_access_token(self) -> str:
        if self._access_token and (
            not self._token_expires_at or time.time() < self._token_expires_at
        ):
            return self._access_token
        if self._creds.get("refresh_token"):
            self._refresh_access_token()
            if self._access_token:
                return self._access_token
        if self._access_token:
            return self._access_token
        raise EtsyAPIError(
            "Missing Etsy access_token (and no refresh_token to renew it). "
            "Provide OAuth credentials in the store JSON."
        )

    def _auth_headers(self) -> dict:
        x_api_key = self._x_api_key_header()
        if not x_api_key:
            raise EtsyAPIError(
                "Missing Etsy api_key (keystring:shared_secret). "
                'Example: {"api_key":"keystring:secret","access_token":"...","shop_id":"..."}'
            )
        token = self._ensure_access_token()
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "x-api-key": x_api_key,
            "Authorization": f"Bearer {token}",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body=None,
        params=None,
        data=None,
        timeout=30,
        _retried=False,
    ):
        url = path if str(path).startswith("http") else f"{self._base_url}{path}"
        headers = self._auth_headers()
        if data is not None:
            headers = {**headers, "Content-Type": "application/x-www-form-urlencoded"}
        try:
            resp = self._session.request(
                method,
                url,
                json=json_body,
                data=data,
                params=params,
                headers=headers,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise EtsyAPIError(str(exc)) from exc

        if resp.status_code == 401 and not _retried and self._creds.get("refresh_token"):
            try:
                if self._refresh_access_token():
                    return self._request(
                        method,
                        path,
                        json_body=json_body,
                        params=params,
                        data=data,
                        timeout=timeout,
                        _retried=True,
                    )
            except EtsyAPIError:
                pass

        if resp.status_code >= 400:
            detail = ""
            try:
                body = resp.json() if resp.text else {}
                if isinstance(body, dict):
                    detail = (
                        body.get("error_description")
                        or body.get("error")
                        or body.get("message")
                        or ""
                    )
                    if not detail and isinstance(body.get("errors"), list):
                        detail = "; ".join(str(e) for e in body["errors"][:5])
            except Exception:  # noqa: BLE001
                detail = (resp.text or "")[:300]
            msg = f"Etsy API {method} {path}: {resp.status_code}"
            if detail:
                msg = f"{msg} — {detail}"
            raise EtsyAPIError(
                msg,
                status_code=resp.status_code,
                response_body=resp.text[:500] if resp.text else None,
            )
        if not resp.text:
            return None
        try:
            return resp.json()
        except Exception:  # noqa: BLE001
            return {"raw": resp.text}

    # ------------------------------------------------------------------ connection / shop

    def resolve_shop_id(self) -> str:
        if self._shop_id:
            return self._shop_id
        data = self._request("GET", "/application/users/me")
        shop_id = None
        if isinstance(data, dict):
            shop_id = data.get("shop_id") or data.get("user_id")
        self._shop_id = self._normalize_shop_id(shop_id)
        if not self._shop_id:
            raise EtsyAPIError(
                "Could not resolve Etsy shop_id from /users/me. "
                "Add shop_id to the credentials JSON."
            )
        self._persist_token_fields({"shop_id": self._shop_id})
        return self._shop_id

    def validate_connection(self) -> bool:
        try:
            ok, _msg, _code = self.test_etsy_connection()
            return ok
        except Exception:  # noqa: BLE001
            return False

    def test_etsy_connection(self) -> tuple[bool, str, str | None]:
        """
        Live credential check for create/update flows.
        Returns ``(ok, message, shop_id_or_none)``.
        """
        x_api_key = self._x_api_key_header()
        if not x_api_key:
            return False, (
                'Etsy credentials must include api_key ("keystring:shared_secret") '
                "or keystring + shared_secret."
            ), None
        if not self._access_token and not (self._creds.get("refresh_token") or "").strip():
            return False, (
                "Etsy credentials must include access_token and/or refresh_token."
            ), None
        try:
            shop_id = self.resolve_shop_id()
            self._request("GET", f"/application/shops/{quote(str(shop_id))}")
            return True, MSG_ETSY_CONNECTED, shop_id
        except EtsyAPIError as exc:
            if exc.status_code in (401, 403):
                return False, MSG_ETSY_INVALID_CREDS, None
            return False, str(exc)[:500], None

    # ------------------------------------------------------------------ inventory helpers

    @staticmethod
    def _money_to_float(price) -> float | None:
        if price is None:
            return None
        if isinstance(price, (int, float, Decimal)):
            return float(price)
        if isinstance(price, dict):
            amount = price.get("amount")
            divisor = price.get("divisor") or 100
            if amount is None:
                return None
            try:
                return float(Decimal(str(amount)) / Decimal(str(divisor)))
            except Exception:  # noqa: BLE001
                return None
        try:
            return float(price)
        except Exception:  # noqa: BLE001
            return None

    @classmethod
    def _sanitize_inventory_for_put(cls, inventory: dict) -> dict:
        """Convert getListingInventory payload into updateListingInventory body."""
        products_out = []
        for product in inventory.get("products") or []:
            if not isinstance(product, dict):
                continue
            row = {k: v for k, v in product.items() if k not in _INVENTORY_PRODUCT_DROP}
            props = []
            for prop in product.get("property_values") or []:
                if not isinstance(prop, dict):
                    continue
                cleaned = {
                    k: v for k, v in prop.items() if k not in _INVENTORY_PROPERTY_DROP
                }
                props.append(cleaned)
            row["property_values"] = props
            offerings = []
            for offering in product.get("offerings") or []:
                if not isinstance(offering, dict):
                    continue
                cleaned = {
                    k: v for k, v in offering.items() if k not in _INVENTORY_OFFERING_DROP
                }
                price = cls._money_to_float(cleaned.get("price"))
                if price is None:
                    raise EtsyAPIError("Etsy inventory offering is missing a valid price")
                cleaned["price"] = price
                if "quantity" in cleaned:
                    cleaned["quantity"] = max(0, int(cleaned["quantity"] or 0))
                if "is_enabled" not in cleaned:
                    cleaned["is_enabled"] = True
                offerings.append(cleaned)
            if not offerings:
                continue
            row["offerings"] = offerings
            products_out.append(row)

        body: dict = {"products": products_out}
        for key in (
            "price_on_property",
            "quantity_on_property",
            "sku_on_property",
            "readiness_state_on_property",
        ):
            if key in inventory and inventory[key] is not None:
                body[key] = inventory[key]
        return body

    def get_listing_inventory(self, listing_id) -> dict:
        lid = str(listing_id or "").strip()
        if not lid:
            raise EtsyAPIError("Missing Etsy listing_id for get_listing_inventory")
        data = self._request("GET", f"/application/listings/{quote(lid)}/inventory")
        if not isinstance(data, dict):
            raise EtsyAPIError(f"Unexpected inventory response for listing {lid}")
        return data

    def _put_listing_inventory(self, listing_id, body: dict) -> dict | None:
        lid = str(listing_id or "").strip()
        return self._request(
            "PUT",
            f"/application/listings/{quote(lid)}/inventory",
            json_body=body,
        )

    def _apply_price_stock_to_inventory(
        self,
        inventory: dict,
        *,
        price=None,
        stock=None,
        sku: str | None = None,
    ) -> dict:
        body = self._sanitize_inventory_for_put(inventory)
        target_sku = (sku or "").strip() or None
        price_float = None
        if price is not None:
            price_float = float(
                Decimal(str(price)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            )
        stock_int = None if stock is None else max(0, int(stock))

        matched = 0
        for product in body.get("products") or []:
            product_sku = str(product.get("sku") or "").strip()
            if target_sku and product_sku and product_sku != target_sku:
                continue
            matched += 1
            for offering in product.get("offerings") or []:
                if price_float is not None:
                    offering["price"] = price_float
                if stock_int is not None:
                    offering["quantity"] = stock_int
        if target_sku and matched == 0:
            raise EtsyAPIError(
                f"No Etsy inventory product matched SKU {target_sku!r} on this listing"
            )
        return body

    # ------------------------------------------------------------------ lookup

    def _iter_shop_listing_ids(self, *, states: tuple[str, ...] = ("active", "sold_out")):
        shop_id = self.resolve_shop_id()
        for state in states:
            offset = 0
            while True:
                data = self._request(
                    "GET",
                    f"/application/shops/{quote(str(shop_id))}/listings",
                    params={"state": state, "limit": 100, "offset": offset},
                )
                results = []
                if isinstance(data, dict):
                    results = data.get("results") or []
                if not results:
                    break
                for row in results:
                    if isinstance(row, dict) and row.get("listing_id") is not None:
                        yield str(row["listing_id"])
                if len(results) < 100:
                    break
                offset += 100

    def _batch_inventory_by_listing_ids(self, listing_ids: list[str]) -> list[dict]:
        if not listing_ids:
            return []
        # OAS: listing_ids as query array — requests encodes as listing_ids=a&listing_ids=b
        data = self._request(
            "GET",
            "/application/listings/batch/inventory",
            params=[("listing_ids", lid) for lid in listing_ids],
        )
        if isinstance(data, dict):
            results = data.get("results") or data.get("listings") or []
            if isinstance(results, list):
                return [r for r in results if isinstance(r, dict)]
        return data if isinstance(data, list) else []

    def lookup_listing_by_sku(self, sku: str) -> str | None:
        """
        Resolve Etsy listing_id for a SKU.

        If ``sku`` is already a numeric listing id owned by the shop, return it.
        Otherwise scan shop listings (batched inventory) for a matching product SKU.
        """
        text = (sku or "").strip()
        if not text:
            return None
        if text in self._sku_listing_cache:
            return self._sku_listing_cache[text]

        if text.isdigit():
            try:
                inv = self.get_listing_inventory(text)
                if isinstance(inv, dict) and inv.get("products") is not None:
                    self._sku_listing_cache[text] = text
                    return text
            except EtsyAPIError:
                pass

        batch: list[str] = []
        try:
            for listing_id in self._iter_shop_listing_ids():
                batch.append(listing_id)
                if len(batch) < 100:
                    continue
                found = self._find_sku_in_batch(text, batch)
                batch = []
                if found:
                    self._sku_listing_cache[text] = found
                    return found
            if batch:
                found = self._find_sku_in_batch(text, batch)
                if found:
                    self._sku_listing_cache[text] = found
                    return found
        except EtsyAPIError as exc:
            logger.warning("Etsy SKU lookup failed for %s: %s", text, exc)
            return None
        return None

    def _find_sku_in_batch(self, sku: str, listing_ids: list[str]) -> str | None:
        needle = sku.strip().lower()
        rows = self._batch_inventory_by_listing_ids(listing_ids)
        for row in rows:
            listing_id = row.get("listing_id")
            inventory = row.get("inventory") if isinstance(row.get("inventory"), dict) else row
            products = []
            if isinstance(inventory, dict):
                products = inventory.get("products") or []
            for product in products:
                if not isinstance(product, dict):
                    continue
                product_sku = str(product.get("sku") or "").strip().lower()
                if product_sku and product_sku == needle and listing_id is not None:
                    return str(listing_id)
        # Fallback: per-listing inventory when batch shape differs
        if not rows:
            for lid in listing_ids:
                try:
                    inv = self.get_listing_inventory(lid)
                except EtsyAPIError:
                    continue
                for product in inv.get("products") or []:
                    if not isinstance(product, dict):
                        continue
                    product_sku = str(product.get("sku") or "").strip().lower()
                    if product_sku == needle:
                        return str(lid)
        return None

    # ------------------------------------------------------------------ CRUD

    def create_product(self, sku, title, price, stock, **kwargs):
        """
        Create an Etsy draft listing when enough fields are supplied.

        Inventory-only sync does not use this path; managed full-store create
        requires taxonomy / shipping / readiness IDs from kwargs.
        """
        shop_id = self.resolve_shop_id()
        required = {
            "who_made": kwargs.get("who_made"),
            "when_made": kwargs.get("when_made"),
            "taxonomy_id": kwargs.get("taxonomy_id"),
        }
        missing = [k for k, v in required.items() if v in (None, "")]
        if missing:
            raise EtsyAPIError(
                "Etsy create_product requires who_made, when_made, and taxonomy_id "
                f"(missing: {', '.join(missing)}). Inventory-only mode should map "
                "existing listings instead of creating them."
            )
        form = {
            "quantity": max(0, int(stock or 0)) or 1,
            "title": title,
            "description": kwargs.get("description") or title or sku,
            "price": float(
                Decimal(str(price)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            ),
            "who_made": kwargs["who_made"],
            "when_made": kwargs["when_made"],
            "taxonomy_id": int(kwargs["taxonomy_id"]),
            "type": kwargs.get("listing_type") or kwargs.get("type") or "physical",
        }
        if kwargs.get("shipping_profile_id"):
            form["shipping_profile_id"] = int(kwargs["shipping_profile_id"])
        if kwargs.get("readiness_state_id"):
            form["readiness_state_id"] = int(kwargs["readiness_state_id"])
        if kwargs.get("image_ids"):
            form["image_ids"] = kwargs["image_ids"]
        data = self._request(
            "POST",
            f"/application/shops/{quote(str(shop_id))}/listings",
            data=form,
        )
        if isinstance(data, dict) and data.get("listing_id") is not None:
            listing_id = str(data["listing_id"])
            # Attach SKU / stock via inventory when possible
            try:
                self.update_product(listing_id, stock=stock, price=price, sku=sku)
            except EtsyAPIError as exc:
                logger.warning(
                    "Etsy listing %s created but inventory/SKU update failed: %s",
                    listing_id,
                    exc,
                )
            return listing_id
        return data

    def update_product(self, external_id, price=None, stock=None, **kwargs):
        """Update listing inventory price and/or quantity (inventory-only push path)."""
        listing_id = str(external_id or "").strip()
        if not listing_id:
            raise EtsyAPIError("Missing Etsy listing_id for update_product")
        if price is None and stock is None and not kwargs.get("title"):
            return None

        if price is not None or stock is not None:
            inventory = self.get_listing_inventory(listing_id)
            body = self._apply_price_stock_to_inventory(
                inventory,
                price=price,
                stock=stock,
                sku=kwargs.get("sku") or kwargs.get("marketplace_child_sku"),
            )
            self._put_listing_inventory(listing_id, body)

        title = kwargs.get("title")
        if title:
            shop_id = self.resolve_shop_id()
            self._request(
                "PATCH",
                f"/application/shops/{quote(str(shop_id))}/listings/{quote(listing_id)}",
                data={"title": title},
            )
        return True

    def update_inventory(self, external_id, stock):
        """Update only stock for the listing (all offerings, or SKU match via kwargs not used)."""
        return self.update_product(external_id, stock=stock)

    def delete_product(self, external_id):
        """Delete listing via DELETE /v3/application/listings/{listing_id}."""
        lid = str(external_id or "").strip()
        if not lid:
            raise ValueError("listing id is required")
        try:
            self._request("DELETE", f"/application/listings/{quote(lid)}")
            return True
        except EtsyAPIError as exc:
            if exc.status_code == 404:
                return True
            raise
