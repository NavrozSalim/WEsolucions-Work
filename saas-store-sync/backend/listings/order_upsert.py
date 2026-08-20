"""Persist marketplace orders without letting a later fetch reset processed work.

Fetch/upsert used to overwrite ``status``, customer, and line items on every
pull. After a seller submits shipping, marks complete, or cancels, that local
state must stay unless the marketplace reports a real terminal change
(cancelled / refunded) or a forward move (delivered).
"""
from __future__ import annotations

from .models import MarketplaceOrder, OrderStatus

MARKETPLACE_TERMINAL = {OrderStatus.CANCELLED, OrderStatus.REFUNDED}

PROCESSED_STATUSES = {
    OrderStatus.SHIPPING_SUBMITTED,
    OrderStatus.SHIPPING_COMPLETE,
    OrderStatus.CANCELLED,
    OrderStatus.REFUNDED,
}

STATUS_RANK = {
    OrderStatus.NEW: 0,
    OrderStatus.PAID: 1,
    OrderStatus.SENT: 2,
    OrderStatus.SHIPPING_SUBMITTED: 3,
    OrderStatus.SHIPPING_COMPLETE: 4,
}

SHIPPING_RANK = {
    "pending": 0,
    "submitted": 1,
    "shipped": 2,
    "complete": 3,
}

TERMINAL_SHIPPING = {"cancelled"}

_FROZEN_FIELDS = (
    "customer_info_json",
    "line_items_json",
    "total_amount_cents",
    "invoice_number",
    "raw_response_json",
)


def _has_data(value) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, dict, str)) and not value:
        return False
    return True


def resolve_status(existing: str | None, incoming: str | None) -> str:
    """Choose the status to store for an upsert.

    * New row: incoming, or ``new`` when the marketplace value is missing.
    * Missing/unmapped incoming: keep existing.
    * Marketplace cancelled/refunded always wins.
    * Local cancelled/refunded is never reopened by paid/new/sent.
    * Local shipping submitted/complete is not reset; delivered may still
      advance submitted → complete.
    * Open orders never move backwards (paid is not overwritten by new).
    """
    existing = (existing or "").strip() or None
    incoming = (incoming or "").strip() or None

    if existing is None:
        return incoming or OrderStatus.NEW
    if incoming is None:
        return existing
    if incoming in MARKETPLACE_TERMINAL:
        return incoming
    if existing in MARKETPLACE_TERMINAL:
        return existing
    if existing in PROCESSED_STATUSES:
        if (
            existing == OrderStatus.SHIPPING_SUBMITTED
            and incoming == OrderStatus.SHIPPING_COMPLETE
        ):
            return incoming
        return existing

    if STATUS_RANK.get(incoming, 0) >= STATUS_RANK.get(existing, 0):
        return incoming
    return existing


def resolve_shipping_status(existing: str | None, incoming: str | None) -> str:
    existing = (existing or "").strip() or None
    incoming = (incoming or "").strip() or None

    if existing is None:
        return incoming or "pending"
    if incoming is None:
        return existing
    if incoming in TERMINAL_SHIPPING:
        return incoming
    if existing in TERMINAL_SHIPPING:
        return existing
    if SHIPPING_RANK.get(incoming, -1) >= SHIPPING_RANK.get(existing, -1):
        return incoming
    return existing


def merge_customer(stored, incoming) -> dict | None:
    """Keep non-empty stored customer fields; fill gaps from a fresh parse."""
    a = stored if isinstance(stored, dict) else {}
    b = incoming if isinstance(incoming, dict) else {}
    if not a:
        return incoming if isinstance(incoming, dict) else None
    if not b:
        return stored if isinstance(stored, dict) else None
    out = {
        "firstName": a.get("firstName") or b.get("firstName") or "",
        "lastName": a.get("lastName") or b.get("lastName") or "",
        "name": a.get("name") or b.get("name") or "",
        "email": a.get("email") or b.get("email") or "",
        "phone": a.get("phone") or b.get("phone") or "",
    }
    company = a.get("company") or b.get("company")
    if company:
        out["company"] = company
    ship = a.get("shippingAddress") if isinstance(a.get("shippingAddress"), dict) else None
    if not ship or not any(v not in (None, "") for v in ship.values()):
        ship = b.get("shippingAddress") if isinstance(b.get("shippingAddress"), dict) else None
    bill = a.get("billingAddress") if isinstance(a.get("billingAddress"), dict) else None
    if not bill or not any(v not in (None, "") for v in bill.values()):
        bill = b.get("billingAddress") if isinstance(b.get("billingAddress"), dict) else None
    if ship:
        out["shippingAddress"] = ship
    if bill:
        out["billingAddress"] = bill
    raw = a.get("_raw") if a.get("_raw") else b.get("_raw")
    if raw:
        out["_raw"] = raw
    return out


def merge_order_defaults(existing: MarketplaceOrder | None, incoming: dict) -> dict:
    """Build ``update_or_create`` defaults that do not clobber processed orders."""
    incoming = dict(incoming or {})
    if existing is None:
        if not incoming.get("status"):
            incoming["status"] = OrderStatus.NEW
        if not incoming.get("shipping_status"):
            incoming["shipping_status"] = "pending"
        return incoming

    out = dict(incoming)
    out["status"] = resolve_status(existing.status, incoming.get("status"))
    if "shipping_status" in incoming or existing.shipping_status:
        out["shipping_status"] = resolve_shipping_status(
            existing.shipping_status,
            incoming.get("shipping_status"),
        )

    processed = existing.status in PROCESSED_STATUSES
    if processed:
        for field in _FROZEN_FIELDS:
            current = getattr(existing, field, None)
            if _has_data(current):
                out[field] = current
        return out

    if "customer_info_json" in incoming:
        out["customer_info_json"] = merge_customer(
            existing.customer_info_json,
            incoming.get("customer_info_json"),
        )
    incoming_lines = incoming.get("line_items_json")
    if not incoming_lines and existing.line_items_json:
        out["line_items_json"] = existing.line_items_json
    if incoming.get("total_amount_cents") is None and existing.total_amount_cents is not None:
        out["total_amount_cents"] = existing.total_amount_cents
    if not incoming.get("invoice_number") and existing.invoice_number:
        out["invoice_number"] = existing.invoice_number
    incoming_raw = incoming.get("raw_response_json")
    if not incoming_raw and existing.raw_response_json:
        out["raw_response_json"] = existing.raw_response_json
    return out


def persist_marketplace_order(*, store, external_order_key, environment, defaults):
    """Upsert an order, preserving processed local status and payload."""
    key = str(external_order_key or "")
    existing = MarketplaceOrder.objects.filter(
        store=store,
        external_order_key=key,
        environment=environment,
    ).first()
    merged = merge_order_defaults(existing, defaults)
    order, created = MarketplaceOrder.objects.update_or_create(
        store=store,
        external_order_key=key,
        environment=environment,
        defaults=merged,
    )
    return order, created
