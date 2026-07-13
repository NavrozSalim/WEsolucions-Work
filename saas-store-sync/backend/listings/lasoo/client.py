"""HTTP client for the LasooConnect API, built from a Store row.

Base URL and AuthKey come from the Store's Lasoo fields (encrypted at rest),
so each managed store can use its own configuration. Requests/responses are
logged but the AuthKey never is.
"""
import logging

import requests

from ..errors import MarketplaceError
from .queries import (
    DEFAULT_ENDPOINTS,
    DEFAULT_PRODUCTION_BASE_URL,
    DEFAULT_STAGING_BASE_URL,
    extract_lasoo_failure_message,
)

logger = logging.getLogger("listings.lasoo")

REQUEST_TIMEOUT = 30


class LasooResult:
    def __init__(self, ok: bool, data=None, error=None, message: str = "", status: int = 0):
        self.ok = ok
        self.data = data
        self.error = error
        self.message = message
        self.status = status

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "data": self.data,
            "error": self.error,
            "message": self.message,
            "status": self.status,
        }


class LasooClient:
    def __init__(self, store, environment: str | None = None, *, require_auth: bool = True):
        self.store = store
        self.environment = environment or (store.lasoo_environment or 'staging')

        if self.environment == 'production':
            self.base_url = (store.lasoo_production_base_url or '').strip() or DEFAULT_PRODUCTION_BASE_URL
            self._auth_key = (store.lasoo_production_auth_key or '').strip()
        else:
            self.base_url = (store.lasoo_staging_base_url or '').strip() or DEFAULT_STAGING_BASE_URL
            self._auth_key = (store.lasoo_staging_auth_key or '').strip()

        if require_auth and not self._auth_key:
            raise MarketplaceError(
                f"No {self.environment} Lasoo AuthKey configured for store "
                f"'{store.name}'. Add it in store settings."
            )

    def url(self, endpoint_key: str) -> str:
        path = DEFAULT_ENDPOINTS.get(endpoint_key, "")
        if not path:
            raise MarketplaceError(f"Lasoo endpoint '{endpoint_key}' is not configured.")
        return f"{self.base_url.rstrip('/')}{path}"

    @property
    def auth_key(self) -> str:
        return self._auth_key

    def send(self, endpoint_key: str, payload: dict) -> LasooResult:
        url = self.url(endpoint_key)
        logger.info(
            "Lasoo request store=%s env=%s endpoint=%s query=%s",
            self.store.name,
            self.environment,
            endpoint_key,
            payload.get("query"),
        )

        try:
            resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            logger.error("Lasoo connection error endpoint=%s: %s", endpoint_key, exc)
            return LasooResult(
                ok=False,
                error=str(exc),
                message="Could not reach Lasoo. Please try again.",
                status=0,
            )

        body = _safe_body(resp)
        if resp.ok:
            failure = extract_lasoo_failure_message(body)
            if failure:
                logger.error(
                    "Lasoo business error store=%s status=%s body=%s",
                    self.store.name, resp.status_code, body,
                )
                return LasooResult(
                    ok=False, data=body, error=body, message=failure, status=resp.status_code,
                )
            logger.info("Lasoo response store=%s status=%s ok", self.store.name, resp.status_code)
            return LasooResult(ok=True, data=body, message=_success_message(body), status=resp.status_code)

        logger.error(
            "Lasoo API error store=%s status=%s body=%s",
            self.store.name, resp.status_code, body,
        )
        return LasooResult(
            ok=False, error=body, message=_user_message(body, resp.status_code), status=resp.status_code,
        )


def _safe_body(resp):
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return {"raw": resp.text[:2000]}


def _user_message(body, status: int) -> str:
    if isinstance(body, dict):
        for field in ("message", "error", "detail", "title"):
            if body.get(field):
                return str(body[field])
    return f"Lasoo returned an error (HTTP {status})."


def _success_message(body) -> str:
    if isinstance(body, dict):
        if body.get("success") is True:
            msg = body.get("message")
            if isinstance(msg, str) and msg.strip() and msg.strip().lower() != "no message":
                return msg.strip()
    return "Success."
