"""HTTP client for the MyDeal (WMP) Universal API.

Auth flow (API doc §0.4):
  1. POST /mydealaccesstoken with client_id + client_secret → bearer access_token
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
            # Doc §0.4.1 lists client_Id (capital I). Also send client_id for OAuth-style gateways.
            resp = requests.post(
                url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_Id": self._client_id,
                    "client_secret": self._client_secret,
                },
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
