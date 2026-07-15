"""Reverb order normalization + sync helpers for managed stores.

Maps Reverb selling-order payloads into the same MarketplaceOrder shape the
Orders UI already expects (customer_info_json, line_items_json, etc.).
Always talks to production api.reverb.com — there is no Reverb staging API.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

from store_adapters import get_adapter
from store_adapters.reverb_adapter import ReverbAPIError

from ..errors import MarketplaceError
from ..models import Environment, MarketplaceOrder, OrderStatus

logger = logging.getLogger("listings.reverb")

# Overlap so boundary updates are not missed (Reverb Order Retrieval guide).
SYNC_OVERLAP = timedelta(minutes=5)
# First sync looks back this far when last_order_sync_at is unset.
INITIAL_LOOKBACK = timedelta(days=90)


def read_money(value) -> dict:
    """Extract amount_cents + currency from a Reverb money object."""
    if not isinstance(value, dict):
        return {"amount_cents": None, "currency": None, "amount": None}
    cents = value.get("amount_cents")
    try:
        cents = int(cents) if cents is not None else None
    except (TypeError, ValueError):
        cents = None
    currency = value.get("currency")
    if not isinstance(currency, str) or not currency.strip():
        currency = None
    amount = value.get("amount")
    return {
        "amount_cents": cents,
        "currency": currency,
        "amount": str(amount) if amount is not None else None,
    }


def map_reverb_status(raw_status) -> str:
    """Map Reverb order status → local OrderStatus."""
    if not raw_status:
        return OrderStatus.NEW
    text = str(raw_status).strip().lower().replace(" ", "_").replace("-", "_")
    mapping = {
        "unpaid": OrderStatus.NEW,
        "payment_pending": OrderStatus.NEW,
        "pending_review": OrderStatus.NEW,
        "blocked": OrderStatus.NEW,
        "paid": OrderStatus.PAID,
        "shipped": OrderStatus.SENT,
        "picked_up": OrderStatus.SENT,
        "received": OrderStatus.SHIPPING_COMPLETE,
        "refunded": OrderStatus.REFUNDED,
        "cancelled": OrderStatus.CANCELLED,
        "canceled": OrderStatus.CANCELLED,
    }
    return mapping.get(text, OrderStatus.NEW)


def map_shipping_status(raw_status) -> str:
    text = str(raw_status or "").strip().lower().replace(" ", "_").replace("-", "_")
    if text in ("shipped", "picked_up"):
        return "shipped"
    if text in ("received",):
        return "complete"
    if text in ("refunded", "cancelled", "canceled", "blocked"):
        return "cancelled"
    if text == "paid":
        return "pending"
    return "pending"


def _shipping_address(raw: dict) -> dict | None:
    addr = raw.get("shipping_address")
    if not isinstance(addr, dict):
        return None
    out = {
        "line1": str(addr.get("street_address") or "").strip(),
        "line2": str(addr.get("extended_address") or "").strip(),
        "city": str(addr.get("locality") or "").strip(),
        "state": str(addr.get("region") or "").strip(),
        "postcode": str(addr.get("postal_code") or "").strip(),
        "country": str(addr.get("country_code") or "").strip(),
        "company": "",
        "name": str(addr.get("name") or "").strip(),
        "phone": str(
            addr.get("phone") or addr.get("unformatted_phone") or ""
        ).strip(),
    }
    if not any(out.get(k) for k in ("line1", "city", "postcode", "country", "name")):
        return None
    return out


def normalize_customer(raw: dict) -> dict | None:
    first = str(raw.get("buyer_first_name") or "").strip()
    last = str(raw.get("buyer_last_name") or "").strip()
    name = str(raw.get("buyer_name") or "").strip() or (
        " ".join(p for p in (first, last) if p).strip()
    )
    shipping = _shipping_address(raw)
    phone = ""
    if shipping:
        phone = shipping.get("phone") or ""
    out = {
        "firstName": first,
        "lastName": last,
        "name": name,
        "email": "",
        "phone": phone,
        "buyerId": str(raw.get("buyer_id")) if raw.get("buyer_id") is not None else "",
    }
    if shipping:
        out["shippingAddress"] = shipping
    if not any(out.get(k) for k in ("name", "firstName", "lastName", "phone")) and not shipping:
        return None
    return out


def normalize_line_items(raw: dict) -> list:
    """Reverb orders are typically one listing per order — build a single line item."""
    product = read_money(raw.get("amount_product_subtotal") or raw.get("amount_product"))
    qty = raw.get("quantity")
    try:
        qty = int(qty) if qty is not None else 1
    except (TypeError, ValueError):
        qty = 1
    sku = str(raw.get("sku") or "").strip()
    product_id = raw.get("product_id")
    title = str(raw.get("title") or "").strip()
    image_url = None
    links = raw.get("_links") if isinstance(raw.get("_links"), dict) else {}
    photo = links.get("photo")
    if isinstance(photo, dict):
        image_url = photo.get("href")
    photos = raw.get("photos")
    if not image_url and isinstance(photos, list) and photos:
        first = photos[0]
        if isinstance(first, dict):
            plinks = first.get("_links") if isinstance(first.get("_links"), dict) else {}
            for key in ("thumbnail", "small_crop", "large_crop", "full"):
                node = plinks.get(key)
                if isinstance(node, dict) and node.get("href"):
                    image_url = node["href"]
                    break
    return [{
        "title": title,
        "sku": sku,
        "externalVariantKey": sku,
        "externalProductKey": str(product_id) if product_id is not None else "",
        "quantity": qty,
        "priceCents": product.get("amount_cents"),
        "lineItemId": str(product_id) if product_id is not None else "",
        "imageUrl": image_url or "",
        "_raw": {
            "order_number": raw.get("order_number"),
            "product_id": product_id,
            "sku": sku,
        },
    }]


def normalize_totals(raw: dict) -> dict:
    total = read_money(raw.get("total"))
    product = read_money(raw.get("amount_product_subtotal") or raw.get("amount_product"))
    shipping = read_money(raw.get("shipping"))
    tax = read_money(raw.get("amount_tax"))
    return {
        "totalCents": total.get("amount_cents"),
        "subtotalCents": product.get("amount_cents"),
        "shippingCents": shipping.get("amount_cents"),
        "taxCents": tax.get("amount_cents"),
        "discountCents": None,
        "currency": total.get("currency") or product.get("currency") or "USD",
    }


def to_ui_raw_shape(raw: dict) -> dict:
    """
    Build a Lasoo-compatible raw envelope so build_order_details() can render
    Reverb orders without UI changes. Original Reverb payload stays under
    ``_reverb``.
    """
    customer = normalize_customer(raw) or {}
    shipping_addr = customer.pop("shippingAddress", None) if "shippingAddress" in customer else None
    if shipping_addr is None:
        shipping_addr = _shipping_address(raw)
    totals = normalize_totals(raw)
    line_items = normalize_line_items(raw)
    status = raw.get("status")
    return {
        "id": raw.get("order_number"),
        "invoiceNumber": str(raw.get("order_number") or ""),
        "status": status,
        "createdAt": raw.get("created_at"),
        "updatedAt": raw.get("updated_at"),
        "paidAt": raw.get("paid_at"),
        "totalCents": totals.get("totalCents"),
        "subtotalCents": totals.get("subtotalCents"),
        "shippingCents": totals.get("shippingCents"),
        "taxCents": totals.get("taxCents"),
        "currency": totals.get("currency"),
        "customer": {
            "firstName": customer.get("firstName") or "",
            "lastName": customer.get("lastName") or "",
            "name": customer.get("name") or "",
            "email": customer.get("email") or "",
            "phone": customer.get("phone") or "",
            "shippingAddress": shipping_addr,
        },
        "shippingAddress": shipping_addr,
        "lineItems": line_items,
        "shipping": {
            "status": map_shipping_status(status),
            "method": raw.get("shipping_method"),
            "carrier": raw.get("shipping_provider"),
            "trackingNumber": raw.get("shipping_code"),
            "shippedAt": raw.get("shipped_at"),
            "localPickup": bool(raw.get("local_pickup")),
        },
        "shopName": raw.get("shop_name"),
        "orderType": raw.get("order_type"),
        "orderBundleId": raw.get("order_bundle_id"),
        "_links": raw.get("_links"),
        "_reverb": raw,
    }


def upsert_order(user, store, raw: dict) -> MarketplaceOrder | None:
    """Upsert one Reverb selling order into MarketplaceOrder."""
    order_number = raw.get("order_number")
    if order_number is None or str(order_number).strip() == "":
        logger.warning("Skipping Reverb order without order_number store=%s", store.id)
        return None
    order_key = str(order_number).strip()
    ui_raw = to_ui_raw_shape(raw)
    customer = normalize_customer(raw)
    line_items = normalize_line_items(raw)
    totals = normalize_totals(raw)
    status = map_reverb_status(raw.get("status"))
    shipping_status = map_shipping_status(raw.get("status"))

    order, _ = MarketplaceOrder.objects.update_or_create(
        store=store,
        external_order_key=order_key,
        environment=Environment.PRODUCTION,
        defaults={
            "user": user,
            "invoice_number": order_key,
            "customer_info_json": customer,
            "line_items_json": line_items,
            "total_amount_cents": totals.get("totalCents"),
            "status": status,
            "shipping_status": shipping_status,
            "raw_response_json": ui_raw,
        },
    )
    return order


def fetch(user, store) -> dict:
    """
    Pull Reverb selling orders (incremental by updated_at) and upsert locally.

    Advances ``store.reverb_last_order_sync_at`` only after a full successful pull.
    """
    if not (getattr(store, "api_token", None) or "").strip():
        raise MarketplaceError(
            "No Reverb API token configured for this store. Add it in store settings."
        )

    adapter = get_adapter(store)
    if not hasattr(adapter, "iter_orders_selling_all"):
        raise MarketplaceError("Store adapter is not Reverb — cannot fetch Reverb orders.")

    end = timezone.now()
    previous = getattr(store, "reverb_last_order_sync_at", None)
    if previous:
        start = previous - SYNC_OVERLAP
    else:
        start = end - INITIAL_LOOKBACK

    start_iso = start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_iso = end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    saved = 0
    try:
        for raw in adapter.iter_orders_selling_all(
            updated_start_date=start_iso,
            updated_end_date=end_iso,
        ):
            if upsert_order(user, store, raw):
                saved += 1
    except ReverbAPIError as exc:
        status_code = getattr(exc, "status_code", None)
        logger.error(
            "Reverb order sync failed store=%s status=%s err=%s",
            store.id, status_code, exc,
        )
        if status_code == 401:
            return {
                "ok": False,
                "message": (
                    "Reverb rejected the API token (401). "
                    "Reconnect the store with a token that has the read_orders scope."
                ),
                "fetched": 0,
            }
        if status_code == 429:
            return {
                "ok": False,
                "message": "Reverb rate-limited this request. Try again in a minute.",
                "fetched": 0,
            }
        return {
            "ok": False,
            "message": str(exc)[:400] or "Reverb order sync failed.",
            "fetched": 0,
        }

    store.reverb_last_order_sync_at = end
    store.save(update_fields=["reverb_last_order_sync_at", "updated_at"])

    return {
        "ok": True,
        "message": f"Retrieved {saved} order(s) from Reverb.",
        "fetched": saved,
    }
