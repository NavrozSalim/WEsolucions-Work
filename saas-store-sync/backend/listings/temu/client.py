"""HTTP client for the Temu Partner Open API (AU / Global router).

All calls are ``POST {router}/openapi/router`` with a single flat JSON body that
carries both the common parameters and the business parameters:

    type          API name, e.g. bg.local.goods.list.query
    app_key       Partner / self-developed app key
    access_token  Per-mall token from bg.open.accesstoken.create
    timestamp     10-digit UNIX seconds (±300s window)
    data_type     JSON
    sign          uppercase MD5 of app_secret + sorted(k+v) + app_secret

Signature (per Temu "Signature Method for API request"):
  1. sort every request parameter by key, ASCII ascending
  2. concatenate ``key`` then ``value`` with no separator
  3. wrap the long string with ``app_secret`` at head and tail
  4. MD5, then uppercase

AU sellers use the Global router. US/EU hosts are intentionally not supported
here — a Temu AU token is rejected by the regional US/EU endpoints.

Secrets are never logged.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time

import requests

from ..errors import MarketplaceError

logger = logging.getLogger("listings.temu")

REQUEST_TIMEOUT = 60

# AU / Global sellers. Do not point this at openapi-b-us / openapi-b-eu.
DEFAULT_BASE_URL = "https://openapi-b-global.temu.com"
ROUTER_PATH = "/openapi/router"

# Seller Center used for the AU authorization redirect.
AU_SELLER_CENTER = "https://au.seller.temu.com"
AUTHORIZE_PATH = "/open-platform/client-manage/authorization"

SUCCESS_CODE = 1000000

# errorCode values that mean "credentials / token are not usable".
AUTH_ERROR_CODES = frozenset({
    2000000,  # illegal app_key / sign
    2000010,
    3000000,  # token invalid / expired
    3000001,
    3000002,
})

_AUTH_ERROR_MARKERS = (
    "access_token",
    "accesstoken",
    "app_key",
    "appkey",
    "sign",
    "not authorized",
    "unauthorized",
    "token is invalid",
    "token expired",
    "no permission",
)


class TemuResult:
    """Normalized Temu response: ``ok`` already accounts for errorCode."""

    def __init__(
        self,
        ok: bool,
        data=None,
        message: str = "",
        status: int = 0,
        error_code=None,
        request_id: str = "",
    ):
        self.ok = ok
        self.data = data
        self.message = message
        self.status = status
        self.error_code = error_code
        self.request_id = request_id or ""

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "data": self.data,
            "message": self.message,
            "status": self.status,
            "error_code": self.error_code,
            "request_id": self.request_id,
        }

    @property
    def is_auth_error(self) -> bool:
        if self.status in (401, 403):
            return True
        try:
            if self.error_code is not None and int(self.error_code) in AUTH_ERROR_CODES:
                return True
        except (TypeError, ValueError):
            pass
        msg = (self.message or "").lower()
        return any(marker in msg for marker in _AUTH_ERROR_MARKERS)


def sign_payload(params: dict, app_secret: str) -> str:
    """Uppercase MD5 signature for a Temu request body (``sign`` is excluded)."""
    parts = []
    for key in sorted(k for k in params if k != "sign"):
        parts.append(str(key))
        parts.append(_sign_value(params[key]))
    raw = f"{app_secret}{''.join(parts)}{app_secret}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest().upper()


def _sign_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    return str(value)


def authorize_url(app_key: str, redirect_uri: str, state: str = "") -> str:
    """AU Seller Center URL the seller opens to authorize this app."""
    from urllib.parse import urlencode

    query = {"appKey": (app_key or "").strip(), "redirect_uri": (redirect_uri or "").strip()}
    if (state or "").strip():
        query["state"] = state.strip()
    return f"{AU_SELLER_CENTER}{AUTHORIZE_PATH}?{urlencode(query)}"


class TemuClient:
    def __init__(self, store, *, require_auth: bool = True, credentials: dict | None = None):
        self.store = store
        creds = credentials or {}
        self.region = (
            str(creds.get("region") or getattr(store, "temu_region", None) or "au").strip().lower()
            or "au"
        )
        base = str(
            creds.get("base_url") or getattr(store, "temu_base_url", None) or ""
        ).strip().rstrip("/")
        self.base_url = base or DEFAULT_BASE_URL
        self._app_key = str(creds.get("app_key") or getattr(store, "temu_app_key", None) or "").strip()
        self._app_secret = str(
            creds.get("app_secret") or getattr(store, "temu_app_secret", None) or ""
        ).strip()
        self._access_token = str(
            creds.get("access_token") or getattr(store, "temu_access_token", None) or ""
        ).strip()
        self.mall_id = str(creds.get("mall_id") or getattr(store, "temu_mall_id", None) or "").strip()

        if require_auth:
            missing = []
            if not self._app_key:
                missing.append("App Key")
            if not self._app_secret:
                missing.append("App Secret")
            if not self._access_token:
                missing.append("Access Token")
            if missing:
                raise MarketplaceError(
                    f"No Temu {', '.join(missing)} configured for store "
                    f"'{getattr(store, 'name', store)}'. Add them in store settings, "
                    "or use Connect Temu to authorize the app."
                )

    @property
    def app_key(self) -> str:
        return self._app_key

    @property
    def router_url(self) -> str:
        return f"{self.base_url}{ROUTER_PATH}"

    def build_body(self, api_type: str, data: dict | None = None, *, access_token: str | None = None) -> dict:
        body = {k: v for k, v in (data or {}).items() if v is not None}
        body.update(
            {
                "type": api_type,
                "app_key": self._app_key,
                "data_type": "JSON",
                "timestamp": str(int(time.time())),
            }
        )
        token = self._access_token if access_token is None else access_token
        if token:
            body["access_token"] = token
        body["sign"] = sign_payload(body, self._app_secret)
        return body

    def call(
        self,
        api_type: str,
        data: dict | None = None,
        *,
        access_token: str | None = None,
        timeout: int | None = None,
    ) -> TemuResult:
        body = self.build_body(api_type, data, access_token=access_token)
        logger.info(
            "Temu request store=%s region=%s type=%s",
            getattr(self.store, "name", ""),
            self.region,
            api_type,
        )
        try:
            response = requests.post(
                self.router_url,
                json=body,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                timeout=timeout if timeout is not None else REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            logger.warning("Temu network error type=%s: %s", api_type, exc)
            return TemuResult(
                ok=False,
                message="Could not reach the Temu Open API. Check the router URL and try again.",
                status=0,
            )

        payload = _safe_json(response)
        return _interpret(payload, response.status_code, api_type)

    # ------------------------------------------------------------------ auth

    def create_access_token(self, code: str) -> TemuResult:
        """bg.open.accesstoken.create — exchange the callback ``code`` for a token.

        Temu's first exchange expects ``access_token`` to equal ``code``.
        """
        token = (code or "").strip()
        if not token:
            return TemuResult(ok=False, message="Temu authorization code is required.")
        return self.call(
            "bg.open.accesstoken.create",
            {"code": token},
            access_token=token,
        )

    def token_info(self) -> TemuResult:
        return self.call("bg.open.accesstoken.info.get")

    def verify_connection(self) -> TemuResult:
        """Confirm app key + secret + token by asking Temu for one product page.

        An empty catalog is still a successful connection. ``token_info`` is
        tried first because it needs no goods scope.
        """
        info = self.token_info()
        if info.ok:
            mall = _first_mall_id(info.data)
            if mall:
                self.mall_id = mall
            return TemuResult(
                ok=True,
                data=info.data,
                message=f"Temu {self.region.upper()} connection successful.",
                status=info.status,
                error_code=info.error_code,
                request_id=info.request_id,
            )
        if info.is_auth_error:
            return info

        goods = self.list_goods(page_size=1)
        if goods.ok:
            return TemuResult(
                ok=True,
                data=goods.data,
                message=f"Temu {self.region.upper()} connection successful.",
                status=goods.status,
                error_code=goods.error_code,
                request_id=goods.request_id,
            )
        return goods

    # ------------------------------------------------------------------ goods

    def list_goods(self, *, page: int = 1, page_size: int = 20, search_text: str = "") -> TemuResult:
        data = {
            "pageNo": max(1, int(page or 1)),
            "pageSize": max(1, min(int(page_size or 20), 100)),
        }
        if (search_text or "").strip():
            data["searchText"] = search_text.strip()
            data["goodsSearchType"] = 1
        return self.call("bg.local.goods.list.query", data)

    def list_skus(self, goods_id) -> TemuResult:
        gid = str(goods_id or "").strip()
        if not gid:
            return TemuResult(ok=False, message="Temu goodsId is required.")
        return self.call("bg.local.goods.sku.list.query", {"goodsId": gid})

    def add_goods(self, payload: dict) -> TemuResult:
        return self.call("bg.local.goods.add", payload, timeout=90)

    def update_goods(self, payload: dict) -> TemuResult:
        return self.call("bg.local.goods.update", payload, timeout=90)

    def edit_stock(self, sku_stock_changes: list[dict]) -> TemuResult:
        """bg.local.goods.stock.edit — per-SKU warehouse stock updates."""
        if not sku_stock_changes:
            return TemuResult(ok=False, message="No stock changes to send.")
        return self.call(
            "bg.local.goods.stock.edit",
            {"skuStockChangeList": sku_stock_changes},
        )

    def change_sku_price(self, price_changes: list[dict]) -> TemuResult:
        if not price_changes:
            return TemuResult(ok=False, message="No price changes to send.")
        return self.call(
            "bg.local.goods.priceorder.change.sku.price",
            {"skuPriceList": price_changes},
        )

    def set_sale_status(self, goods_id, *, on_sale: bool) -> TemuResult:
        """bg.local.goods.sale.status.set — 1 = on sale, 0 = off sale."""
        gid = str(goods_id or "").strip()
        if not gid:
            return TemuResult(ok=False, message="Temu goodsId is required.")
        return self.call(
            "bg.local.goods.sale.status.set",
            {"goodsId": gid, "saleStatus": 1 if on_sale else 0},
        )

    def delete_goods(self, goods_id) -> TemuResult:
        gid = str(goods_id or "").strip()
        if not gid:
            return TemuResult(ok=False, message="Temu goodsId is required.")
        return self.call("temu.local.goods.delete", {"goodsId": gid})

    def list_categories(self, parent_id=0) -> TemuResult:
        return self.call("bg.local.goods.cats.get", {"parentCatId": int(parent_id or 0)})

    def list_warehouses(self) -> TemuResult:
        return self.call("bg.logistics.warehouse.list.get")

    # ----------------------------------------------------------------- orders

    def list_orders(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        create_after=None,
        create_before=None,
        order_status=None,
    ) -> TemuResult:
        data = {
            "pageNumber": max(1, int(page or 1)),
            "pageSize": max(1, min(int(page_size or 50), 100)),
        }
        if create_after is not None:
            data["createAfter"] = int(create_after)
        if create_before is not None:
            data["createBefore"] = int(create_before)
        if order_status is not None:
            data["orderStatus"] = order_status
        return self.call("bg.order.list.v2.get", data)

    def get_order(self, parent_order_sn: str) -> TemuResult:
        sn = (parent_order_sn or "").strip()
        if not sn:
            return TemuResult(ok=False, message="Temu parentOrderSn is required.")
        return self.call("bg.order.detail.v2.get", {"parentOrderSnList": [sn]})

    def get_shipping_info(self, parent_order_sn: str) -> TemuResult:
        sn = (parent_order_sn or "").strip()
        if not sn:
            return TemuResult(ok=False, message="Temu parentOrderSn is required.")
        return self.call("bg.order.shippinginfo.v2.get", {"parentOrderSnList": [sn]})

    def decrypt_shipping_info(self, parent_order_sn: str) -> TemuResult:
        """bg.order.decryptshippinginfo.get — buyer address arrives encrypted."""
        sn = (parent_order_sn or "").strip()
        if not sn:
            return TemuResult(ok=False, message="Temu parentOrderSn is required.")
        return self.call("bg.order.decryptshippinginfo.get", {"parentOrderSnList": [sn]})

    def cancel_out_of_stock(self, parent_order_sn: str, *, reason: str = "") -> TemuResult:
        """temu.order.cancel.outofstock.apply — seller pre-ship cancellation."""
        sn = (parent_order_sn or "").strip()
        if not sn:
            return TemuResult(ok=False, message="Temu parentOrderSn is required.")
        data = {"parentOrderSn": sn}
        if (reason or "").strip():
            data["cancelReason"] = reason.strip()
        return self.call("temu.order.cancel.outofstock.apply", data)

    # -------------------------------------------------------------- logistics

    def list_shipping_companies(self) -> TemuResult:
        return self.call("bg.logistics.companies.get")

    def create_shipment(self, payload: dict) -> TemuResult:
        return self.call("bg.logistics.shipment.create", payload)

    def confirm_shipment(self, payload: dict) -> TemuResult:
        return self.call("bg.logistics.shipment.confirm", payload)


def _safe_json(response):
    try:
        return response.json()
    except ValueError:
        return {"raw": (response.text or "")[:2000]}


def _interpret(payload, http_status: int, api_type: str) -> TemuResult:
    """Temu answers HTTP 200 with errorCode for business failures."""
    if not isinstance(payload, dict):
        return TemuResult(
            ok=False,
            data=payload,
            message=f"Unexpected Temu response for {api_type}.",
            status=http_status,
        )

    request_id = str(payload.get("requestId") or "")
    code = payload.get("errorCode")
    if code is None:
        code = payload.get("error_code")
    message = str(payload.get("errorMsg") or payload.get("error_msg") or "").strip()
    result = payload.get("result")
    if result is None:
        result = payload.get("data")

    success = payload.get("success")
    ok = False
    try:
        ok = int(code) == SUCCESS_CODE
    except (TypeError, ValueError):
        ok = bool(success) and http_status < 400
    if code is None and success is None:
        ok = http_status < 400

    if ok:
        return TemuResult(
            ok=True,
            data=result if result is not None else payload,
            message=message,
            status=http_status,
            error_code=code,
            request_id=request_id,
        )

    detail = message or _nested_error(payload) or f"Temu rejected {api_type}"
    if code is not None:
        detail = f"{detail} (errorCode {code})"
    return TemuResult(
        ok=False,
        data=result if result is not None else payload,
        message=detail[:600],
        status=http_status,
        error_code=code,
        request_id=request_id,
    )


def _nested_error(payload: dict) -> str:
    for key in ("message", "msg", "detail", "raw"):
        val = payload.get(key)
        if val:
            return str(val)[:400]
    return ""


def _first_mall_id(data) -> str:
    if isinstance(data, dict):
        for key in ("mallId", "mall_id", "mallIdList"):
            val = data.get(key)
            if isinstance(val, list):
                val = val[0] if val else None
            if val not in (None, ""):
                return str(val).strip()
    return ""


def extract_access_token(data) -> tuple[str, str]:
    """Pull (access_token, mall_id) out of a bg.open.accesstoken.create result."""
    if not isinstance(data, dict):
        return "", ""
    token = ""
    for key in ("accessToken", "access_token", "token"):
        val = data.get(key)
        if val not in (None, ""):
            token = str(val).strip()
            break
    return token, _first_mall_id(data)
