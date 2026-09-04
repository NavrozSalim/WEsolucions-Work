"""HTTP client for Bunnings Marketplace (Mirakl Seller API).

Auth: Authorization header with the seller SHOP_KEY from Mirakl
(My user settings > API Key). Base URL and secrets come from Store fields.

Key operations:
  H11  GET  /api/hierarchies
  PM11 GET  /api/products/attributes
  P41  POST /api/products/imports
  P42  GET  /api/products/imports/{import}
  OF01 POST /api/offers/imports
  OF02 GET  /api/offers/imports/{import}
  OF21 GET  /api/offers
  OR11 GET  /api/orders
  OR21 PUT  /api/orders/{order_id}/accept
  OR23 PUT  /api/orders/{order_id}/tracking
  OR24 PUT  /api/orders/{order_id}/ship
  OR29 PUT  /api/orders/{order_id}/cancel
"""
from __future__ import annotations

import logging
import time

import requests

from ..errors import MarketplaceError

logger = logging.getLogger("listings.bunnings")

REQUEST_TIMEOUT = 60
IMPORT_POLL_ATTEMPTS = 8
IMPORT_POLL_SECONDS = 1.5
DEFAULT_PRODUCTION_BASE_URL = "https://bunnings-prod.mirakl.net"

_COMPLETE_STATUSES = frozenset({"COMPLETE", "COMPLETED", "SENT"})
_FAIL_STATUSES = frozenset({"FAILED", "CANCELLED", "CANCELED"})


class BunningsResult:
    def __init__(
        self,
        ok: bool,
        data=None,
        message: str = "",
        status: int = 0,
    ):
        self.ok = ok
        self.data = data
        self.message = message
        self.status = status

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "data": self.data,
            "message": self.message,
            "status": self.status,
        }


class BunningsClient:
    def __init__(self, store, environment: str | None = None, *, require_auth: bool = True):
        self.store = store
        self.environment = environment or (getattr(store, "bunnings_environment", None) or "production")
        if self.environment not in ("staging", "production"):
            self.environment = "production"

        if self.environment == "production":
            self.base_url = (
                (getattr(store, "bunnings_production_base_url", None) or "").strip().rstrip("/")
                or DEFAULT_PRODUCTION_BASE_URL
            )
            self._shop_key = (getattr(store, "bunnings_production_shop_key", None) or "").strip()
        else:
            self.base_url = (getattr(store, "bunnings_staging_base_url", None) or "").strip().rstrip("/")
            self._shop_key = (getattr(store, "bunnings_staging_shop_key", None) or "").strip()

        if require_auth:
            missing = []
            if not self.base_url:
                missing.append("base URL")
            if not self._shop_key:
                missing.append("SHOP_KEY")
            if missing:
                raise MarketplaceError(
                    f"No {self.environment} Bunnings {', '.join(missing)} configured for store "
                    f"'{getattr(store, 'name', store)}'. Add it in store settings."
                )

    @property
    def shop_key(self) -> str:
        return self._shop_key

    def _headers(self, *, json_body: bool = False, accept: str = "application/json") -> dict[str, str]:
        headers = {
            "Authorization": self._shop_key,
            "Accept": accept,
        }
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body=None,
        files: dict | None = None,
        data=None,
        empty_body: bool = False,
        timeout: int | None = None,
    ) -> BunningsResult:
        url = f"{self.base_url}{path}"
        logger.info(
            "Bunnings request store=%s env=%s method=%s path=%s",
            getattr(self.store, "name", ""),
            self.environment,
            method.upper(),
            path,
        )
        headers = self._headers(json_body=json_body is not None and files is None)
        try:
            kwargs = {
                "headers": headers,
                "params": params or {},
                "timeout": timeout or REQUEST_TIMEOUT,
            }
            if files is not None:
                kwargs["files"] = files
                if data is not None:
                    kwargs["data"] = data
            elif json_body is not None:
                kwargs["json"] = json_body
            elif empty_body:
                kwargs["data"] = b""
            elif data is not None:
                kwargs["data"] = data
            response = requests.request(method.upper(), url, **kwargs)
        except requests.RequestException as exc:
            logger.warning("Bunnings network error path=%s: %s", path, exc)
            return BunningsResult(ok=False, message=str(exc), status=0)

        if response.status_code == 401:
            return BunningsResult(
                ok=False,
                message="Bunnings rejected these credentials (401 Unauthorized).",
                status=401,
            )
        if response.status_code >= 400:
            text = (response.text or "")[:800]
            return BunningsResult(
                ok=False,
                message=text or f"Bunnings HTTP {response.status_code}",
                status=response.status_code,
            )

        if not (response.content or b"").strip():
            return BunningsResult(ok=True, data={}, status=response.status_code)
        try:
            payload = response.json()
        except ValueError:
            payload = response.text
        return BunningsResult(ok=True, data=payload, status=response.status_code)

    def get(self, path: str, *, params: dict | None = None) -> BunningsResult:
        return self.request("GET", path, params=params)

    def verify_connection(self) -> BunningsResult:
        """GET /api/hierarchies (H11) — same check as the standalone test script."""
        result = self.get("/api/hierarchies")
        if not result.ok:
            return result
        count = 0
        if isinstance(result.data, dict):
            hierarchies = result.data.get("hierarchies")
            if isinstance(hierarchies, list):
                count = len(hierarchies)
        msg = f"Bunnings {self.environment} connection successful."
        if count:
            msg = f"{msg} ({count} categories)."
        return BunningsResult(ok=True, data=result.data, message=msg, status=result.status)

    def list_hierarchies(self) -> BunningsResult:
        return self.get("/api/hierarchies")

    def list_product_attributes(self, hierarchy_code: str) -> BunningsResult:
        return self.get("/api/products/attributes", params={"hierarchy": hierarchy_code})

    def list_logistic_classes(self) -> BunningsResult:
        result = self.get("/api/shipping/logistics")
        if result.ok or result.status not in (404, 405):
            return result
        return self.get("/api/shipping/logistic_classes")

    def import_products(self, csv_text: str, *, filename: str = "products.csv") -> BunningsResult:
        """P41 POST /api/products/imports."""
        files = {
            "file": (filename, csv_text.encode("utf-8"), "text/csv"),
        }
        return self.request(
            "POST",
            "/api/products/imports",
            files=files,
            data={"operator_format": "true"},
        )

    def product_import_status(self, import_id) -> BunningsResult:
        return self.get(f"/api/products/imports/{import_id}")

    def import_offers(self, csv_text: str, *, filename: str = "offers.csv", import_mode: str = "NORMAL") -> BunningsResult:
        """OF01 POST /api/offers/imports."""
        files = {
            "file": (filename, csv_text.encode("utf-8"), "text/csv"),
        }
        return self.request(
            "POST",
            "/api/offers/imports",
            files=files,
            data={"import_mode": import_mode or "NORMAL"},
        )

    def offer_import_status(self, import_id) -> BunningsResult:
        return self.get(f"/api/offers/imports/{import_id}")

    def list_offers(self, *, sku: str = "", max_results: int = 10) -> BunningsResult:
        params = {"max": max(1, min(int(max_results or 10), 100))}
        if sku:
            params["sku"] = sku
        return self.get("/api/offers", params=params)

    def poll_import(
        self,
        kind: str,
        import_id,
        *,
        attempts: int | None = None,
        interval: float | None = None,
    ) -> BunningsResult:
        """Poll P42 or OF02 until complete/failed or attempts exhausted."""
        getter = self.product_import_status if kind == "product" else self.offer_import_status
        tries = attempts if attempts is not None else IMPORT_POLL_ATTEMPTS
        wait = interval if interval is not None else IMPORT_POLL_SECONDS
        last = BunningsResult(ok=False, message="Import id missing.")
        for i in range(max(1, tries)):
            last = getter(import_id)
            if not last.ok:
                return last
            status = _import_status(last.data)
            if status in _FAIL_STATUSES:
                return BunningsResult(
                    ok=False,
                    data=last.data,
                    message=f"Bunnings {kind} import {import_id} {status}.",
                    status=last.status,
                )
            if status in _COMPLETE_STATUSES:
                last.message = f"Bunnings {kind} import {import_id} {status}."
                return last
            if i < tries - 1 and wait:
                time.sleep(wait)
        last.message = (
            f"Bunnings {kind} import {import_id} still "
            f"{_import_status(last.data) or 'in progress'}."
        )
        return last

    def list_orders(
        self,
        *,
        offset: int = 0,
        max_results: int = 50,
        order_state_codes: str = "",
        start_update_date: str = "",
    ) -> BunningsResult:
        params = {
            "paginate": "true",
            "max": max(1, min(int(max_results or 50), 100)),
            "offset": max(0, int(offset or 0)),
        }
        if order_state_codes:
            params["order_state_codes"] = order_state_codes
        if start_update_date:
            params["start_update_date"] = start_update_date
        return self.get("/api/orders", params=params)

    def accept_order(self, order_id: str, order_lines: list[dict]) -> BunningsResult:
        """OR21 PUT /api/orders/{order_id}/accept."""
        return self.request(
            "PUT",
            f"/api/orders/{order_id}/accept",
            json_body={"order_lines": order_lines},
        )

    def update_tracking(self, order_id: str, payload: dict) -> BunningsResult:
        """OR23 PUT /api/orders/{order_id}/tracking."""
        return self.request("PUT", f"/api/orders/{order_id}/tracking", json_body=payload)

    def ship_order(self, order_id: str) -> BunningsResult:
        """OR24 PUT /api/orders/{order_id}/ship (empty body)."""
        return self.request("PUT", f"/api/orders/{order_id}/ship", empty_body=True)

    def cancel_order(self, order_id: str) -> BunningsResult:
        """OR29 PUT /api/orders/{order_id}/cancel (empty body)."""
        return self.request("PUT", f"/api/orders/{order_id}/cancel", empty_body=True)


def extract_import_id(data) -> str:
    if isinstance(data, dict):
        for key in ("import_id", "importId", "id"):
            val = data.get(key)
            if val not in (None, ""):
                return str(val)
    return ""


def _import_status(data) -> str:
    if not isinstance(data, dict):
        return ""
    return str(
        data.get("import_status")
        or data.get("status")
        or data.get("importStatus")
        or ""
    ).strip().upper()
