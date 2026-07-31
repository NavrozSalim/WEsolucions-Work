"""HTTP client for the MyDeal (WMP) Universal API.

Auth flow (API doc §0.4 / OpenIddict):
  1. POST /mydealaccesstoken with HTTP Basic (client_id:client_secret) and
     grant_type=client_credentials → bearer access_token
  2. Subsequent calls send Authorization: Bearer <token> plus SellerID / SellerToken
     headers for the active seller.

Base URL and secrets come from the Store's MyDeal fields (encrypted at rest).
Secrets are never logged.
"""
from __future__ import annotations

import logging
import time
from typing import Any
import requests

from ..errors import MarketplaceError

logger = logging.getLogger("listings.mydeal")

REQUEST_TIMEOUT = 30
TOKEN_SKEW_SECONDS = 60  # refresh a minute before expires_in


class MyDealResult:
    def __init__(
        self,
        ok: bool,
        data=None,
        error=None,
        message: str = "",
        status: int = 0,
        response_status: str = "",
    ):
        self.ok = ok
        self.data = data
        self.error = error
        self.message = message
        self.status = status
        self.response_status = response_status or ""

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "data": self.data,
            "error": self.error,
            "message": self.message,
            "status": self.status,
            "response_status": self.response_status,
        }


class MyDealClient:
    def __init__(self, store, environment: str | None = None, *, require_auth: bool = True):
        self.store = store
        self.environment = environment or (getattr(store, "mydeal_environment", None) or "sandbox")
        if self.environment not in ("sandbox", "production"):
            self.environment = "sandbox"

        if self.environment == "production":
            self.base_url = (store.mydeal_production_base_url or "").strip().rstrip("/")
            self._client_id = (store.mydeal_production_client_id or "").strip()
            self._client_secret = (store.mydeal_production_client_secret or "").strip()
            self._seller_id = (store.mydeal_production_seller_id or "").strip()
            self._seller_token = (store.mydeal_production_seller_token or "").strip()
        else:
            self.base_url = (store.mydeal_sandbox_base_url or "").strip().rstrip("/")
            self._client_id = (store.mydeal_sandbox_client_id or "").strip()
            self._client_secret = (store.mydeal_sandbox_client_secret or "").strip()
            self._seller_id = (store.mydeal_sandbox_seller_id or "").strip()
            self._seller_token = (store.mydeal_sandbox_seller_token or "").strip()

        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

        if require_auth:
            missing = []
            if not self.base_url:
                missing.append("base URL")
            if not self._client_id:
                missing.append("ClientID")
            if not self._client_secret:
                missing.append("ClientSecret")
            if not self._seller_id:
                missing.append("SellerID")
            if not self._seller_token:
                missing.append("SellerToken")
            if missing:
                raise MarketplaceError(
                    f"No {self.environment} MyDeal {', '.join(missing)} configured for store "
                    f"'{store.name}'. Add them in store settings."
                )

    def _url(self, path: str) -> str:
        path = path if path.startswith("/") else f"/{path}"
        return f"{self.base_url.rstrip('/')}{path}"

    def _seller_headers(self) -> dict[str, str]:
        return {
            "SellerID": self._seller_id,
            "SellerToken": self._seller_token,
            "api-version": "1",
            "Accept": "application/json",
        }

    def get_access_token(self, *, force: bool = False) -> str:
        """Obtain (or reuse cached) bearer token via client_credentials."""
        now = time.time()
        if (
            not force
            and self._access_token
            and self._token_expires_at
            and now < self._token_expires_at - TOKEN_SKEW_SECONDS
        ):
            return self._access_token

        url = self._url("/mydealaccesstoken")
        logger.info(
            "MyDeal token request store=%s env=%s",
            self.store.name,
            self.environment,
        )
        try:
            # OpenIddict (MyDeal sandbox/prod) expects client credentials via HTTP Basic
            # auth, not form-body client_id/client_secret (body-only → ID2029 missing client_id).
            resp = requests.post(
                url,
                data={"grant_type": "client_credentials"},
                auth=(self._client_id, self._client_secret),
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            logger.error("MyDeal token connection error: %s", exc)
            raise MarketplaceError("Could not reach MyDeal token endpoint. Check the base URL.") from exc

        body = _safe_json(resp)
        if not resp.ok:
            msg = _token_error_message(body, resp.status_code)
            logger.error(
                "MyDeal token error status=%s amzn=%s body=%s",
                resp.status_code,
                resp.headers.get("x-amzn-ErrorType") or "",
                body,
            )
            raise MarketplaceError(msg)

        token = ""
        expires_in = 3599
        if isinstance(body, dict):
            token = str(body.get("access_token") or "").strip()
            try:
                expires_in = int(body.get("expires_in") or 3599)
            except (TypeError, ValueError):
                expires_in = 3599
        if not token:
            raise MarketplaceError("MyDeal token response did not include access_token.")

        self._access_token = token
        self._token_expires_at = time.time() + max(expires_in, 60)
        return token

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: Any = None,
        data: Any = None,
        auth_seller: bool = True,
        retry_on_401: bool = True,
    ) -> MyDealResult:
        """Authenticated HTTP call. Does not log secrets."""
        try:
            token = self.get_access_token()
        except MarketplaceError as exc:
            return MyDealResult(ok=False, message=str(exc), status=0)

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        if auth_seller:
            headers.update(self._seller_headers())
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        url = self._url(path)
        logger.info(
            "MyDeal request store=%s env=%s method=%s path=%s",
            self.store.name,
            self.environment,
            method.upper(),
            path,
        )
        try:
            resp = requests.request(
                method.upper(),
                url,
                params=params,
                json=json_body,
                data=data,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            logger.error("MyDeal connection error path=%s: %s", path, exc)
            return MyDealResult(
                ok=False,
                error=str(exc),
                message="Could not reach MyDeal. Please try again.",
                status=0,
            )

        body = _safe_json(resp)
        if resp.status_code == 401 and retry_on_401:
            # Token may have expired early — refresh once.
            try:
                self.get_access_token(force=True)
            except MarketplaceError as exc:
                return MyDealResult(ok=False, data=body, message=str(exc), status=401)
            return self.request(
                method,
                path,
                params=params,
                json_body=json_body,
                data=data,
                auth_seller=auth_seller,
                retry_on_401=False,
            )

        response_status = ""
        if isinstance(body, dict):
            response_status = str(body.get("ResponseStatus") or body.get("responseStatus") or "")

        if resp.ok:
            # MyDeal often returns HTTP 200 even for business failures.
            if response_status.lower() in ("failed", "fail"):
                return MyDealResult(
                    ok=False,
                    data=body,
                    error=body,
                    message=_action_error_message(body) or "MyDeal request failed.",
                    status=resp.status_code,
                    response_status=response_status,
                )
            return MyDealResult(
                ok=True,
                data=body,
                message=_success_message(body, response_status),
                status=resp.status_code,
                response_status=response_status,
            )

        return MyDealResult(
            ok=False,
            data=body,
            error=body,
            message=_http_error_message(body, resp.status_code),
            status=resp.status_code,
            response_status=response_status,
        )

    def verify_connection(self) -> MyDealResult:
        """Smoke-test: obtain token, then list one product field."""
        try:
            self.get_access_token(force=True)
        except MarketplaceError as exc:
            return MyDealResult(ok=False, message=str(exc), status=0)

        # Fields is required by MyDeal — empty Fields yields an empty response.
        return self.request(
            "GET",
            "/products",
            params={
                "page": 1,
                "Limit": 1,
                "Fields": "ProductSKU,Title",
            },
        )

    def end_listings(self, items: list[dict]) -> MyDealResult:
        """Discontinue products on MyDeal via POST /products/listingstatus (NotLive).

        Each item: ``sku`` (required), optional ``external_product_id``,
        ``buyable_sku``, ``external_buyable_id``. For standalone products
        External* ids default to the same value as the SKU when omitted.
        """
        groups = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            product_sku = str(item.get("sku") or item.get("product_sku") or "").strip()
            if not product_sku:
                continue
            ext_pid = str(
                item.get("external_product_id")
                or item.get("ExternalProductID")
                or product_sku
            ).strip()
            buyable_sku = str(item.get("buyable_sku") or item.get("SKU") or product_sku).strip()
            ext_bid = str(
                item.get("external_buyable_id")
                or item.get("ExternalBuyableProductID")
                or ext_pid
            ).strip()
            groups.append(
                {
                    "ExternalProductID": ext_pid,
                    "ProductSKU": product_sku,
                    "BuyableProducts": [
                        {
                            "ExternalBuyableProductID": ext_bid,
                            "SKU": buyable_sku,
                            "ListingStatus": "NotLive",
                        }
                    ],
                }
            )
        if not groups:
            return MyDealResult(ok=False, message="SKU is required to end a MyDeal listing.")
        return self.request("POST", "/products/listingstatus", json_body=groups)

    def end_listing(
        self,
        *,
        sku: str,
        external_product_id: str | None = None,
        buyable_sku: str | None = None,
        external_buyable_id: str | None = None,
    ) -> MyDealResult:
        """End a single MyDeal product (set ListingStatus=NotLive)."""
        return self.end_listings(
            [
                {
                    "sku": sku,
                    "external_product_id": external_product_id,
                    "buyable_sku": buyable_sku,
                    "external_buyable_id": external_buyable_id,
                }
            ]
        )

    # --- Products ---

    def upsert_products(self, product_groups: list[dict]) -> MyDealResult:
        """Create or update products via POST /products (async pending-responses)."""
        if not product_groups:
            return MyDealResult(ok=False, message="No products to send.")
        return self.request("POST", "/products", json_body=product_groups)

    def update_price_quantity(self, product_groups: list[dict]) -> MyDealResult:
        """Update price/qty via POST /products/priceandquantity (doc §0.5.4)."""
        if not product_groups:
            return MyDealResult(ok=False, message="No products to update.")
        # Endpoint name varies slightly across doc revisions — try common path.
        return self.request("POST", "/products/priceandquantity", json_body=product_groups)

    def get_pending_response(self, work_item_id: str) -> MyDealResult:
        wid = str(work_item_id or "").strip()
        if not wid:
            return MyDealResult(ok=False, message="workItemId is required.")
        return self.request(
            "GET",
            "/pending-responses",
            params={"workItemId": wid},
        )

    def get_categories(self, *, page: int = 1, limit: int = 250) -> MyDealResult:
        return self.request(
            "GET",
            "/categories",
            params={"page": page, "Limit": limit},
        )

    # --- Orders ---

    def list_orders(
        self,
        *,
        order_status: str = "All",
        page: int = 1,
        limit: int = 100,
    ) -> MyDealResult:
        """GET /orders?orderStatus=&Page=&Limit="""
        return self.request(
            "GET",
            "/orders",
            params={
                "orderStatus": order_status or "All",
                "Page": max(1, int(page or 1)),
                "Limit": min(250, max(1, int(limit or 100))),
            },
        )

    def get_order(self, order_id) -> MyDealResult:
        oid = str(order_id or "").strip()
        if not oid:
            return MyDealResult(ok=False, message="Order id is required.")
        return self.request("GET", f"/orders/{oid}")

    def list_unfulfilled(self, *, limit: int = 100) -> MyDealResult:
        """GET /orders/unfulfilled — ReadytoFulfill orders not yet acknowledged."""
        return self.request(
            "GET",
            "/orders/unfulfilled",
            params={"Limit": min(250, max(1, int(limit or 100)))},
        )

    def acknowledge_order(self, order_id) -> MyDealResult:
        oid = str(order_id or "").strip()
        if not oid:
            return MyDealResult(ok=False, message="Order id is required.")
        return self.request("POST", f"/orders/{oid}/acknowledge")

    def fulfill_orders(self, fulfillments: list[dict]) -> MyDealResult:
        """POST /orders/fulfill — array of OrderFulfillment items."""
        if not fulfillments:
            return MyDealResult(ok=False, message="No fulfillments to send.")
        return self.request("POST", "/orders/fulfill", json_body=fulfillments)

    def cancel_order(self, order_id, cancellation: dict) -> MyDealResult:
        """POST /orders/{id}/cancel — OrderCancellation body."""
        oid = str(order_id or "").strip()
        if not oid:
            return MyDealResult(ok=False, message="Order id is required.")
        if not isinstance(cancellation, dict):
            return MyDealResult(ok=False, message="Cancellation payload is required.")
        return self.request("POST", f"/orders/{oid}/cancel", json_body=cancellation)

    def refund_order(self, order_id, refund: dict) -> MyDealResult:
        """POST /orders/{id}/refund — OrderRefund body (shipped orders)."""
        oid = str(order_id or "").strip()
        if not oid:
            return MyDealResult(ok=False, message="Order id is required.")
        if not isinstance(refund, dict):
            return MyDealResult(ok=False, message="Refund payload is required.")
        return self.request("POST", f"/orders/{oid}/refund", json_body=refund)


def _safe_json(resp):
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return {"raw": (resp.text or "")[:2000]}


def _token_error_message(body, status: int) -> str:
    if isinstance(body, dict):
        for key in ("error_description", "error", "message", "Message", "title"):
            val = body.get(key)
            if val:
                text = str(val).strip()
                # AWS API Gateway ForbiddenException — usually IP allowlist / private API,
                # not invalid ClientID (docs say auth failures return HTTP 400).
                if status == 403 and text.lower() == "forbidden":
                    return (
                        "MyDeal API Gateway returned Forbidden (403). "
                        "This usually means your IP is not allowlisted for sandbox, "
                        "not that ClientID/Secret are wrong. Ask MyDeal to whitelist "
                        "this machine/server IP, or retest from an allowlisted host."
                    )
                return text[:400]
        errors = body.get("Errors") or body.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict) and first.get("Message"):
                return str(first["Message"])[:400]
    return f"MyDeal rejected client credentials (HTTP {status})."


def _action_error_message(body: dict) -> str:
    errors = body.get("Errors") or body.get("errors") or []
    if isinstance(errors, list):
        for err in errors:
            if isinstance(err, dict):
                msg = err.get("Message") or err.get("message")
                if msg:
                    return str(msg)[:400]
    for key in ("message", "Message", "error", "detail"):
        if body.get(key):
            return str(body[key])[:400]
    return ""


def _http_error_message(body, status: int) -> str:
    if isinstance(body, dict):
        msg = _action_error_message(body)
        if msg:
            return msg
        for key in ("message", "Message", "error", "title", "detail"):
            if body.get(key):
                return str(body[key])[:400]
    return f"MyDeal returned an error (HTTP {status})."


def _success_message(body, response_status: str) -> str:
    if response_status:
        return f"MyDeal response: {response_status}."
    if isinstance(body, dict) and body.get("message"):
        return str(body["message"])
    return "Success."
