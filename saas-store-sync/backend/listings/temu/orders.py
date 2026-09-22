"""Temu order sync, cancel, and shipment helpers for managed stores.

Temu returns buyer address encrypted on ``bg.order.shippinginfo.v2.get``; the
plain address needs ``bg.order.decryptshippinginfo.get``. Both are attempted so
packing slips and Shopify forwarding get a usable address, and a failure there
never blocks the order import.
"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from ..models import Environment, MarketplaceOrder, OrderStatus
from ..order_upsert import persist_marketplace_order
from .client import TemuClient

logger = logging.getLogger("listings.temu")

# Temu pre-ship seller cancellation is the out-of-stock flow.
TEMU_CANCEL_REASONS = [
    ("OUT_OF_STOCK", "Out of stock"),
    ("PRICE_ERROR", "Price error"),
    ("CANNOT_FULFILL", "Cannot fulfill in time"),
    ("OTHER", "Other"),
]

# Default look-back when a store has never synced (seconds).
FIRST_SYNC_WINDOW = 30 * 24 * 60 * 60
MAX_PAGES = 50


def store_environment(store) -> str:
    """Temu has no sandbox router for local sellers — always production."""
    return Environment.PRODUCTION


def _money_to_cents(value) -> int | None:
    """Temu local-seller money fields are already minor units (cents)."""
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        value = (
            value.get("amount")
            if value.get("amount") is not None
            else value.get("value") or value.get("price")
        )
    try:
        return int(Decimal(str(value)).quantize(Decimal("1")))
    except (InvalidOperation, TypeError, ValueError):
        return None


def map_order_status(raw_status) -> str | None:
    if raw_status is None or str(raw_status).strip() == "":
        return None
    text = str(raw_status).strip().upper().replace(" ", "_")
    by_name = {
        "PENDING": OrderStatus.NEW,
        "UNPAID": OrderStatus.NEW,
        "PENDING_PAYMENT": OrderStatus.NEW,
        "UNSHIPPED": OrderStatus.PAID,
        "PENDING_SHIPMENT": OrderStatus.PAID,
        "TO_BE_SHIPPED": OrderStatus.PAID,
        "PARTIALLY_SHIPPED": OrderStatus.PAID,
        "SHIPPED": OrderStatus.SENT,
        "DELIVERED": OrderStatus.SHIPPING_COMPLETE,
        "RECEIVED": OrderStatus.SHIPPING_COMPLETE,
        "COMPLETED": OrderStatus.SHIPPING_COMPLETE,
        "CANCELED": OrderStatus.CANCELLED,
        "CANCELLED": OrderStatus.CANCELLED,
        "REFUNDED": OrderStatus.REFUNDED,
    }
    if text in by_name:
        return by_name[text]
    by_code = {
        "0": OrderStatus.NEW,
        "1": OrderStatus.PAID,
        "2": OrderStatus.PAID,
        "3": OrderStatus.SENT,
        "4": OrderStatus.SHIPPING_COMPLETE,
        "5": OrderStatus.CANCELLED,
    }
    return by_code.get(text)


def map_shipping_status(raw_status) -> str:
    mapped = map_order_status(raw_status)
    if mapped == OrderStatus.SENT:
        return "shipped"
    if mapped == OrderStatus.SHIPPING_COMPLETE:
        return "complete"
    if mapped in (OrderStatus.CANCELLED, OrderStatus.REFUNDED):
        return "cancelled"
    return "pending"


def _order_rows(data) -> list[dict]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("pageItems", "page_items", "orderList", "order_list", "data", "items", "list"):
            val = data.get(key)
            if isinstance(val, list):
                return [row for row in val if isinstance(row, dict)]
        if data.get("parentOrderSn") or data.get("parentOrderMap"):
            return [data]
    return []


def _parent_map(raw: dict) -> dict:
    nested = raw.get("parentOrderMap")
    if isinstance(nested, dict):
        return {**raw, **nested}
    return raw


def parent_order_sn(raw: dict) -> str:
    row = _parent_map(raw if isinstance(raw, dict) else {})
    for key in ("parentOrderSn", "parent_order_sn", "orderSn", "order_sn"):
        val = row.get(key)
        if val not in (None, ""):
            return str(val).strip()
    return ""


def _child_orders(raw: dict) -> list[dict]:
    row = _parent_map(raw)
    for key in ("orderList", "order_list", "childOrderList", "subOrderList", "orderItemList"):
        val = row.get(key)
        if isinstance(val, list):
            return [x for x in val if isinstance(x, dict)]
    return []


def _epoch_to_iso(value):
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return value if value else None
    if seconds <= 0:
        return None
    # Temu sends seconds; tolerate milliseconds.
    if seconds > 10_000_000_000:
        seconds = seconds // 1000
    from datetime import datetime, timezone as dt_timezone

    return datetime.fromtimestamp(seconds, tz=dt_timezone.utc).isoformat()


def normalize_line_items(raw: dict) -> list:
    out = []
    for item in _child_orders(raw):
        sku = str(
            item.get("outSkuSn")
            or item.get("out_sku_sn")
            or item.get("extCode")
            or item.get("skuId")
            or ""
        ).strip()
        try:
            qty = int(item.get("quantity") or item.get("goodsCount") or 1)
        except (TypeError, ValueError):
            qty = 1
        unit = _money_to_cents(
            item.get("originalOrderPrice")
            if item.get("originalOrderPrice") is not None
            else item.get("skuPrice") or item.get("price")
        )
        total = _money_to_cents(
            item.get("orderAmount")
            if item.get("orderAmount") is not None
            else item.get("totalPrice")
        )
        if total is None and unit is not None:
            total = unit * qty
        order_sn = item.get("orderSn") or item.get("order_sn") or item.get("id")
        out.append(
            {
                "title": str(
                    item.get("goodsName") or item.get("productName") or sku
                ).strip(),
                "sku": sku,
                "externalVariantKey": sku,
                "externalProductKey": str(
                    item.get("outGoodsSn") or item.get("goodsId") or ""
                ).strip(),
                "quantity": qty,
                "priceCents": unit,
                "totalCents": total,
                "lineItemId": str(order_sn) if order_sn is not None else "",
                "imageUrl": str(item.get("thumbUrl") or item.get("goodsImageUrl") or "").strip(),
                "_raw": item,
            }
        )
    return out


def _address(raw: dict | None) -> dict | None:
    if not isinstance(raw, dict):
        return None
    out = {
        "line1": str(
            raw.get("addressLine1")
            or raw.get("addressLine")
            or raw.get("detailAddress")
            or raw.get("address")
            or ""
        ).strip(),
        "line2": str(raw.get("addressLine2") or "").strip(),
        "city": str(raw.get("city") or raw.get("cityName") or "").strip(),
        "state": str(raw.get("state") or raw.get("stateName") or raw.get("province") or "").strip(),
        "postcode": str(raw.get("postCode") or raw.get("zipCode") or raw.get("postalCode") or "").strip(),
        "country": str(raw.get("countryCode") or raw.get("country") or "AU").strip() or "AU",
        "name": str(raw.get("receiverName") or raw.get("name") or "").strip(),
        "phone": str(raw.get("receiverPhone") or raw.get("phone") or raw.get("mobile") or "").strip(),
    }
    if not any(out.get(k) for k in ("line1", "city", "postcode", "name")):
        return None
    return out


def _shipping_rows(data) -> list[dict]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("shippingInfoList", "decryptShippingInfoList", "data", "list", "items"):
            val = data.get(key)
            if isinstance(val, list):
                return [row for row in val if isinstance(row, dict)]
        if data.get("receiverName") or data.get("addressLine1"):
            return [data]
    return []


def fetch_address(client: TemuClient, order_sn: str) -> dict | None:
    """Decrypted buyer address, falling back to the encrypted payload shape."""
    for getter in (client.decrypt_shipping_info, client.get_shipping_info):
        try:
            result = getter(order_sn)
        except Exception:  # noqa: BLE001
            logger.exception("Temu shipping info call failed order=%s", order_sn)
            continue
        if not result.ok:
            logger.info("Temu shipping info unavailable order=%s: %s", order_sn, result.message)
            continue
        for row in _shipping_rows(result.data):
            address = _address(row)
            if address:
                return address
    return None


def normalize_customer(raw: dict, address: dict | None = None) -> dict:
    row = _parent_map(raw)
    name = str(
        row.get("receiverName") or row.get("buyerName") or (address or {}).get("name") or ""
    ).strip()
    first, _, last = name.partition(" ")
    out = {
        "firstName": first,
        "lastName": last,
        "name": name,
        "email": str(row.get("buyerEmail") or row.get("email") or "").strip(),
        "phone": str(
            row.get("receiverPhone") or (address or {}).get("phone") or ""
        ).strip(),
    }
    if address:
        out["shippingAddress"] = address
    return out


def to_ui_raw_shape(raw: dict, address: dict | None = None) -> dict:
    row = _parent_map(raw)
    order_sn = parent_order_sn(raw)
    line_items = normalize_line_items(raw)
    total = _money_to_cents(
        row.get("parentOrderPaidAmount")
        if row.get("parentOrderPaidAmount") is not None
        else row.get("orderAmount") or row.get("totalAmount")
    )
    if total is None:
        totals = [i.get("totalCents") for i in line_items if i.get("totalCents") is not None]
        total = sum(totals) if totals else None
    status = (
        row.get("parentOrderStatusStr")
        or row.get("parentOrderStatus")
        or row.get("orderStatus")
        or row.get("status")
    )
    return {
        "id": order_sn,
        "invoiceNumber": order_sn,
        "status": status,
        "createdAt": _epoch_to_iso(row.get("parentOrderTime") or row.get("createdAt")),
        "updatedAt": _epoch_to_iso(row.get("updateTime") or row.get("updatedAt")),
        "totalCents": total,
        "subtotalCents": None,
        "shippingCents": _money_to_cents(row.get("shippingAmount")),
        "taxCents": _money_to_cents(row.get("taxAmount")),
        "currency": str(row.get("currency") or row.get("currencyCode") or "AUD"),
        "customer": normalize_customer(raw, address),
        "lineItems": line_items,
        "orderSource": "Temu",
        "_temu": raw,
    }


def upsert_order(user, store, raw: dict, *, address: dict | None = None) -> MarketplaceOrder | None:
    if not isinstance(raw, dict):
        return None
    order_sn = parent_order_sn(raw)
    if not order_sn:
        return None
    row = _parent_map(raw)
    status_raw = (
        row.get("parentOrderStatusStr")
        or row.get("parentOrderStatus")
        or row.get("orderStatus")
        or row.get("status")
    )
    ui = to_ui_raw_shape(raw, address)
    order, created = persist_marketplace_order(
        store=store,
        external_order_key=order_sn,
        environment=store_environment(store),
        defaults={
            "user": user,
            "invoice_number": order_sn,
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


def fetch(user, store, *, page: int = 1, take: int = 50) -> dict:
    """Page bg.order.list.v2.get from the last sync cutoff and upsert each order."""
    try:
        client = TemuClient(store)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": str(exc), "fetched": 0}

    now = timezone.now()
    last = getattr(store, "temu_last_order_sync_at", None)
    if last:
        create_after = int(last.timestamp()) - 60
    else:
        create_after = int(now.timestamp()) - FIRST_SYNC_WINDOW

    saved = 0
    errors: list[str] = []
    page_no = max(1, int(page or 1))
    page_size = max(1, min(int(take or 50), 100))

    for _ in range(MAX_PAGES):
        result = client.list_orders(
            page=page_no,
            page_size=page_size,
            create_after=create_after,
            create_before=int(now.timestamp()),
        )
        if not result.ok:
            errors.append(result.message or "Temu order list failed.")
            break
        rows = _order_rows(result.data)
        if not rows:
            break
        for raw in rows:
            order_sn = parent_order_sn(raw)
            address = fetch_address(client, order_sn) if order_sn else None
            if upsert_order(user, store, raw, address=address):
                saved += 1
        if len(rows) < page_size:
            break
        page_no += 1

    if saved == 0 and errors:
        return {"ok": False, "message": errors[0][:400], "fetched": 0}

    if hasattr(store, "temu_last_order_sync_at"):
        store.temu_last_order_sync_at = now
        try:
            store.save(update_fields=["temu_last_order_sync_at", "updated_at"])
        except Exception:  # noqa: BLE001
            logger.warning("Could not persist temu_last_order_sync_at for store=%s", store.id)

    message = f"Retrieved {saved} Temu order(s)."
    if errors:
        message = f"{message} {errors[0][:200]}"
    return {"ok": True, "message": message, "fetched": saved}


def cancel_reasons() -> dict:
    return {
        "ok": True,
        "marketplace": "temu",
        "environment": "production",
        "source": "temu_out_of_stock",
        "reasons": [{"value": code, "label": label} for code, label in TEMU_CANCEL_REASONS],
    }


def cancel(order: MarketplaceOrder, *, reason: str = "") -> dict:
    """Seller pre-ship cancellation (out of stock). Local cancel always applies."""
    if order.status in (OrderStatus.CANCELLED, OrderStatus.REFUNDED, OrderStatus.SHIPPING_COMPLETE):
        return {
            "ok": True,
            "marketplace_ok": True,
            "message": f"Order is already {order.status.replace('_', ' ')}.",
        }

    reason_code = (reason or "").strip() or "OUT_OF_STOCK"
    marketplace_ok = False
    message = ""
    try:
        client = TemuClient(order.store)
        order_sn = (order.external_order_key or order.invoice_number or "").strip()
        result = client.cancel_out_of_stock(order_sn, reason=reason_code)
        marketplace_ok = bool(result.ok)
        message = result.message or ""
    except Exception as exc:  # noqa: BLE001
        message = str(exc)

    raw = order.raw_response_json if isinstance(order.raw_response_json, dict) else {}
    order.status = OrderStatus.CANCELLED
    order.raw_response_json = {
        **raw,
        "_local_cancel": {
            "reason": reason_code,
            "marketplace_ok": marketplace_ok,
            "marketplace_message": None if marketplace_ok else (message or "Temu cancel failed"),
        },
    }
    order.save(update_fields=["status", "raw_response_json", "updated_at"])

    if marketplace_ok:
        return {
            "ok": True,
            "marketplace_ok": True,
            "message": "Cancellation requested on Temu and marked cancelled here.",
        }
    return {
        "ok": True,
        "marketplace_ok": False,
        "message": (
            "Order marked cancelled here. "
            + (
                message
                or "Temu only accepts seller cancellation before shipment (out of stock)."
            )
        ),
    }


def flatten_shipping_companies(payload) -> list[dict]:
    rows = []
    if isinstance(payload, dict):
        for key in ("shipCompanyList", "companyList", "data", "list", "items"):
            val = payload.get(key)
            if isinstance(val, list):
                rows = val
                break
    elif isinstance(payload, list):
        rows = payload
    out = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        code = str(
            raw.get("shipId") or raw.get("shipCompanyId") or raw.get("id") or raw.get("code") or ""
        ).strip()
        name = str(
            raw.get("shipCompanyName") or raw.get("name") or raw.get("companyName") or code
        ).strip()
        if code:
            out.append({"code": code, "name": name})
    return out


def resolve_shipping_company(client: TemuClient, carrier: str) -> tuple[str, str]:
    """Match the UI carrier text to a Temu shipping company id."""
    text = (carrier or "").strip()
    if not text:
        return "", ""
    try:
        result = client.list_shipping_companies()
    except Exception:  # noqa: BLE001
        return "", text
    if not result.ok:
        return "", text
    needle = text.lower()
    rows = flatten_shipping_companies(result.data)
    for row in rows:
        if row["name"].lower() == needle or row["code"].lower() == needle:
            return row["code"], row["name"]
    for row in rows:
        if needle in row["name"].lower() or row["name"].lower() in needle:
            return row["code"], row["name"]
    return "", text


def build_shipment_payload(
    order: MarketplaceOrder,
    *,
    tracking_number: str,
    ship_company_id: str = "",
    carrier: str = "",
) -> dict:
    """bg.logistics.shipment.create body for one parent order."""
    order_sn = (order.external_order_key or order.invoice_number or "").strip()
    send_items = []
    for item in order.line_items_json or []:
        if not isinstance(item, dict):
            continue
        child_sn = str(item.get("lineItemId") or "").strip()
        if not child_sn:
            continue
        send_items.append(
            {
                "orderSn": child_sn,
                "goodsQuantity": int(item.get("quantity") or 1),
            }
        )
    package = {
        "trackingNumber": (tracking_number or "").strip(),
        "sendType": 0,
        "packageDetailList": send_items,
    }
    if (ship_company_id or "").strip():
        package["shipCompanyId"] = ship_company_id.strip()
    elif (carrier or "").strip():
        package["shipCompanyName"] = carrier.strip()
    return {
        "parentOrderSn": order_sn,
        "sendRequestList": [package],
    }
