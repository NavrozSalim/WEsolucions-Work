"""HTTP client for Bunnings Marketplace (Mirakl Seller API).

Auth: Authorization header with the seller SHOP_KEY from Mirakl
(My user settings > API Key). Base URL and secrets come from Store fields.

Key operations:
  H11  GET  /api/hierarchies
  PM11 GET  /api/products/attributes
  P41  POST /api/products/imports
  P42  GET  /api/products/imports/{import}
  P44  GET  /api/products/imports/{import}/error_report
  P11  GET  /api/products/{product_id}
  P31  GET  /api/products
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
# One catalog read for many shop SKUs. A per-SKU lookup of a large publish
# exceeds the web worker limit before the product file is sent.
CATALOG_LOOKUP_TIMEOUT = 20
CATALOG_LOOKUP_CHUNK = 100
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

        return BunningsResult(
            ok=True,
            data=_response_payload(response),
            status=response.status_code,
        )

    def get(
        self,
        path: str,
        *,
        params: dict | None = None,
        accept: str | None = None,
        timeout: int | None = None,
    ) -> BunningsResult:
        return self.request("GET", path, params=params, accept=accept, timeout=timeout)

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
        """P44 / OF03, then P47 transformation errors when P44 is empty."""
        if kind == "product":
            path = f"/api/products/imports/{import_id}/error_report"
        else:
            path = f"/api/offers/imports/{import_id}/error_report"
        result = self.get(path, accept="*/*")
        if kind != "product":
            return result
        if result.ok and parse_mirakl_error_report(result.data):
            return result
        transform = self.get(
            f"/api/products/imports/{import_id}/transformation_error_report",
            accept="*/*",
        )
        if transform.ok and parse_mirakl_error_report(transform.data):
            return transform
        return result if result.ok else transform

    def list_offers(self, *, sku: str = "", max_results: int = 10) -> BunningsResult:
        params = {"max": max(1, min(int(max_results or 10), 100))}
        if sku:
            params["sku"] = sku
        return self.get("/api/offers", params=params)

    def get_product(self, product_id: str) -> BunningsResult:
        """P11 / P31 / MCM — whether Bunnings has accepted this product-id."""
        pid = (product_id or "").strip()
        if not pid:
            return BunningsResult(ok=False, message="Product id missing.", status=0)
        last = BunningsResult(ok=False, message="Product not found.", status=404)
        lookups = (
            (f"/api/products/{pid}", None),
            ("/api/products", {"product_ids": pid}),
            (f"/api/mcm/products/{pid}", None),
        )
        for path, params in lookups:
            result = self.get(path, params=params)
            last = result
            if result.status == 401:
                return result
            if result.ok and _payload_has_product(result.data, pid):
                return result
            if result.status not in (0, 404, 405):
                continue
        return BunningsResult(
            ok=False,
            data=last.data,
            message=last.message or "Product not found.",
            status=last.status or 404,
        )

    def product_exists(self, product_id: str) -> bool:
        result = self.get_product(product_id)
        return bool(result.ok and _payload_has_product(result.data, product_id))

    def products_in_catalog(self, product_ids) -> set[str] | None:
        """Shop SKUs Bunnings already has.

        One P31 call per chunk, not one call per SKU. None means the catalog
        could not be read, so the caller should still send the product file.
        """
        ordered: list[str] = []
        seen: set[str] = set()
        for raw in product_ids or []:
            pid = str(raw or "").strip()
            key = pid.casefold()
            if not pid or key in seen:
                continue
            seen.add(key)
            ordered.append(pid)
        if not ordered:
            return set()

        found: set[str] = set()
        chunk_size = max(1, CATALOG_LOOKUP_CHUNK)
        for start in range(0, len(ordered), chunk_size):
            chunk = ordered[start:start + chunk_size]
            result = self.get(
                "/api/products",
                params={"product_ids": ",".join(chunk), "max": len(chunk)},
                timeout=CATALOG_LOOKUP_TIMEOUT,
            )
            if result.status == 401:
                raise MarketplaceError(
                    result.message or "Bunnings rejected these credentials (401 Unauthorized)."
                )
            if not result.ok:
                logger.warning(
                    "Bunnings catalog lookup failed status=%s message=%s",
                    result.status,
                    (result.message or "")[:300],
                )
                return None
            found.update(_matching_requested_ids(result.data, chunk))
        return found

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


def _product_items(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        nested = data.get("products") or data.get("data") or data.get("mcm_products")
        if isinstance(nested, list):
            return nested
        if any(data.get(key) for key in ("product_id", "product-id", "product_sku", "shop_sku", "sku")):
            return [data]
    return []


def _matching_requested_ids(data, requested: list[str]) -> set[str]:
    """Requested shop SKUs that appear in a P31 payload."""
    wanted = {pid.casefold(): pid for pid in requested if str(pid or "").strip()}
    if not wanted:
        return set()
    found: set[str] = set()
    for item in _product_items(data):
        if not isinstance(item, dict):
            continue
        for key in (
            "product_id",
            "product-id",
            "product_sku",
            "product-sku",
            "shop_sku",
            "sku",
            "code",
            "id",
            "mirakl_product_id",
        ):
            val = str(item.get(key) or "").strip()
            hit = wanted.get(val.casefold())
            if hit:
                found.add(hit)
                break
    return found


def _payload_has_product(data, product_id: str) -> bool:
    """True when a Mirakl product payload includes this shop product-id."""
    needle = (product_id or "").strip().lower()
    if not needle:
        return False
    if isinstance(data, list):
        items = data
        wrapped = True
    elif isinstance(data, dict):
        nested = data.get("products") or data.get("data") or data.get("mcm_products")
        if isinstance(nested, list):
            items = nested
            wrapped = True
        else:
            items = [data]
            wrapped = False
    else:
        return False
    if wrapped:
        if not items:
            return False
        for item in items:
            if _item_matches_product(item, needle):
                return True
        return True
    return any(_item_matches_product(item, needle) for item in items) or _looks_like_product_body(data)


def _item_matches_product(item, needle: str) -> bool:
    if not isinstance(item, dict):
        return False
    for key in (
        "product_id",
        "product-id",
        "shop_sku",
        "sku",
        "code",
        "id",
        "mirakl_product_id",
    ):
        val = str(item.get(key) or "").strip().lower()
        if val and val == needle:
            return True
    return False


def _looks_like_product_body(data) -> bool:
    if not isinstance(data, dict) or not data:
        return False
    if data.get("products") == []:
        return False
    skip = {"message", "status", "errors", "error", "ok"}
    return any(str(k) not in skip for k in data.keys())


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
    return max(
        _int_field(data, "lines_in_error", "linesInError", "error_lines", "nb_error"),
        _int_field(data, "transform_lines_in_error", "transformLinesInError"),
    )


def import_has_line_errors(data) -> bool:
    """True when Mirakl finished with rejected product/offer rows."""
    if not isinstance(data, dict):
        return False
    if "WITH_ERROR" in _import_status(data):
        return True
    errors = line_error_count(data)
    if errors > 0:
        return True
    has_transform_report = data.get(
        "has_transformation_error_report", data.get("hasTransformationErrorReport")
    )
    if has_transform_report in (True, "true", "True", 1, "1"):
        transform_ok = _int_field(data, "transform_lines_in_success", "transformLinesInSuccess")
        if transform_ok == 0:
            return True
    has_report = data.get("has_error_report", data.get("hasErrorReport"))
    if has_report in (True, "true", "True", 1, "1"):
        success = _int_field(data, "lines_in_success", "linesInSuccess")
        read = _int_field(data, "lines_read", "linesRead")
        if success == 0 and (read > 0 or errors > 0):
            return True
    return False


def _is_spreadsheet(content_type: str, raw: bytes) -> bool:
    """True for Mirakl Excel error reports. CSV and JSON stay text."""
    if "json" in content_type or "csv" in content_type or content_type.startswith("text/"):
        return False
    if "spreadsheet" in content_type or "excel" in content_type:
        return True
    return raw[:4] == b"PK\x03\x04"


def _response_payload(response):
    raw = response.content or b""
    if not raw.strip():
        return {}
    headers = getattr(response, "headers", None) or {}
    content_type = ""
    if hasattr(headers, "get"):
        content_type = str(headers.get("Content-Type") or headers.get("content-type") or "")
    content_type = content_type.split(";")[0].strip().lower()
    if _is_spreadsheet(content_type, raw):
        return raw
    try:
        return response.json()
    except ValueError:
        return response.text


def _cell(value) -> str:
    return str(value or "").strip().strip('"')


def _assemble_error(code: str, attr: str, val: str, message: str) -> str:
    text = message
    if attr and text and not text.lower().startswith(attr.lower()):
        text = f"{attr}={val}: {text}" if val else f"{attr}: {text}"
    elif not text and (attr or val):
        text = f"{attr}={val}".strip("=")
    if code and text and code not in text:
        text = f"{code}|{text}"
    return text or code


def _split_code(err: str) -> tuple[str, str]:
    """Pull a Mirakl code such as 1004 or 2006 off the front of an errors cell."""
    text = _cell(err)
    if "|" not in text:
        return "", text
    left, right = text.split("|", 1)
    left = left.strip()
    right = right.strip()
    if not left or not right or " " in left or len(left) > 20:
        return "", text
    if left.isdigit() or any(ch.isdigit() for ch in left):
        return left, right
    return "", text


_FIELD_HINTS = (
    ("category", "category"),
    ("brand", "brand"),
    ("barcode", "ean"),
    ("gtin", "ean"),
    ("ean", "ean"),
    ("image", "image-1"),
    ("description", "description"),
    ("title", "title"),
    ("logistic", "logistic-class"),
)


def _infer_field(message: str, mapped: dict) -> tuple[str, str]:
    """Integration reports name the bad column in the sentence, not in its own column."""
    text = _cell(message).casefold()
    if "|" in text:
        text = text.split("|", 1)[1]
    for word, column in _FIELD_HINTS:
        if word not in text:
            continue
        return column, _cell(mapped.get(column.lower()))
    return "", ""


def _rows_from_mapped(mapped: dict) -> list[dict]:
    sku = (
        mapped.get("product-id")
        or mapped.get("product_id")
        or mapped.get("sku")
        or mapped.get("shop_sku")
        or mapped.get("product-sku")
        or mapped.get("supplier_item_number")
        or mapped.get("supplier-item-number")
        or ""
    )
    sku = _cell(sku)
    err = (
        mapped.get("errors")
        or mapped.get("error")
        or mapped.get("error-message")
        or mapped.get("error_message")
        or mapped.get("message")
        or ""
    )
    attr = _cell(
        mapped.get("attribute-code")
        or mapped.get("attribute_code")
        or mapped.get("attribute")
        or ""
    )
    explicit_value = bool(
        mapped.get("attribute-value")
        or mapped.get("attribute_value")
        or mapped.get("rejected-value")
        or mapped.get("error-code")
        or mapped.get("error_code")
        or mapped.get("error-message")
        or mapped.get("error_message")
    )
    val = _cell(
        mapped.get("attribute-value")
        or mapped.get("attribute_value")
        or mapped.get("rejected-value")
        or (mapped.get("value") if explicit_value or attr else "")
        or ""
    )
    code = _cell(mapped.get("error-code") or mapped.get("error_code") or "")
    parts = [part.strip() for part in _cell(err).replace("\r", "\n").split("\n") if part.strip()]
    if not parts:
        parts = [""]
    rows: list[dict] = []
    for part in parts:
        part_attr = attr
        part_val = val
        part_code = code
        message = part
        if not part_code:
            part_code, message = _split_code(part)
        if not part_val and "=" in message and ":" in message:
            left, right = message.split(":", 1)
            name, sep, raw_val = left.partition("=")
            if sep and name.strip() and raw_val.strip():
                if not part_attr:
                    part_attr = name.strip()
                part_val = raw_val.strip()
                message = right.strip()
        if not part_attr:
            inferred_attr, inferred_val = _infer_field(message, mapped)
            part_attr = inferred_attr
            if not part_val:
                part_val = inferred_val
        display = _assemble_error(part_code, part_attr, part_val, message)
        if not display:
            continue
        rows.append({
            "sku": sku,
            "errors": display,
            "code": part_code,
            "attribute": part_attr,
            "value": part_val,
            "message": message or display,
        })
    return rows


def normalize_line_error(row) -> dict | None:
    """Keep sku/errors and fill code, attribute, value, and message when we can."""
    if not isinstance(row, dict):
        return None
    sku = _cell(row.get("sku") or row.get("product-id") or "")
    errors = _cell(row.get("errors") or row.get("error") or "")
    code = _cell(row.get("code") or "")
    attr = _cell(row.get("attribute") or "")
    val = _cell(row.get("value") or "")
    message = _cell(row.get("message") or "")
    if code or attr or message:
        if not message:
            message = errors
            if code and message.startswith(f"{code}|"):
                message = message[len(code) + 1:].strip()
            prefix = f"{attr}={val}:".lower() if attr and val else ""
            if prefix and message.lower().startswith(prefix):
                message = message.split(":", 1)[1].strip()
        if not errors:
            errors = _assemble_error(code, attr, val, message)
        if not (errors or message):
            return None
        return {
            "sku": sku,
            "errors": errors or message,
            "code": code,
            "attribute": attr,
            "value": val,
            "message": message or errors,
        }
    if not errors:
        return None
    parsed = _rows_from_mapped({"sku": sku, "errors": errors})
    if not parsed:
        return None
    item = parsed[0]
    item["errors"] = errors
    if sku:
        item["sku"] = sku
    return item


def _parse_xlsx_error_report(blob: bytes) -> list[dict]:
    from openpyxl import load_workbook

    try:
        workbook = load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Bunnings error report Excel could not be read: %s", exc)
        return []
    try:
        sheet = workbook.active
        grid: list[list[str]] = []
        for raw in sheet.iter_rows(values_only=True):
            grid.append([_cell(cell) for cell in raw])
            if len(grid) > 5000:
                break
    finally:
        workbook.close()
    if not grid:
        return []
    header_at = 0
    for index, row in enumerate(grid[:8]):
        if any("error" in cell.casefold() for cell in row):
            header_at = index
            break
    header = grid[header_at]
    rows: list[dict] = []
    for raw in grid[header_at + 1:]:
        if not any(raw):
            continue
        mapped: dict[str, str] = {}
        for index, key in enumerate(header):
            name = _cell(key).lower()
            if not name:
                continue
            mapped[name] = raw[index] if index < len(raw) else ""
        rows.extend(_rows_from_mapped(mapped))
    return rows


def parse_mirakl_error_report(payload) -> list[dict]:
    """Parse P44/P47/OF03 CSV or Excel into [{sku, errors, code, attribute, value, message}, ...]."""
    if isinstance(payload, dict):
        nested = payload.get("line_errors")
        if isinstance(nested, list) and nested:
            out = []
            for row in nested:
                item = normalize_line_error(row)
                if item:
                    out.append(item)
            if out:
                return out
        payload = (
            payload.get("error_report")
            or payload.get("raw")
            or payload.get("file")
            or ""
        )
    if isinstance(payload, (bytes, bytearray)):
        blob = bytes(payload)
        if blob[:4] == b"PK\x03\x04" or blob[:2] == b"PK":
            return _parse_xlsx_error_report(blob)
        text = blob.decode("utf-8-sig", errors="replace")
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
        rows.extend(_rows_from_mapped(mapped))
    return rows


def line_error_rows(data) -> list[dict]:
    """Error rows from a poll payload, including a stored error_report file."""
    if isinstance(data, dict):
        nested = data.get("line_errors")
        if isinstance(nested, list) and nested:
            rows = []
            for row in nested:
                item = normalize_line_error(row)
                if item:
                    rows.append(item)
            if rows:
                return rows
        report = data.get("error_report")
        if report:
            return parse_mirakl_error_report(report)
    return parse_mirakl_error_report(data)


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
