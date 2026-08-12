"""Etsy receipt/order sync + normalize for managed stores."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from store_adapters import get_adapter
from store_adapters.etsy_adapter import EtsyAPIError

from ..errors import MarketplaceError
from ..models import Environment, MarketplaceOrder, OrderStatus

logger = logging.getLogger("listings.etsy")

INITIAL_LOOKBACK = timedelta(days=90)
SYNC_OVERLAP = timedelta(minutes=5)


def map_order_status(raw: dict) -> str:
    if not isinstance(raw, dict):
        return OrderStatus.NEW
    if raw.get("is_canceled") or raw.get("was_canceled"):
        return OrderStatus.CANCELLED
    if raw.get("is_refunded") or (raw.get("grandtotal") and str(raw.get("status") or "").lower() == "refunded"):
        return OrderStatus.REFUNDED
    shipments = raw.get("shipments") or []
    if isinstance(shipments, list) and shipments:
        return OrderStatus.SENT
    if raw.get("was_shipped"):
        return OrderStatus.SENT
    if raw.get("was_paid"):
        return OrderStatus.PAID
    return OrderStatus.NEW


def map_shipping_status(raw: dict) -> str:
    if not isinstance(raw, dict):
        return "pending"
    if raw.get("is_canceled") or raw.get("was_canceled"):
        return "cancelled"
    if raw.get("was_shipped") or (isinstance(raw.get("shipments"), list) and raw.get("shipments")):
        return "shipped"
    if raw.get("was_paid"):
        return "pending"
    return "pending"


def _money_to_cents(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, dict):
        amount = value.get("amount")
        divisor = value.get("divisor") or 100
        if amount is None:
            return None
        try:
            return int(Decimal(str(amount)) * 100 / Decimal(str(divisor)))
        except (InvalidOperation, TypeError, ValueError):
            return None
    try:
        return int((Decimal(str(value)) * 100).quantize(Decimal("1")))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _addr(raw: dict | None) -> dict | None:
    if not isinstance(raw, dict):
        return None
    out = {
        "line1": str(raw.get("first_line") or raw.get("address_1") or "").strip(),
        "line2": str(raw.get("second_line") or raw.get("address_2") or "").strip(),
        "city": str(raw.get("city") or "").strip(),
        "state": str(raw.get("state") or "").strip(),
        "postcode": str(raw.get("zip") or raw.get("zip_code") or "").strip(),
        "country": str(raw.get("country_iso") or raw.get("country_code") or "").strip(),
        "company": "",
        "name": str(raw.get("name") or "").strip(),
        "phone": "",
    }
    if not any(out.get(k) for k in ("line1", "city", "postcode", "name", "country")):
        return None
    return out


def normalize_customer(raw: dict) -> dict | None:
    shipping = _addr(raw.get("formatted_address") if isinstance(raw.get("formatted_address"), dict) else None)
    if shipping is None:
        shipping = {
            "line1": str(raw.get("first_line") or "").strip(),
            "line2": str(raw.get("second_line") or "").strip(),
            "city": str(raw.get("city") or "").strip(),
            "state": str(raw.get("state") or "").strip(),
            "postcode": str(raw.get("zip") or "").strip(),
            "country": str(raw.get("country_iso") or "").strip(),
            "company": "",
            "name": str(raw.get("name") or "").strip(),
            "phone": "",
        }
        if not any(shipping.get(k) for k in ("line1", "city", "postcode", "name")):
            shipping = None
    name = str(raw.get("name") or "").strip()
    out = {
        "firstName": name.split(" ", 1)[0] if name else "",
        "lastName": name.split(" ", 1)[1] if name and " " in name else "",
        "name": name,
        "email": str(raw.get("buyer_email") or "").strip(),
        "phone": "",
    }
    if shipping:
        out["shippingAddress"] = shipping
    if not any(out.get(k) for k in ("name", "email", "firstName")) and not shipping:
        return None
    return out


def normalize_line_items(raw: dict) -> list:
    txs = raw.get("transactions") or []
    if not isinstance(txs, list):
        return []
    out = []
    for tx in txs:
        if not isinstance(tx, dict):
            continue
        sku = str(tx.get("sku") or tx.get("product_id") or "").strip()
        title = str(tx.get("title") or tx.get("description") or sku).strip()
        qty = tx.get("quantity") or 1
        try:
            qty = int(qty)
        except (TypeError, ValueError):
            qty = 1
        price_cents = _money_to_cents(tx.get("price"))
        out.append({
            "sku": sku,
            "title": title,
            "quantity": qty,
            "price_cents": price_cents,
            "external_line_id": str(tx.get("transaction_id") or "").strip(),
        })
    return out


def upsert_order(user, store, raw: dict) -> MarketplaceOrder | None:
    if not isinstance(raw, dict):
        return None
    receipt_id = raw.get("receipt_id") or raw.get("receiptId")
    if receipt_id is None:
        return None
    external_key = str(receipt_id).strip()
    environment = Environment.PRODUCTION
    status = map_order_status(raw)
    shipping_status = map_shipping_status(raw)
    total_cents = _money_to_cents(raw.get("grandtotal") or raw.get("total_price"))
    customer = normalize_customer(raw)
    lines = normalize_line_items(raw)

    defaults = {
        "user": user,
        "status": status,
        "shipping_status": shipping_status,
        "currency": str(
            (raw.get("grandtotal") or {}).get("currency_code")
            if isinstance(raw.get("grandtotal"), dict)
            else raw.get("currency_code") or "USD"
        ).upper(),
        "total_cents": total_cents,
        "customer_info_json": customer,
        "line_items_json": lines,
        "raw_payload_json": raw,
        "external_order_number": external_key,
    }
    order, _created = MarketplaceOrder.objects.update_or_create(
        store=store,
        environment=environment,
        external_order_key=external_key,
        defaults=defaults,
    )
    return order


def fetch(user, store) -> dict:
    """Pull shop receipts and upsert MarketplaceOrder rows."""
    adapter = get_adapter(store)
    last = getattr(store, "etsy_last_order_sync_at", None)
    if last:
        min_created = int((last - SYNC_OVERLAP).timestamp())
    else:
        min_created = int((timezone.now() - INITIAL_LOOKBACK).timestamp())

    saved = 0
    offset = 0
    newest_ts = last
    try:
        while True:
            data = adapter.get_shop_receipts(limit=100, offset=offset, min_created=min_created, was_paid=True)
            results = data.get("results") if isinstance(data, dict) else None
            if not isinstance(results, list) or not results:
                break
            for raw in results:
                if not isinstance(raw, dict):
                    continue
                upsert_order(user, store, raw)
                saved += 1
                created = raw.get("created_timestamp") or raw.get("create_timestamp")
                try:
                    created_i = int(created) if created is not None else None
                except (TypeError, ValueError):
                    created_i = None
                if created_i:
                    dt = datetime.fromtimestamp(created_i, tz=dt_timezone.utc)
                    if newest_ts is None or dt > newest_ts:
                        newest_ts = dt
            if len(results) < 100:
                break
            offset += 100
            if offset > 2000:
                break
    except EtsyAPIError as exc:
        logger.warning("Etsy order fetch failed store=%s: %s", store.id, exc)
        return {"ok": False, "message": str(exc), "fetched": saved}

    if newest_ts and hasattr(store, "etsy_last_order_sync_at"):
        store.etsy_last_order_sync_at = newest_ts
        try:
            store.save(update_fields=["etsy_last_order_sync_at"])
        except Exception:  # noqa: BLE001
            logger.warning("Could not persist etsy_last_order_sync_at for store=%s", store.id)

    return {
        "ok": True,
        "message": f"Retrieved {saved} order(s) from Etsy.",
        "fetched": saved,
    }


def cancel_reasons(_store=None) -> list[dict]:
    # Etsy seller cancel via Open API is limited; expose empty list so UI hides cancel.
    return []


def cancel(order: MarketplaceOrder, *, reason: str = "") -> dict:
    raise MarketplaceError(
        "Cancelling Etsy orders via API is not supported yet. "
        "Cancel or refund the order in Etsy Seller Manager."
    )
