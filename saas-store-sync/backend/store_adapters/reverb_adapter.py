"""
Reverb API adapter for Wesolutions.
Uses Bearer token auth. Base URL: api.reverb.com (production only — no sandbox in our flow).
"""
import logging
from decimal import Decimal
from urllib.parse import quote

import requests

from .base import BaseStoreAdapter

logger = logging.getLogger(__name__)

REVERB_API_BASE = "https://api.reverb.com"

HEADERS = {
    "Content-Type": "application/hal+json",
    "Accept": "application/hal+json",
    "Accept-Version": "3.0",
}

# Allowed selling-order list endpoints (see Reverb Order Retrieval guide).
_ORDER_LIST_ENDPOINTS = frozenset({"all", "unpaid", "awaiting_shipment"})


class ReverbAPIError(Exception):
    """Reverb API call failed."""
    def __init__(self, message, status_code=None, response_body=None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class ReverbAdapter(BaseStoreAdapter):
    """Reverb API adapter. Listings + order retrieval for managed stores."""

    def __init__(self, store):
        super().__init__(store)
        # Always production Reverb — we do not use sandbox.reverb.com.
        self._base_url = REVERB_API_BASE
        self._session = requests.Session()
        self._session.headers.update(HEADERS)
        if self._token:
            self._session.headers["Authorization"] = f"Bearer {self._token}"

    def _request(self, method, path, json=None, params=None, timeout=30):
        url = path if str(path).startswith("http") else f"{self._base_url}{path}"
        try:
            resp = self._session.request(
                method, url, json=json, params=params, timeout=timeout,
            )
        except requests.RequestException as e:
            raise ReverbAPIError(str(e))
        if resp.status_code >= 400:
            detail = ""
            try:
                body = resp.json() if resp.text else {}
                if isinstance(body, dict):
                    detail = (
                        body.get("message")
                        or body.get("error")
                        or body.get("detail")
                        or ""
                    )
                    if not detail and isinstance(body.get("errors"), dict):
                        detail = "; ".join(
                            f"{k}: {v}" for k, v in body["errors"].items()
                        )
            except Exception:  # noqa: BLE001
                detail = (resp.text or "")[:300]
            msg = f"Reverb API {method} {path}: {resp.status_code}"
            if detail:
                msg = f"{msg} — {detail}"
            raise ReverbAPIError(
                msg,
                status_code=resp.status_code,
                response_body=resp.text[:500] if resp.text else None,
            )
        return resp.json() if resp.text else None

    def validate_connection(self):
        """Validate API token by calling /api/shop."""
        if not self._token or len(str(self._token)) < 10:
            return False
        try:
            self._request("GET", "/api/shop")
            return True
        except ReverbAPIError:
            return False

    def lookup_listing_by_sku(self, sku: str) -> str | None:
        """Find listing ID by SKU. Returns listing ID (UUID) or None."""
        try:
            data = self._request("GET", f"/api/my/listings?sku={quote(sku)}&state=all")
        except ReverbAPIError:
            return None
        if not data:
            return None
        listings = data.get("listings")
        if not listings and isinstance(data.get("_embedded"), dict):
            listings = data["_embedded"].get("listings")
        if isinstance(listings, list) and listings:
            first = listings[0]
            lid = first.get("id") or first.get("uuid") if isinstance(first, dict) else None
            return str(lid) if lid else None
        return None

    def get_orders_selling(
        self,
        *,
        endpoint: str = "all",
        updated_start_date: str | None = None,
        updated_end_date: str | None = None,
        page: int | None = None,
        extra_query: dict | None = None,
    ) -> dict:
        """
        GET /api/my/orders/selling/{all|unpaid|awaiting_shipment}.

        ``updated_start_date`` / ``updated_end_date`` are ISO 8601 UTC strings.
        Pagination: pass ``page`` when the API accepts it; also follow
        ``_links.next.href`` from the previous response when available.
        """
        if endpoint not in _ORDER_LIST_ENDPOINTS:
            raise ValueError(f"Unsupported Reverb order endpoint: {endpoint!r}")
        params: dict = {}
        if updated_start_date:
            params["updated_start_date"] = updated_start_date
        if updated_end_date:
            params["updated_end_date"] = updated_end_date
        if page is not None:
            params["page"] = int(page)
        if extra_query:
            for key, value in extra_query.items():
                if value is not None:
                    params[key] = value
        path = f"/api/my/orders/selling/{endpoint}"
        data = self._request("GET", path, params=params or None)
        return data if isinstance(data, dict) else {"orders": []}

    def get_order_selling(self, order_id: str) -> dict | None:
        """GET /api/my/orders/selling/{order_id}."""
        oid = (order_id or "").strip()
        if not oid:
            return None
        data = self._request("GET", f"/api/my/orders/selling/{quote(oid)}")
        return data if isinstance(data, dict) else None

    def iter_orders_selling_all(
        self,
        *,
        updated_start_date: str | None = None,
        updated_end_date: str | None = None,
        max_pages: int = 100,
    ):
        """
        Yield every order from selling/all across pages.

        Prefers HAL ``_links.next.href`` when present; otherwise increments
        ``page`` until ``current_page >= total_pages`` or a page returns no orders.
        """
        page = 1
        next_url = None
        for _ in range(max_pages):
            if next_url:
                data = self._request("GET", next_url)
            else:
                data = self.get_orders_selling(
                    endpoint="all",
                    updated_start_date=updated_start_date,
                    updated_end_date=updated_end_date,
                    page=page,
                )
            if not isinstance(data, dict):
                break
            orders = data.get("orders")
            if not isinstance(orders, list):
                embedded = data.get("_embedded")
                orders = embedded.get("orders") if isinstance(embedded, dict) else None
            if not isinstance(orders, list):
                orders = []
            for order in orders:
                if isinstance(order, dict):
                    yield order

            links = data.get("_links") if isinstance(data.get("_links"), dict) else {}
            next_link = links.get("next") if isinstance(links, dict) else None
            next_href = None
            if isinstance(next_link, dict):
                next_href = next_link.get("href")
            elif isinstance(next_link, str):
                next_href = next_link

            current = int(data.get("current_page") or page or 1)
            total_pages = int(data.get("total_pages") or current)

            if next_href:
                next_url = next_href
                page = current + 1
                continue

            next_url = None
            if current >= total_pages or not orders:
                break
            page = current + 1

    # --- Conversations / messages (Tickets) -------------------------------- #

    def list_conversations(self, *, unread_only: bool = False, page: int | None = None) -> dict:
        """GET /api/my/conversations."""
        params: dict = {}
        if unread_only:
            params["unread_only"] = "true"
        if page is not None:
            params["page"] = int(page)
        data = self._request("GET", "/api/my/conversations", params=params or None)
        return data if isinstance(data, dict) else {"conversations": []}

    def get_conversation(self, conversation_id: str) -> dict | None:
        """GET /api/my/conversations/{id} — includes messages."""
        cid = str(conversation_id or "").strip()
        if not cid:
            return None
        data = self._request("GET", f"/api/my/conversations/{quote(cid)}")
        return data if isinstance(data, dict) else None

    def mark_conversation_read(self, conversation_id: str) -> bool:
        """PUT /api/my/conversations/{id} with ``{"read": true}``."""
        cid = str(conversation_id or "").strip()
        if not cid:
            return False
        self._request("PUT", f"/api/my/conversations/{quote(cid)}", json={"read": True})
        return True

    def reply_to_conversation(self, conversation_id: str, body: str) -> dict | None:
        """POST /api/my/conversations/{id}/messages with ``{"body": "..."}``."""
        cid = str(conversation_id or "").strip()
        text = (body or "").strip()
        if not cid or not text:
            raise ValueError("conversation_id and body are required")
        data = self._request(
            "POST",
            f"/api/my/conversations/{quote(cid)}/messages",
            json={"body": text},
        )
        return data if isinstance(data, dict) else None

    def iter_conversations(self, *, unread_only: bool = False, max_pages: int = 50):
        """Yield conversation summary objects across pages."""
        page = 1
        next_url = None
        for _ in range(max_pages):
            if next_url:
                data = self._request("GET", next_url)
            else:
                data = self.list_conversations(unread_only=unread_only, page=page)
            if not isinstance(data, dict):
                break
            conversations = data.get("conversations")
            if not isinstance(conversations, list):
                embedded = data.get("_embedded")
                conversations = (
                    embedded.get("conversations") if isinstance(embedded, dict) else None
                )
            if not isinstance(conversations, list):
                conversations = []
            for conv in conversations:
                if isinstance(conv, dict):
                    yield conv

            links = data.get("_links") if isinstance(data.get("_links"), dict) else {}
            next_link = links.get("next") if isinstance(links, dict) else None
            next_href = None
            if isinstance(next_link, dict):
                next_href = next_link.get("href")
            elif isinstance(next_link, str):
                next_href = next_link

            current = int(data.get("current_page") or page or 1)
            total_pages = int(data.get("total_pages") or current)

            if next_href:
                next_url = next_href
                page = current + 1
                continue

            next_url = None
            if current >= total_pages or not conversations:
                break
            page = current + 1

    def create_product(self, sku, title, price, stock, **kwargs):
        """Create a Reverb listing via POST /api/listings.

        Prefer ``create_listing(payload)`` with a full Reverb body. This thin
        wrapper exists for BaseStoreAdapter compatibility.
        """
        body = {
            "sku": sku,
            "title": title,
            "price": {
                "amount": str(Decimal(str(price)).quantize(Decimal("0.01"))),
                "currency": kwargs.get("currency", "USD"),
            },
            "has_inventory": True,
            "inventory": max(0, int(stock or 0)),
            "make": kwargs.get("make") or "Unknown",
            "model": kwargs.get("model") or title or "Unknown",
            "publish": bool(kwargs.get("publish", True)),
        }
        if kwargs.get("description"):
            body["description"] = kwargs["description"]
        if kwargs.get("condition_uuid"):
            body["condition"] = {"uuid": kwargs["condition_uuid"]}
        if kwargs.get("category_uuid"):
            body["categories"] = [{"uuid": kwargs["category_uuid"]}]
        if kwargs.get("photos"):
            body["photos"] = list(kwargs["photos"])
        return self.create_listing(body)

    def create_listing(self, payload: dict) -> dict:
        """POST /api/listings — create (and optionally publish) a listing."""
        data = self._request("POST", "/api/listings", json=payload or {})
        return data if isinstance(data, dict) else {"raw": data}

    def publish_listing(self, listing_id: str) -> dict | None:
        """PUT /api/listings/{id} with state published if still a draft."""
        lid = str(listing_id or "").strip()
        if not lid:
            raise ValueError("listing_id is required")
        data = self._request(
            "PUT",
            f"/api/listings/{quote(lid)}",
            json={"state": {"slug": "live"}},
        )
        return data if isinstance(data, dict) else None

    def list_listing_conditions(self) -> list:
        """GET /api/listing_conditions — condition UUID catalog."""
        data = self._request("GET", "/api/listing_conditions")
        if isinstance(data, dict):
            conditions = data.get("conditions") or data.get("listing_conditions")
            if isinstance(conditions, list):
                return conditions
            embedded = data.get("_embedded")
            if isinstance(embedded, dict):
                conditions = embedded.get("conditions") or embedded.get("listing_conditions")
                if isinstance(conditions, list):
                    return conditions
        return data if isinstance(data, list) else []

    def list_categories_flat(self) -> list:
        """GET /api/categories/flat — category UUID + full_name catalog."""
        data = self._request("GET", "/api/categories/flat")
        if isinstance(data, dict):
            categories = data.get("categories")
            if isinstance(categories, list):
                return categories
            embedded = data.get("_embedded")
            if isinstance(embedded, dict):
                categories = embedded.get("categories")
                if isinstance(categories, list):
                    return categories
        return data if isinstance(data, list) else []

    def update_product(self, external_id, price=None, stock=None, **kwargs):
        """Update listing price and/or inventory. PUT /api/listings/{id}."""
        body = {}
        if price is not None:
            amt = str(Decimal(str(price)).quantize(Decimal("0.01")))
            body["price"] = {"amount": amt, "currency": kwargs.get("currency", "USD")}
        if stock is not None:
            body["inventory"] = max(0, int(stock))
            body["has_inventory"] = True
        if not body:
            return None
        self._request("PUT", f"/api/listings/{external_id}", json=body)
        return True

    def update_inventory(self, external_id, stock):
        """Update only stock."""
        return self.update_product(external_id, stock=stock)

    def delete_product(self, external_id):
        """Remove listing from Reverb.

        Live listings: PUT /api/my/listings/{id}/state/end with reason not_sold.
        Drafts cannot be "ended" (Reverb returns 422) — DELETE /api/listings/{id}.
        """
        lid = str(external_id or "").strip()
        if not lid:
            raise ValueError("listing id is required")

        state_slug = ""
        try:
            data = self._request("GET", f"/api/listings/{quote(lid)}")
            if isinstance(data, dict):
                state = data.get("state")
                if isinstance(state, dict):
                    state_slug = str(state.get("slug") or state.get("name") or "").lower()
                elif state:
                    state_slug = str(state).lower()
        except ReverbAPIError:
            state_slug = ""

        # Drafts / unpublished → delete resource; live → end listing
        if state_slug in ("draft", "unpublished", "pending"):
            self._request("DELETE", f"/api/listings/{quote(lid)}")
            return True

        try:
            self._request(
                "PUT",
                f"/api/my/listings/{quote(lid)}/state/end",
                json={"reason": "not_sold"},
            )
            return True
        except ReverbAPIError as exc:
            # Already ended, or was actually a draft — try DELETE as fallback
            if exc.status_code in (404, 406, 422):
                try:
                    self._request("DELETE", f"/api/listings/{quote(lid)}")
                    return True
                except ReverbAPIError as del_exc:
                    # If already gone, treat as success for local cleanup
                    if del_exc.status_code == 404:
                        return True
                    raise
            raise
