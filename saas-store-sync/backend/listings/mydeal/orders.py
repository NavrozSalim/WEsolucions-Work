"""MyDeal / WMP order sync, cancel, and UI normalization helpers."""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from ..errors import MarketplaceError
from ..models import Environment, MarketplaceOrder, OrderStatus
from .client import MyDealClient

logger = logging.getLogger("listings.mydeal")

# Doc §0.12.7 RefundReason — also used as cancel reasons for unshipped orders.
MYDEAL_CANCEL_REASONS = [
    ("OUT_OF_STOCK", "Out of stock"),
    ("CANCELLED_CHANGE_OF_MIND", "Change of mind"),
    ("PRICE_ERROR", "Price error"),
    ("DISPATCH_ERROR", "Incorrect product / dispatch error"),
    ("OVERSEAS_ADDRESS", "Outside delivery area"),
    ("FAULTY", "Faulty item"),
    ("NOT_AS_DESCRIBED", "Not as described"),
    ("DAMAGED_ON_ARRIVAL", "Damaged on arrival"),
    ("LOST_IN_POST", "Lost in post"),
    ("MISSING_PARTS", "Missing parts"),
    ("RETURN_TO_SENDER", "Return to sender"),
    ("DELIVERY_ADDRESS_NOT_CONFIRMED", "Delivery address not confirmed"),
    ("FREIGHT_DISCOUNT", "Freight discount"),
    ("COMPENSATION", "Compensation"),
]

_REASON_CODES = {code for code, _ in MYDEAL_CANCEL_REASONS}
_REASON_BY_LABEL = {label.lower(): code for code, label in MYDEAL_CANCEL_REASONS}


def store_environment(store) -> str:
    env = (getattr(store, "mydeal_environment", None) or "sandbox").strip().lower()
    return Environment.PRODUCTION if env == "production" else Environment.STAGING


def _money_to_cents(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int((Decimal(str(value)) * 100).quantize(Decimal("1")))
    except (InvalidOperation, TypeError, ValueError):
        return None


def map_order_status(raw_status) -> str:
    text = str(raw_status or "").strip().lower().replace(" ", "").replace("_", "")
    mapping = {
        "readytofulfill": OrderStatus.PAID,
        "selleracknowledged": OrderStatus.PAID,
        "shipped": OrderStatus.SENT,
        "refunded": OrderStatus.REFUNDED,
        "all": OrderStatus.NEW,
    }
    return mapping.get(text, OrderStatus.NEW)


def map_shipping_status(raw_status) -> str:
    text = str(raw_status or "").strip().lower().replace(" ", "").replace("_", "")
    if text == "shipped":
        return "shipped"
    if text == "refunded":
        return "cancelled"
    if text in ("readytofulfill", "selleracknowledged"):
        return "pending"
    return "pending"


def _extract_orders(result) -> list[dict]:
    if not result or not result.ok:
        return []
    data = result.data
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        # Single order sometimes returned as Data: {Order…} or Data: [Order]
        if "OrderId" in data or "orderId" in data:
            return [data]
        for key in ("Data", "data", "Orders", "orders"):
            val = data.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
            if isinstance(val, dict) and ("OrderId" in val or "orderId" in val):
                return [val]
    return []


def _addr(raw: dict | None) -> dict | None:
    if not isinstance(raw, dict):
        return None
    out = {
        "line1": str(raw.get("Address1") or raw.get("address1") or "").strip(),
        "line2": str(raw.get("Address2") or raw.get("address2") or "").strip(),
        "city": str(raw.get("Suburb") or raw.get("suburb") or raw.get("City") or "").strip(),
        "state": str(raw.get("State") or raw.get("state") or "").strip(),
        "postcode": str(raw.get("PostalCode") or raw.get("postalCode") or "").strip(),
        "country": str(raw.get("CountryCode") or raw.get("countryCode") or "AU").strip(),
        "company": str(raw.get("CompanyName") or raw.get("companyName") or "").strip(),
        "name": " ".join(
            p
            for p in (
                str(raw.get("FirstName") or raw.get("firstName") or "").strip(),
                str(raw.get("LastName") or raw.get("lastName") or "").strip(),
            )
            if p
        ).strip(),
        "phone": str(raw.get("Phone") or raw.get("phone") or "").strip(),
    }
    if not any(out.get(k) for k in ("line1", "city", "postcode", "name")):
        return None
    return out


def normalize_customer(raw: dict) -> dict | None:
    shipping = _addr(raw.get("ShippingAddress") or raw.get("shippingAddress"))
    first = ""
    last = ""
    phone = ""
    if isinstance(raw.get("ShippingAddress"), dict):
        sa = raw["ShippingAddress"]
        first = str(sa.get("FirstName") or "").strip()
        last = str(sa.get("LastName") or "").strip()
        phone = str(sa.get("Phone") or "").strip()
    name = " ".join(p for p in (first, last) if p).strip()
    out = {
        "firstName": first,
        "lastName": last,
        "name": name,
        "email": str(raw.get("CustomerEmail") or raw.get("customerEmail") or "").strip(),
        "phone": phone,
    }
    if shipping:
        out["shippingAddress"] = shipping
    if not any(out.get(k) for k in ("name", "email", "phone", "firstName")) and not shipping:
        return None
    return out


def normalize_line_items(raw: dict) -> list:
    items = raw.get("LineItems") or raw.get("lineItems") or []
    if not isinstance(items, list):
        return []
    out = []
    for li in items:
        if not isinstance(li, dict):
            continue
        sku = str(li.get("SKU") or li.get("sku") or "").strip()
        qty = li.get("Quantity") if li.get("Quantity") is not None else li.get("quantity")
        try:
            qty = int(qty) if qty is not None else 1
        except (TypeError, ValueError):
            qty = 1
        unit = _money_to_cents(li.get("UnitPrice") if li.get("UnitPrice") is not None else li.get("unitPrice"))
        total = _money_to_cents(li.get("TotalPrice") if li.get("TotalPrice") is not None else li.get("totalPrice"))
        oid = li.get("OrderItemId") if li.get("OrderItemId") is not None else li.get("orderItemId")
        out.append(
            {
                "title": str(li.get("ProductTitle") or li.get("productTitle") or sku).strip(),
                "sku": sku,
                "externalVariantKey": sku,
                "externalProductKey": str(li.get("ProductId") or li.get("productId") or "").strip(),
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
    """Lasoo-compatible envelope so Orders UI can render MyDeal orders."""
    customer = normalize_customer(raw) or {}
    shipping_addr = customer.pop("shippingAddress", None) if "shippingAddress" in customer else None
    line_items = normalize_line_items(raw)
    order_id = raw.get("OrderId") if raw.get("OrderId") is not None else raw.get("orderId")
    total_cents = _money_to_cents(raw.get("TotalPrice") if raw.get("TotalPrice") is not None else raw.get("totalPrice"))
    sub_cents = _money_to_cents(raw.get("SubTotalPrice") if raw.get("SubTotalPrice") is not None else raw.get("subTotalPrice"))
    ship_cents = _money_to_cents(
        raw.get("TotalShippingPrice") if raw.get("TotalShippingPrice") is not None else raw.get("totalShippingPrice")
    )
    status = raw.get("OrderStatus") or raw.get("orderStatus")
    return {
        "id": order_id,
        "invoiceNumber": str(order_id or ""),
        "status": status,
        "createdAt": raw.get("PurchaseDate") or raw.get("purchaseDate"),
        "updatedAt": raw.get("PurchaseDate") or raw.get("purchaseDate"),
        "totalCents": total_cents,
        "subtotalCents": sub_cents,
        "shippingCents": ship_cents,
        "taxCents": None,
        "currency": str(raw.get("Currency") or raw.get("currency") or "AUD"),
        "customer": {
            "firstName": customer.get("firstName") or "",
            "lastName": customer.get("lastName") or "",
            "name": customer.get("name") or "",
            "email": customer.get("email") or "",
            "phone": customer.get("phone") or "",
            "shippingAddress": shipping_addr,
        },
        "lineItems": line_items,
        "orderSource": raw.get("OrderSource") or raw.get("orderSource") or "",
        "_mydeal": raw,
    }


def upsert_order(user, store, raw: dict) -> MarketplaceOrder | None:
    if not isinstance(raw, dict):
        return None
    order_id = raw.get("OrderId") if raw.get("OrderId") is not None else raw.get("orderId")
    if order_id is None or str(order_id).strip() == "":
        return None
    key = str(order_id).strip()
    environment = store_environment(store)
    status_raw = raw.get("OrderStatus") or raw.get("orderStatus")
    ui = to_ui_raw_shape(raw)
    customer = ui.get("customer")
    lines = ui.get("lineItems") or []
    total_cents = ui.get("totalCents")

    order, _created = MarketplaceOrder.objects.update_or_create(
        store=store,
        external_order_key=key,
        environment=environment,
        defaults={
            "user": user,
            "invoice_number": key,
            "total_amount_cents": total_cents,
            "customer_info_json": customer,
            "line_items_json": lines,
            "raw_response_json": ui,
            "status": map_order_status(status_raw),
            "shipping_status": map_shipping_status(status_raw),
        },
    )
    return order


def fetch(user, store, *, page: int = 1, take: int = 100) -> dict:
    """Pull unfulfilled (ack) + status pages from MyDeal and upsert locally."""
    method = (getattr(store, "mydeal_setup_method", None) or "upload").strip().lower()
    if method != "api":
        raise MarketplaceError(
            "MyDeal order sync requires API connection mode (not upload templates)."
        )
    try:
        client = MyDealClient(store)
    except MarketplaceError as exc:
        return {"ok": False, "message": str(exc), "fetched": 0}

    saved = 0
    acknowledged = 0
    errors: list[str] = []

    # 1) Unfulfilled → upsert + acknowledge so they leave the unfulfilled queue.
    uf = client.list_unfulfilled(limit=min(250, max(1, int(take or 100))))
    if not uf.ok:
        # Some sandboxes may not expose /orders/unfulfilled — fall through to /orders.
        logger.warning("MyDeal unfulfilled list failed: %s", uf.message)
        errors.append(uf.message or "Unfulfilled fetch failed.")
    else:
        for raw in _extract_orders(uf):
            order = upsert_order(user, store, raw)
            if not order:
                continue
            saved += 1
            oid = order.external_order_key
            ack = client.acknowledge_order(oid)
            if ack.ok:
                acknowledged += 1
                # Refresh local status after ack
                raw["OrderStatus"] = "SellerAcknowledged"
                upsert_order(user, store, raw)
            else:
                logger.warning("MyDeal acknowledge failed order=%s: %s", oid, ack.message)

    # 2) Broader refresh by status (ReadytoFulfill may still appear if ack failed).
    for status in ("ReadytoFulfill", "SellerAcknowledged", "Shipped", "Refunded"):
        result = client.list_orders(
            order_status=status,
            page=max(1, int(page or 1)),
            limit=min(250, max(1, int(take or 100))),
        )
        if not result.ok:
            errors.append(f"{status}: {result.message or 'failed'}")
            continue
        for raw in _extract_orders(result):
            if upsert_order(user, store, raw):
                saved += 1

    if saved == 0 and errors and not uf.ok:
        return {
            "ok": False,
            "message": errors[0][:400],
            "fetched": 0,
        }

    msg = f"Retrieved {saved} MyDeal order(s)"
    if acknowledged:
        msg += f", acknowledged {acknowledged}"
    msg += "."
    return {"ok": True, "message": msg, "fetched": saved}


def cancel_reasons() -> dict:
    return {
        "ok": True,
        "marketplace": "mydeal",
        "environment": "sandbox",
        "source": "mydeal_api_docs",
        "reasons": [{"value": code, "label": label} for code, label in MYDEAL_CANCEL_REASONS],
    }


def normalize_cancel_reason(reason: str) -> str:
    text = (reason or "").strip()
    if not text:
        return "OUT_OF_STOCK"
    if text in _REASON_CODES:
        return text
    key = text.lower().replace(" ", "_").replace("-", "_")
    upper = text.upper().replace(" ", "_").replace("-", "_")
    if upper in _REASON_CODES:
        return upper
    if text.lower() in _REASON_BY_LABEL:
        return _REASON_BY_LABEL[text.lower()]
    if key in {c.lower() for c in _REASON_CODES}:
        for c in _REASON_CODES:
            if c.lower() == key:
                return c
    return "OUT_OF_STOCK"


def cancel(order: MarketplaceOrder, *, reason: str = "") -> dict:
    """Cancel unshipped MyDeal order (full cancel → internal refund queue)."""
    if order.status in (OrderStatus.CANCELLED, OrderStatus.REFUNDED, OrderStatus.SHIPPING_COMPLETE):
        return {
            "ok": True,
            "marketplace_ok": True,
            "message": f"Order is already {order.status.replace('_', ' ')}.",
        }

    reason_code = normalize_cancel_reason(reason)
    store = order.store
    try:
        client = MyDealClient(store)
    except MarketplaceError as exc:
        return {"ok": True, "marketplace_ok": False, "message": str(exc)}

    items = []
    for li in order.line_items_json if isinstance(order.line_items_json, list) else []:
        if not isinstance(li, dict):
            continue
        lid = li.get("lineItemId") or (li.get("_raw") or {}).get("OrderItemId")
        sku = (li.get("sku") or li.get("externalVariantKey") or "").strip()
        if lid is None or not sku:
            continue
        try:
            item_id = int(lid)
        except (TypeError, ValueError):
            item_id = lid
        items.append({"Id": item_id, "SKU": sku, "Reason": reason_code})

    if not items:
        return {
            "ok": True,
            "marketplace_ok": False,
            "message": "No line items available to cancel on MyDeal.",
        }

    try:
        order_id = int(order.external_order_key)
    except (TypeError, ValueError):
        order_id = order.external_order_key

    body = {"OrderId": order_id, "Items": items}
    result = client.cancel_order(order_id, body)
    marketplace_ok = bool(result.ok)

    order.status = OrderStatus.CANCELLED
    raw = order.raw_response_json if isinstance(order.raw_response_json, dict) else {}
    order.raw_response_json = {
        **raw,
        "_local_cancel": {
            "reason": reason_code,
            "marketplace_ok": marketplace_ok,
            "marketplace_message": None if marketplace_ok else (result.message or "Cancel failed"),
        },
    }
    order.save(update_fields=["status", "raw_response_json", "updated_at"])

    if marketplace_ok:
        return {
            "ok": True,
            "marketplace_ok": True,
            "message": "Order cancelled on MyDeal and marked cancelled here.",
        }
    return {
        "ok": True,
        "marketplace_ok": False,
        "message": f"Order marked cancelled here. {result.message or 'MyDeal cancel failed.'}",
    }


def build_fulfillment_payload(
    order: MarketplaceOrder,
    *,
    tracking_number: str,
    carrier: str,
    shipped_date: str = "",
) -> dict:
    """Build a single OrderFulfillment item for POST /orders/fulfill."""
    try:
        order_id = int(order.external_order_key)
    except (TypeError, ValueError):
        order_id = order.external_order_key

    fulfillment_items = []
    for li in order.line_items_json if isinstance(order.line_items_json, list) else []:
        if not isinstance(li, dict):
            continue
        lid = li.get("lineItemId") or (li.get("_raw") or {}).get("OrderItemId")
        sku = (li.get("sku") or li.get("externalVariantKey") or "").strip()
        if lid is None or not sku:
            continue
        try:
            item_id = int(lid)
        except (TypeError, ValueError):
            item_id = lid
        item = {
            "OrderItemId": item_id,
            "SKU": sku,
            "DispatchCarrier": (carrier or "").strip() or None,
            "TrackingCode": (tracking_number or "").strip() or None,
        }
        if (shipped_date or "").strip():
            item["DispatchedDate"] = shipped_date.strip()
        # Drop None optional fields
        fulfillment_items.append({k: v for k, v in item.items() if v is not None})

    return {"OrderId": order_id, "FulfillmentItems": fulfillment_items}
