"""HTTP client for Bunnings Marketplace (Mirakl Seller API).

Auth: Authorization header with the seller SHOP_KEY from Mirakl
(My user settings > API Key). Base URL and secrets come from Store fields.

Key operations:
  H11  GET  /api/hierarchies
  PM11 GET  /api/products/attributes
  P41  POST /api/products/imports
  P42  GET  /api/products/imports/{import}
  P44  GET  /api/products/imports/{import}/error_report
  OF01 POST /api/offers/imports
  OF02 GET  /api/offers/imports/{import}
  OF03 GET  /api/offers/imports/{import}/error_report
  OF21 GET  /api/offers
  OR11 GET  /api/orders
  OR21 PUT  /api/orders/{order_id}/accept
  OR23 PUT  /api/orders/{order_id}/tracking
  OR24 PUT  /api/orders/{order_id}/ship
  OR29 PUT  /api/orders/{order_id}/cancel
"""
from __future__ import annotations

import csv
import io
import json
import logging
import time

import requests

from ..errors import MarketplaceError

logger = logging.getLogger("listings.bunnings")

REQUEST_TIMEOUT = 60
IMPORT_POLL_ATTEMPTS = 15
IMPORT_POLL_SECONDS = 2.0
DEFAULT_PRODUCTION_BASE_URL = "https://bunnings-prod.mirakl.net"

# SENT means queued — not finished. COMPLETE can still have rejected rows.
_COMPLETE_STATUSES = frozenset({"COMPLETE", "COMPLETED"})
_FAIL_STATUSES = frozenset({
    "FAILED",
    "CANCELLED",
    "CANCELED",
    "COMPLETE_WITH_ERROR",
    "COMPLETE_WITH_ERRORS",
    "COMPLETED_WITH_ERROR",
    "COMPLETED_WITH_ERRORS",
})


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
        accept: str | None = None,
    ) -> BunningsResult:
        url = f"{self.base_url}{path}"
        logger.info(
            "Bunnings request store=%s env=%s method=%s path=%s",
            getattr(self.store, "name", ""),
            self.environment,
            method.upper(),
            path,
        )
        headers = self._headers(
            json_body=json_body is not None and files is None,
            accept=accept or "application/json",
        )
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

    def get(self, path: str, *, params: dict | None = None, accept: str | None = None) -> BunningsResult:
        return self.request("GET", path, params=params, accept=accept)

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
        return self.get("/api/hierarchies", params={"max": 10000})

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

    def import_error_report(self, kind: str, import_id) -> BunningsResult:
        """P44 / OF03 — CSV of rejected rows (errors column)."""
        if kind == "product":
            path = f"/api/products/imports/{import_id}/error_report"
        else:
            path = f"/api/offers/imports/{import_id}/error_report"
        result = self.get(path, accept="*/*")
        if result.ok or result.status not in (404, 405):
            return result
        if kind != "product":
            return result
        return self.get(
            f"/api/products/imports/{import_id}/transformation_error_report",
            accept="*/*",
        )

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
        """Poll P42 or OF02 until complete/failed or attempts exhausted.

        COMPLETE with rejected rows (Complete with errors) is a failure.
        SENT / WAITING keep polling — they are not success.
        """
        getter = self.product_import_status if kind == "product" else self.offer_import_status
        tries = attempts if attempts is not None else IMPORT_POLL_ATTEMPTS
        wait = interval if interval is not None else IMPORT_POLL_SECONDS
        last = BunningsResult(ok=False, message="Import id missing.")
        for i in range(max(1, tries)):
            last = getter(import_id)
            if not last.ok:
                return last
            status = _import_status(last.data)
            if status in _FAIL_STATUSES or (
                status in _COMPLETE_STATUSES and import_has_line_errors(last.data)
            ):
                return self._failed_import_result(kind, import_id, last, status)
            if status in _COMPLETE_STATUSES:
                last.message = f"Bunnings {kind} import {import_id} {status}."
                return last
            if i < tries - 1 and wait:
                time.sleep(wait)
        last = BunningsResult(
            ok=False,
            data=last.data,
            message=(
                f"Bunnings {kind} import {import_id} still "
                f"{_import_status(last.data) or 'in progress'}."
            ),
            status=last.status,
        )
        return last

    def _failed_import_result(self, kind: str, import_id, last: BunningsResult, status: str) -> BunningsResult:
        report = self.import_error_report(kind, import_id)
        raw = report.data if report.ok else ""
        line_errors = parse_mirakl_error_report(raw)
        payload = last.data if isinstance(last.data, dict) else {"raw": last.data}
        payload = dict(payload)
        payload["line_errors"] = line_errors
        if isinstance(raw, str) and raw.strip():
            payload["error_report"] = raw[:8000]
        detail = format_line_errors(line_errors)
        if not detail and isinstance(raw, str) and raw.strip() and "error" in raw.lower():
            detail = raw.strip()[:500]
        count = line_error_count(last.data) or len(line_errors)
        status_label = status or "FAILED"
        if import_has_line_errors(last.data) and "ERROR" not in status_label:
            status_label = f"{status_label} with errors"
        message = f"Bunnings {kind} import {import_id} {status_label}."
        if count:
            message = f"{message} {count} line(s) rejected."
        if detail:
            message = f"{message} {detail}"
        return BunningsResult(
            ok=False,
            data=payload,
            message=message,
            status=last.status,
        )

    def list_carriers(self) -> BunningsResult:
        result = self.get("/api/shipping/carriers")
        if result.ok or result.status not in (404, 405):
            return result
        return self.get("/api/carriers")

    def get_order(self, order_id: str) -> BunningsResult:
        oid = (order_id or "").strip()
        result = self.get(f"/api/orders/{oid}")
        if result.ok or result.status not in (404, 405):
            return result
        return self.get("/api/orders", params={"order_ids": oid, "paginate": "false"})

    def list_threads(self, *, with_messages: bool = True, page_token: str = "", updated_since: str = "") -> BunningsResult:
        params = {
            "with_messages": "true" if with_messages else "false",
            "limit": 50,
        }
        if page_token:
            params["page_token"] = page_token
        if updated_since:
            params["updated_since"] = updated_since
        return self.get("/api/inbox/threads", params=params)

    def reply_thread(self, thread_id: str, body: str) -> BunningsResult:
        """M12 — JSON first, then multipart (Mirakl often requires form-data)."""
        payload = {"body": body, "to": [{"type": "CUSTOMER"}]}
        path = f"/api/inbox/threads/{thread_id}/message"
        result = self.request("POST", path, json_body=payload)
        if result.ok or result.status not in (400, 415, 422):
            return result
        return self.request(
            "POST",
            path,
            files={"file": ("", b"", "application/octet-stream")},
            data={"body": json.dumps(payload)},
        )

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

    def cancel_order(self, order_id: str, *, reason_code: str = "", reason_label: str = "") -> BunningsResult:
        """OR29 PUT /api/orders/{order_id}/cancel."""
        path = f"/api/orders/{order_id}/cancel"
        body = {}
        if (reason_code or "").strip():
            body["reason_code"] = reason_code.strip()
        if (reason_label or "").strip():
            body["reason_label"] = reason_label.strip()
        if body:
            result = self.request("PUT", path, json_body=body)
            if result.ok or result.status not in (400, 415, 422):
                return result
        return self.request("PUT", path, empty_body=True)


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


def _int_field(data, *keys) -> int:
    if not isinstance(data, dict):
        return 0
    for key in keys:
        val = data.get(key)
        if val in (None, ""):
            continue
        try:
            return max(0, int(val))
        except (TypeError, ValueError):
            continue
    return 0


def line_error_count(data) -> int:
    return _int_field(data, "lines_in_error", "linesInError", "error_lines", "nb_error")


def import_has_line_errors(data) -> bool:
    """True when Mirakl finished with rejected product/offer rows."""
    if not isinstance(data, dict):
        return False
    if "WITH_ERROR" in _import_status(data):
        return True
    errors = line_error_count(data)
    if errors > 0:
        return True
    has_report = data.get("has_error_report", data.get("hasErrorReport"))
    if has_report in (True, "true", "True", 1, "1"):
        success = _int_field(data, "lines_in_success", "linesInSuccess")
        read = _int_field(data, "lines_read", "linesRead")
        if success == 0 and (read > 0 or errors > 0):
            return True
    return False


def parse_mirakl_error_report(payload) -> list[dict]:
    """Parse P44/OF03 CSV (or JSON wrapper) into [{sku, errors}, ...]."""
    if isinstance(payload, dict):
        nested = payload.get("line_errors")
        if isinstance(nested, list) and nested:
            out = []
            for row in nested:
                if not isinstance(row, dict):
                    continue
                sku = str(row.get("sku") or row.get("product-id") or "").strip()
                err = str(row.get("errors") or row.get("error") or "").strip()
                if err:
                    out.append({"sku": sku, "errors": err})
            if out:
                return out
        payload = (
            payload.get("error_report")
            or payload.get("raw")
            or payload.get("file")
            or ""
        )
    if isinstance(payload, (bytes, bytearray)):
        text = payload.decode("utf-8-sig", errors="replace")
    else:
        text = str(payload or "")
    text = text.strip()
    if not text:
        return []
    first = text.splitlines()[0]
    delimiter = ";" if first.count(";") >= first.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    rows: list[dict] = []
    for raw in reader:
        if not isinstance(raw, dict):
            continue
        mapped = {(k or "").strip().strip('"').lower(): (v or "").strip() for k, v in raw.items()}
        sku = (
            mapped.get("product-id")
            or mapped.get("product_id")
            or mapped.get("sku")
            or mapped.get("shop_sku")
            or ""
        )
        err = mapped.get("errors") or mapped.get("error") or mapped.get("error-message") or ""
        if err:
            rows.append({"sku": sku, "errors": err})
    return rows


def format_line_errors(rows: list[dict], *, limit: int = 8) -> str:
    parts = []
    for row in (rows or [])[:limit]:
        sku = str(row.get("sku") or "").strip() or "row"
        err = str(row.get("errors") or "").strip()
        if err:
            parts.append(f"{sku}: {err}")
    extra = len(rows or []) - limit
    text = " ".join(parts)
    if extra > 0:
        text = f"{text} (+{extra} more)"
    return text


def line_errors_by_sku(data) -> dict[str, str]:
    """Map SKU (lower) → first error string from poll payload."""
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for row in data.get("line_errors") or []:
        if not isinstance(row, dict):
            continue
        sku = str(row.get("sku") or "").strip()
        err = str(row.get("errors") or "").strip()
        if sku and err and sku.lower() not in out:
            out[sku.lower()] = err
    return out
