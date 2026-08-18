"""Shopify Admin GraphQL client using the client-credentials grant.

Dev Dashboard apps no longer expose a permanent Admin token. We store
client_id + client_secret and request a ~24h access token as needed.
"""
from __future__ import annotations

import logging
from datetime import timedelta

import requests
from django.utils import timezone

logger = logging.getLogger("listings.shopify")

API_VERSION = "2025-10"
TOKEN_SKEW = timedelta(minutes=2)
HTTP_TIMEOUT = 30


class ShopifyError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def normalize_shop_domain(raw: str) -> str:
    text = (raw or "").strip().lower()
    text = text.replace("https://", "").replace("http://", "")
    text = text.split("?")[0].strip().rstrip(".")
    if "admin.shopify.com/store/" in text:
        handle = text.split("/store/", 1)[1].split("/")[0].strip()
        if handle:
            return f"{handle}.myshopify.com"
    text = text.split("/")[0].strip().rstrip(".")
    if text and "." not in text:
        text = f"{text}.myshopify.com"
    return text


def shop_handle(domain: str) -> str:
    domain = normalize_shop_domain(domain)
    if domain.endswith(".myshopify.com"):
        return domain[: -len(".myshopify.com")]
    return domain.split(".")[0] if domain else ""


def normalize_location_id(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    if "gid://shopify/Location/" in text:
        return text.rsplit("/", 1)[-1].strip()
    if "/locations/" in text:
        return text.rstrip("/").rsplit("/", 1)[-1].split("?")[0].strip()
    return text


def location_gid(raw: str) -> str:
    loc = normalize_location_id(raw)
    if not loc:
        return ""
    if loc.startswith("gid://"):
        return loc
    return f"gid://shopify/Location/{loc}"


def numeric_id_from_gid(gid: str) -> str:
    text = (gid or "").strip()
    if not text:
        return ""
    return text.rsplit("/", 1)[-1]


def store_shopify_ready(store) -> bool:
    if store is None or not getattr(store, "shopify_enabled", False):
        return False
    domain = normalize_shop_domain(getattr(store, "shopify_shop_domain", "") or "")
    client_id = (getattr(store, "shopify_client_id", None) or "").strip()
    secret = (getattr(store, "shopify_client_secret", None) or "").strip()
    return bool(domain and client_id and secret)


def _token_url(domain: str) -> str:
    return f"https://{normalize_shop_domain(domain)}/admin/oauth/access_token"


def _graphql_url(domain: str) -> str:
    return f"https://{normalize_shop_domain(domain)}/admin/api/{API_VERSION}/graphql.json"


def get_access_token(store) -> str:
    """Return a valid Admin API token, refreshing when expired."""
    existing = (getattr(store, "shopify_access_token", None) or "").strip()
    expires = getattr(store, "shopify_token_expires_at", None)
    now = timezone.now()
    if existing and expires and expires > now + TOKEN_SKEW:
        return existing

    domain = normalize_shop_domain(getattr(store, "shopify_shop_domain", "") or "")
    client_id = (getattr(store, "shopify_client_id", None) or "").strip()
    client_secret = (getattr(store, "shopify_client_secret", None) or "").strip()
    if not domain or not client_id or not client_secret:
        raise ShopifyError("Shopify is enabled but shop domain, Client ID, or Client secret is missing.")

    try:
        resp = requests.post(
            _token_url(domain),
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=HTTP_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise ShopifyError(f"Could not reach Shopify token endpoint: {exc}") from exc

    if resp.status_code >= 400:
        detail = (resp.text or "")[:240]
        raise ShopifyError(
            f"Shopify token request failed ({resp.status_code}). {detail}".strip(),
            status_code=resp.status_code,
        )
    try:
        payload = resp.json()
    except ValueError as exc:
        raise ShopifyError("Shopify token response was not JSON.") from exc
    token = str(payload.get("access_token") or "").strip()
    if not token:
        raise ShopifyError("Shopify did not return an access token. Install the app on the shop first.")
    expires_in = payload.get("expires_in")
    try:
        seconds = int(expires_in) if expires_in is not None else 86399
    except (TypeError, ValueError):
        seconds = 86399
    store.shopify_access_token = token
    store.shopify_token_expires_at = now + timedelta(seconds=max(60, seconds))
    store.save(update_fields=["shopify_access_token", "shopify_token_expires_at", "updated_at"])
    return token


def graphql(store, query: str, variables: dict | None = None) -> dict:
    domain = normalize_shop_domain(getattr(store, "shopify_shop_domain", "") or "")
    token = get_access_token(store)
    try:
        resp = requests.post(
            _graphql_url(domain),
            json={"query": query, "variables": variables or {}},
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Access-Token": token,
            },
            timeout=HTTP_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise ShopifyError(f"Shopify GraphQL request failed: {exc}") from exc
    if resp.status_code >= 400:
        raise ShopifyError(
            f"Shopify GraphQL HTTP {resp.status_code}: {(resp.text or '')[:240]}",
            status_code=resp.status_code,
        )
    try:
        payload = resp.json()
    except ValueError as exc:
        raise ShopifyError("Shopify GraphQL response was not JSON.") from exc
    errors = payload.get("errors")
    if errors:
        first = errors[0] if isinstance(errors, list) and errors else errors
        message = first.get("message") if isinstance(first, dict) else str(first)
        raise ShopifyError(f"Shopify GraphQL error: {message}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ShopifyError("Shopify GraphQL returned no data.")
    return data
