"""Bunnings / Mirakl order sync, accept, cancel, and shipping helpers."""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from ..models import Environment, MarketplaceOrder, OrderStatus
from ..order_upsert import persist_marketplace_order
from .client import BunningsClient

logger = logging.getLogger("listings.bunnings")

BUNNINGS_CANCEL_REASONS = [
    ("OUT_OF_STOCK", "Out of stock"),
    ("PRICE_ERROR", "Price error"),
    ("CUSTOMER_REQUEST", "Customer request"),
    ("WRONG_ITEM", "Wrong item / dispatch error"),
    ("OTHER", "Other"),
]


def store_environment(store) -> str:
    env = (getattr(store, "bunnings_environment", None) or "production").strip().lower()
    return Environment.PRODUCTION if env == "production" else Environment.STAGING


def _money_to_cents(value) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        value = value.get("amount") or value.get("price") or value.get("value")
    try:
        return int((Decimal(str(value)) * 100).quantize(Decimal("1")))
    except (InvalidOperation, TypeError, ValueError):
        return None


def map_order_status(raw_status) -> str | None:
    if raw_status is None or str(raw_status).strip() == "":
        return None
    text = str(raw_status).strip().upper().replace(" ", "_")
    mapping = {
        "WAITING_ACCEPTANCE": OrderStatus.NEW,
        "WAITING_DEBIT": OrderStatus.PAID,
        "WAITING_DEBIT_PAYMENT": OrderStatus.PAID,
        "SHIPPING": OrderStatus.PAID,
        "SHIPPED": OrderStatus.SENT,
        "TO_COLLECT": OrderStatus.SENT,
        "RECEIVED": OrderStatus.SHIPPING_COMPLETE,
        "CLOSED": OrderStatus.SHIPPING_COMPLETE,
        "REFUSED": OrderStatus.CANCELLED,
        "CANCELED": OrderStatus.CANCELLED,
        "CANCELLED": OrderStatus.CANCELLED,
        "REFUNDED": OrderStatus.REFUNDED,
    }
    return mapping.get(text)


def map_shipping_status(raw_status) -> str:
    text = str(raw_status or "").strip().upper().replace(" ", "_")
    if text in ("SHIPPED", "TO_COLLECT"):
        return "shipped"
    if text in ("RECEIVED", "CLOSED"):
        return "complete"
    if text in ("REFUSED", "CANCELED", "CANCELLED", "REFUNDED"):
        return "cancelled"
    return "pending"


def _extract_orders(result) -> list[dict]:
    if not result or not result.ok:
        return []
    data = result.data
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        if data.get("order_id") or data.get("orderId"):
            return [data]
        for key in ("orders", "data"):
            val = data.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
    return []


def _addr(raw: dict | None) -> dict | None:
    if not isinstance(raw, dict):
        return None
    out = {
        "line1": str(raw.get("street_1") or raw.get("street1") or raw.get("address1") or "").strip(),
        "line2": str(raw.get("street_2") or raw.get("street2") or raw.get("address2") or "").strip(),
        "city": str(raw.get("city") or "").strip(),
        "state": str(raw.get("state") or raw.get("state_iso_code") or "").strip(),
        "postcode": str(raw.get("zip_code") or raw.get("zipcode") or raw.get("zip") or "").strip(),
        "country": str(raw.get("country_iso_code") or raw.get("country") or "AU").strip(),
        "company": str(raw.get("company") or "").strip(),
        "name": " ".join(
            p
            for p in (
                str(raw.get("firstname") or raw.get("first_name") or "").strip(),
                str(raw.get("lastname") or raw.get("last_name") or "").strip(),
            )
            if p
        ).strip(),
        "phone": str(raw.get("phone") or raw.get("phone_secondary") or "").strip(),
    }
    if not any(out.get(k) for k in ("line1", "city", "postcode", "name")):
        return None
    return out


def normalize_customer(raw: dict) -> dict | None:
    customer = raw.get("customer") if isinstance(raw.get("customer"), dict) else {}
    shipping = _addr(customer.get("shipping_address") or customer.get("delivery_address"))
    billing = _addr(customer.get("billing_address"))
    first = str(customer.get("firstname") or customer.get("first_name") or "").strip()
    last = str(customer.get("lastname") or customer.get("last_name") or "").strip()
    if shipping and not first:
        first = str((customer.get("shipping_address") or {}).get("firstname") or "").strip()
        last = str((customer.get("shipping_address") or {}).get("lastname") or "").strip()
    out = {
        "firstName": first,
        "lastName": last,
        "name": " ".join(p for p in (first, last) if p).strip(),
        "email": str(customer.get("email") or customer.get("customer_email") or "").strip(),
        "phone": str(
            customer.get("phone")
            or (shipping or {}).get("phone")
            or ""
        ).strip(),
    }
    if shipping:
        out["shippingAddress"] = shipping
    if billing:
        out["billingAddress"] = billing
    if not any(out.get(k) for k in ("name", "email", "phone", "firstName")) and not shipping:
        return None
    return out


def normalize_line_items(raw: dict) -> list:
    items = raw.get("order_lines") or raw.get("orderLines") or []
    if not isinstance(items, list):
        return []
    out = []
    for li in items:
        if not isinstance(li, dict):
            continue
        sku = str(li.get("offer_sku") or li.get("sku") or li.get("shop_sku") or "").strip()
        qty = li.get("quantity") if li.get("quantity") is not None else li.get("order_line_quantity")
        try:
            qty = int(qty) if qty is not None else 1
        except (TypeError, ValueError):
            qty = 1
        unit = _money_to_cents(li.get("price") if li.get("price") is not None else li.get("offer_price"))
        total = _money_to_cents(li.get("total_price") if li.get("total_price") is not None else li.get("price"))
        oid = li.get("order_line_id") or li.get("id")
        out.append(
            {
                "title": str(li.get("product_title") or li.get("offer_title") or sku).strip(),
                "sku": sku,
                "externalVariantKey": sku,
                "externalProductKey": str(li.get("product_sku") or li.get("product_id") or "").strip(),
                "quantity": qty,
                "priceCents": unit,
                "totalCents": total,
                "lineItemId": str(oid) if oid is not None else "",
                "imageUrl": "",
                "_raw": li,
            }
        )
    return out


def to_ui_raw_shape(raw: dict) -> dict:
    customer = normalize_customer(raw) or {}
    shipping_addr = customer.pop("shippingAddress", None) if "shippingAddress" in customer else None
    line_items = normalize_line_items(raw)
    order_id = raw.get("order_id") or raw.get("orderId")
    total_cents = _money_to_cents(raw.get("total_price") if raw.get("total_price") is not None else raw.get("price"))
    ship_cents = _money_to_cents(raw.get("shipping_price") or raw.get("shipping_cost"))
    status = raw.get("order_state") or raw.get("order_state_code") or raw.get("status")
    return {
        "id": order_id,
        "invoiceNumber": str(order_id or ""),
        "status": status,
        "createdAt": raw.get("created_date") or raw.get("date_created"),
        "updatedAt": raw.get("last_updated_date") or raw.get("date_updated"),
        "totalCents": total_cents,
        "subtotalCents": None,
        "shippingCents": ship_cents,
        "taxCents": None,
        "currency": str(raw.get("currency_iso_code") or raw.get("currency") or "AUD"),
        "customer": {
            "firstName": customer.get("firstName") or "",
            "lastName": customer.get("lastName") or "",
            "name": customer.get("name") or "",
            "email": customer.get("email") or "",
            "phone": customer.get("phone") or "",
            "shippingAddress": shipping_addr,
        },
        "lineItems": line_items,
        "orderSource": "Bunnings",
        "_bunnings": raw,
    }


def upsert_order(user, store, raw: dict) -> MarketplaceOrder | None:
    if not isinstance(raw, dict):
        return None
    order_id = raw.get("order_id") or raw.get("orderId")
    if order_id is None or str(order_id).strip() == "":
        return None
    key = str(order_id).strip()
    environment = store_environment(store)
    status_raw = raw.get("order_state") or raw.get("order_state_code") or raw.get("status")
    ui = to_ui_raw_shape(raw)
    order, created = persist_marketplace_order(
        store=store,
        external_order_key=key,
        environment=environment,
        defaults={
            "user": user,
            "invoice_number": key,
            "total_amount_cents": ui.get("totalCents"),
            "customer_info_json": ui.get("customer"),
            "line_items_json": ui.get("lineItems") or [],
            "raw_response_json": ui,
            "status": map_order_status(status_raw),
            "shipping_status": map_shipping_status(status_raw),
        },
    )
    from ..shopify.orders import push_new_order_to_shopify
    push_new_order_to_shopify(order, store, created=created)
    return order


def _order_from_result(result) -> dict | None:
    if not result or not result.ok:
        return None
    rows = _extract_orders(result)
    if rows:
        return rows[0]
    data = result.data
    if isinstance(data, dict):
        inner = data.get("order")
        if isinstance(inner, dict):
            return inner
        if data.get("order_id") or data.get("orderId"):
            return data
    return None


def _accept_if_needed(client: BunningsClient, raw: dict) -> tuple[dict, bool]:
    state = str(raw.get("order_state") or raw.get("order_state_code") or "").strip().upper()
    if state != "WAITING_ACCEPTANCE":
        return raw, False
    order_id = str(raw.get("order_id") or raw.get("orderId") or "").strip()
    lines = raw.get("order_lines") or []
    payload = []
    for li in lines if isinstance(lines, list) else []:
        if not isinstance(li, dict):
            continue
        lid = li.get("order_line_id") or li.get("id")
        if lid is None:
            continue
        payload.append({"id": str(lid), "accepted": True})
    if not order_id or not payload:
        return raw, False
    result = client.accept_order(order_id, payload)
    if not result.ok:
        logger.warning("Bunnings accept failed order=%s: %s", order_id, result.message)
        return raw, False
    fresh = client.get_order(order_id)
    refreshed = _order_from_result(fresh)
    if refreshed:
        return refreshed, True
    updated = dict(raw)
    updated["order_state"] = "WAITING_DEBIT"
    return updated, True


def fetch(user, store, *, page: int = 1, take: int = 50) -> dict:
    try:
        client = BunningsClient(store)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": str(exc), "fetched": 0}

    from django.utils import timezone

    saved = 0
    accepted = 0
    errors: list[str] = []
    last = getattr(store, "bunnings_last_order_sync_at", None)
    start_update = ""
    if last:
        dt = last
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.utc)
        start_update = dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    open_states = (
        "WAITING_ACCEPTANCE,WAITING_DEBIT,WAITING_DEBIT_PAYMENT,"
        "SHIPPING,TO_COLLECT,SHIPPED,RECEIVED,INCIDENT_OPEN,INCIDENT_CLOSED"
    )
    closed_states = "CLOSED,CANCELED,REFUSED,REFUNDED"

    def _pull(states, since=""):
        nonlocal saved, accepted
        offset = 0
        page_size = 100
        while offset <= 5000:
            result = client.list_orders(
                offset=offset,
                max_results=page_size,
                order_state_codes=states,
                start_update_date=since,
            )
            if not result.ok:
                errors.append(result.message or f"OR11 {states} failed")
                return
            rows = _extract_orders(result)
            if not rows:
                return
            for raw in rows:
                raw, did_accept = _accept_if_needed(client, raw)
                if did_accept:
                    accepted += 1
                if upsert_order(user, store, raw):
                    saved += 1
            if len(rows) < page_size:
                return
            offset += page_size

    _pull("WAITING_ACCEPTANCE", "")
    _pull(open_states, start_update)
    _pull(closed_states, start_update)

    if saved == 0 and errors:
        return {"ok": False, "message": errors[0][:400], "fetched": 0}

    if hasattr(store, "bunnings_last_order_sync_at"):
        store.bunnings_last_order_sync_at = timezone.now()
        try:
            store.save(update_fields=["bunnings_last_order_sync_at", "updated_at"])
        except Exception:  # noqa: BLE001
            logger.warning("Could not persist bunnings_last_order_sync_at for store=%s", store.id)

    msg = f"Retrieved {saved} Bunnings order(s)"
    if accepted:
        msg += f", accepted {accepted}"
    msg += "."
    return {"ok": True, "message": msg, "fetched": saved}


def cancel_reasons() -> dict:
    return {
        "ok": True,
        "marketplace": "bunnings",
        "environment": "production",
        "source": "bunnings_defaults",
        "reasons": [{"value": code, "label": label} for code, label in BUNNINGS_CANCEL_REASONS],
    }


def cancel(order: MarketplaceOrder, *, reason: str = "") -> dict:
    if order.status in (OrderStatus.CANCELLED, OrderStatus.REFUNDED, OrderStatus.SHIPPING_COMPLETE):
        return {
            "ok": True,
            "marketplace_ok": True,
            "message": f"Order is already {order.status.replace('_', ' ')}.",
        }
    store = order.store
    try:
        client = BunningsClient(store)
    except Exception as exc:  # noqa: BLE001
        return {"ok": True, "marketplace_ok": False, "message": str(exc)}

    order_id = (order.external_order_key or order.invoice_number or "").strip()
    raw = order.raw_response_json if isinstance(order.raw_response_json, dict) else {}
    nested = raw.get("_bunnings") if isinstance(raw.get("_bunnings"), dict) else {}
    state = str(nested.get("order_state") or raw.get("status") or "").strip().upper()

    marketplace_ok = False
    message = ""
    if state == "WAITING_ACCEPTANCE":
        lines = []
        for li in nested.get("order_lines") or []:
            if not isinstance(li, dict):
                continue
            lid = li.get("order_line_id") or li.get("id")
            if lid is None:
                continue
            lines.append({"id": str(lid), "accepted": False})
        if lines:
            result = client.accept_order(order_id, lines)
            marketplace_ok = bool(result.ok)
            message = result.message or ""
        else:
            message = "No order lines available to refuse."
    else:
        result = client.cancel_order(
            order_id,
            reason_code=(reason or "OTHER").strip() or "OTHER",
            reason_label=dict(BUNNINGS_CANCEL_REASONS).get((reason or "").strip(), reason or "Other"),
        )
        marketplace_ok = bool(result.ok)
        message = result.message or ""

    order.status = OrderStatus.CANCELLED
    order.raw_response_json = {
        **raw,
        "_local_cancel": {
            "reason": (reason or "").strip() or "OTHER",
            "marketplace_ok": marketplace_ok,
            "marketplace_message": None if marketplace_ok else (message or "Cancel failed"),
        },
    }
    order.save(update_fields=["status", "raw_response_json", "updated_at"])

    if marketplace_ok:
        return {
            "ok": True,
            "marketplace_ok": True,
            "message": "Order cancelled on Bunnings and marked cancelled here.",
        }
    return {
        "ok": True,
        "marketplace_ok": False,
        "message": f"Order marked cancelled here. {message or 'Bunnings cancel failed.'}",
    }


def build_tracking_payload(*, tracking_number: str, carrier: str, tracking_url: str = "",
                           carrier_code: str = "") -> dict:
    payload = {
        "carrier_name": (carrier or "").strip() or "Other",
        "tracking_number": (tracking_number or "").strip(),
    }
    if (tracking_url or "").strip():
        payload["carrier_url"] = tracking_url.strip()
    code = (carrier_code or "").strip() or _carrier_code(carrier)
    if code:
        payload["carrier_code"] = code
    return payload


def flatten_carriers(payload) -> list[dict]:
    items = []
    if isinstance(payload, dict):
        items = payload.get("carriers") or payload.get("data") or []
    elif isinstance(payload, list):
        items = payload
    if not isinstance(items, list):
        return []
    out = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        code = str(raw.get("code") or raw.get("carrier_code") or "").strip()
        label = str(raw.get("label") or raw.get("name") or code).strip()
        if code:
            out.append({"code": code, "name": label})
    return out


def resolve_carrier(store, carrier: str) -> tuple[str, str]:
    """Match UI carrier text to a Bunnings shop carrier code."""
    text = (carrier or "").strip()
    fallback_code = _carrier_code(text)
    try:
        client = BunningsClient(store)
        result = client.list_carriers()
    except Exception:  # noqa: BLE001
        return fallback_code, text
    if not result.ok:
        return fallback_code, text
    needle = text.lower()
    for row in flatten_carriers(result.data):
        if row["code"].lower() == needle or row["name"].lower() == needle:
            return row["code"], row["name"]
    if fallback_code:
        for row in flatten_carriers(result.data):
            if row["code"].lower() == fallback_code.lower():
                return row["code"], row["name"]
    return fallback_code, text


def _carrier_code(carrier: str) -> str:
    text = (carrier or "").strip().lower()
    mapping = {
        "australia post": "AUSPOST",
        "auspost": "AUSPOST",
        "startrack": "STARTRACK",
        "star track": "STARTRACK",
        "tnt": "TNT",
        "dhl": "DHL",
        "fedex": "FEDEX",
        "ups": "UPS",
        "aramex": "ARAMEX",
        "couriers please": "COURIERSPLEASE",
        "sendle": "SENDLE",
    }
    return mapping.get(text, "")
