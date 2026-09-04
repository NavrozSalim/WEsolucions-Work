"""Send shipping/tracking info to marketplace (Lasoo Shipments_Upsert / MyDeal fulfill).

Lasoo's shipment API:
- ``Shipments_Upsert`` (/Shipments/Upsert/1.0.0): create/update a shipment with
  tracking number, carrier, status and the items that were shipped.
There is no separate "complete" endpoint; marking complete is an upsert with
``status = "DELIVERED"``.

MyDeal (WMP):
- ``POST /orders/fulfill`` with OrderFulfillment + tracking per OrderItem.
"""
import logging

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from stores.credentials import marketplace_kind

from .errors import MarketplaceError
from .lasoo.client import LasooClient
from .lasoo.queries import build_payload
from .models import MarketplaceOrder, OrderShipment, OrderStatus

logger = logging.getLogger("listings")


def _invoice_id(order: MarketplaceOrder):
    """Lasoo expects a numeric invoiceId; fall back to the raw string if needed."""
    for candidate in (order.external_order_key, order.invoice_number):
        text = str(candidate or "").strip()
        if text.isdigit():
            return int(text)
    return order.external_order_key or order.invoice_number


def _parse_dt(value):
    if not value:
        return None
    dt = parse_datetime(value)
    if dt is None:
        d = parse_date(value)
        if d is not None:
            dt = timezone.datetime(d.year, d.month, d.day)
    if dt is not None and timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_default_timezone())
    return dt


def _to_iso(value: str) -> str:
    dt = _parse_dt(value)
    return (dt or timezone.now()).isoformat()


def _shipped_items(order: MarketplaceOrder) -> list[dict]:
    """Build Lasoo ``shippedItems`` from the order's stored line items."""
    items = order.line_items_json
    if not isinstance(items, list):
        return []

    shipped = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        shipped.append(
            {
                "quantity": raw.get("quantity") or raw.get("qty") or 1,
                "lineItemId": raw.get("lineItemId") or raw.get("id") or raw.get("lineId"),
                "externalProductKey": raw.get("externalProductKey") or raw.get("productKey") or "",
                "externalVariantKey": raw.get("externalVariantKey") or raw.get("variantKey") or "",
            }
        )
    return shipped


def _upsert(client: LasooClient, order: MarketplaceOrder, *, tracking_number, carrier,
            tracking_url, dispatched_at_iso, status, note=""):
    data = {
        "invoiceId": _invoice_id(order),
        "shipmentTrackingNumber": tracking_number,
        "shipmentCarrier": carrier,
        "dispatchedAt": dispatched_at_iso,
        "note": note,
        "status": status,
        "shippedItems": _shipped_items(order),
        "shipmentTrackingLink": tracking_url,
    }
    payload = build_payload("shipping", data=data, auth=client.auth_key)
    return payload, client.send("shipping", payload)


def _submit_mydeal(order: MarketplaceOrder, *, tracking_number: str, carrier: str,
                   shipped_date: str = "") -> dict:
    from .mydeal import orders as mydeal_orders
    from .mydeal.client import MyDealClient

    shipment = OrderShipment.objects.create(
        order=order,
        tracking_number=(tracking_number or "").strip(),
        carrier=(carrier or "").strip(),
        tracking_url="",
        shipped_at=_parse_dt(shipped_date),
        status="submitted",
    )
    try:
        client = MyDealClient(order.store)
    except MarketplaceError as exc:
        shipment.status = "failed"
        shipment.marketplace_response_json = {"error": str(exc)}
        shipment.save(update_fields=["status", "marketplace_response_json"])
        return {"ok": False, "message": str(exc), "shipment_id": str(shipment.id)}

    fulfillment = mydeal_orders.build_fulfillment_payload(
        order,
        tracking_number=tracking_number,
        carrier=carrier,
        shipped_date=shipped_date or _to_iso(""),
    )
    if not fulfillment.get("FulfillmentItems"):
        shipment.status = "failed"
        shipment.marketplace_response_json = {"error": "No line items to fulfill"}
        shipment.save(update_fields=["status", "marketplace_response_json"])
        return {
            "ok": False,
            "message": "No MyDeal line items available to fulfill.",
            "shipment_id": str(shipment.id),
        }

    result = client.fulfill_orders([fulfillment])
    shipment.marketplace_request_json = fulfillment
    shipment.marketplace_response_json = result.data if result.ok else {"error": result.message, "data": result.data}
    shipment.status = "submitted" if result.ok else "failed"
    shipment.save(update_fields=["marketplace_request_json", "marketplace_response_json", "status"])

    if result.ok:
        order.shipping_status = "submitted"
        order.status = OrderStatus.SHIPPING_SUBMITTED
        order.save(update_fields=["shipping_status", "status", "updated_at"])

    return {
        "ok": result.ok,
        "message": result.message or ("Shipping info sent to MyDeal." if result.ok else "MyDeal fulfill failed."),
        "shipment_id": str(shipment.id),
    }


def _submit_bunnings(order: MarketplaceOrder, *, tracking_number: str, carrier: str,
                     tracking_url: str = "") -> dict:
    from .bunnings import orders as bunnings_orders
    from .bunnings.client import BunningsClient

    tracking = (tracking_number or "").strip()
    if not tracking:
        raise MarketplaceError("Tracking number is required for Bunnings shipments.")
    order_id = (order.external_order_key or order.invoice_number or "").strip()
    if not order_id:
        raise MarketplaceError("Bunnings order id is missing on this order.")

    shipment = OrderShipment.objects.create(
        order=order,
        tracking_number=tracking,
        carrier=(carrier or "").strip(),
        tracking_url=(tracking_url or "").strip(),
        shipped_at=timezone.now(),
        status="submitted",
    )
    try:
        client = BunningsClient(order.store)
    except MarketplaceError as exc:
        shipment.status = "failed"
        shipment.marketplace_response_json = {"error": str(exc)}
        shipment.save(update_fields=["status", "marketplace_response_json"])
        return {"ok": False, "message": str(exc), "shipment_id": str(shipment.id)}

    payload = bunnings_orders.build_tracking_payload(
        tracking_number=tracking,
        carrier=carrier,
        tracking_url=tracking_url,
    )
    track = client.update_tracking(order_id, payload)
    ship = None
    if track.ok:
        ship = client.ship_order(order_id)
    ok = bool(track.ok and (ship is None or ship.ok))
    shipment.marketplace_request_json = payload
    shipment.marketplace_response_json = {
        "tracking": track.data if track.ok else {"error": track.message},
        "ship": None if ship is None else (ship.data if ship.ok else {"error": ship.message}),
    }
    shipment.status = "submitted" if ok else "failed"
    shipment.save(update_fields=["marketplace_request_json", "marketplace_response_json", "status"])

    if ok:
        order.shipping_status = "submitted"
        order.status = OrderStatus.SHIPPING_SUBMITTED
        order.save(update_fields=["shipping_status", "status", "updated_at"])

    if not track.ok:
        message = track.message or "Bunnings tracking update failed."
    elif ship is not None and not ship.ok:
        message = (
            "Tracking was sent, but Bunnings ship validation failed: "
            f"{ship.message or 'OR24 failed'}."
        )
    else:
        message = "Shipping info sent to Bunnings."
    return {
        "ok": ok,
        "message": message,
        "shipment_id": str(shipment.id),
    }


def _submit_etsy(order: MarketplaceOrder, *, tracking_number: str, carrier: str) -> dict:
    from store_adapters import get_adapter
    from store_adapters.etsy_adapter import EtsyAPIError

    tracking = (tracking_number or "").strip()
    if not tracking:
        raise MarketplaceError("Tracking number is required for Etsy shipments.")
    receipt_id = (order.external_order_key or order.invoice_number or "").strip()
    if not receipt_id:
        raise MarketplaceError("Etsy receipt id is missing on this order.")

    shipment = OrderShipment.objects.create(
        order=order,
        tracking_number=tracking,
        carrier=(carrier or "").strip(),
        tracking_url="",
        shipped_at=timezone.now(),
        status="submitted",
    )
    adapter = get_adapter(order.store)
    try:
        resp = adapter.create_receipt_shipment(
            receipt_id,
            tracking_code=tracking,
            carrier_name=(carrier or "").strip() or "other",
        )
        shipment.marketplace_request_json = {
            "receipt_id": receipt_id,
            "tracking_code": tracking,
            "carrier_name": carrier,
        }
        shipment.marketplace_response_json = resp
        shipment.status = "submitted"
        shipment.save(
            update_fields=["marketplace_request_json", "marketplace_response_json", "status"]
        )
        order.shipping_status = "submitted"
        order.status = OrderStatus.SHIPPING_SUBMITTED
        order.save(update_fields=["shipping_status", "status", "updated_at"])
        return {
            "ok": True,
            "message": "Shipping info sent to Etsy.",
            "shipment_id": str(shipment.id),
        }
    except EtsyAPIError as exc:
        shipment.status = "failed"
        shipment.marketplace_response_json = {"error": str(exc)}
        shipment.save(update_fields=["status", "marketplace_response_json"])
        return {
            "ok": False,
            "message": str(exc) or "Etsy tracking submit failed.",
            "shipment_id": str(shipment.id),
        }


def submit(order: MarketplaceOrder, *, tracking_number: str, carrier: str,
           tracking_url: str = "", shipped_date: str = "", status: str = "") -> dict:
    """Submit tracking info for an order."""
    kind = marketplace_kind(order.store.marketplace)
    if kind == "mydeal":
        result = _submit_mydeal(
            order,
            tracking_number=tracking_number,
            carrier=carrier,
            shipped_date=shipped_date,
        )
    elif kind == "etsy":
        result = _submit_etsy(
            order,
            tracking_number=tracking_number,
            carrier=carrier,
        )
    elif kind == "reverb":
        raise MarketplaceError(
            "Shipping/tracking push is not supported for Reverb yet. "
            "Mark shipped in Reverb seller tools, or ask for Reverb ship API support."
        )
    elif kind == "bunnings":
        result = _submit_bunnings(
            order,
            tracking_number=tracking_number,
            carrier=carrier,
            tracking_url=tracking_url,
        )
    elif kind != "lasoo":
        raise MarketplaceError(
            f'Shipping is not supported yet for "{kind or "this marketplace"}".'
        )
    else:
        result = _submit_lasoo(
            order,
            tracking_number=tracking_number,
            carrier=carrier,
            tracking_url=tracking_url,
            shipped_date=shipped_date,
            status=status,
        )
    if result.get("ok"):
        from .shopify.orders import push_fulfillment_to_shopify
        push_fulfillment_to_shopify(
            order,
            order.store,
            tracking_number=tracking_number,
            carrier=carrier,
            tracking_url=tracking_url,
        )
    return result


def _submit_lasoo(order: MarketplaceOrder, *, tracking_number: str, carrier: str,
                  tracking_url: str = "", shipped_date: str = "", status: str = "") -> dict:
    client = LasooClient(order.store, order.environment)
    ship_status = (status or "OUT_FOR_DELIVERY").strip()

    shipment = OrderShipment.objects.create(
        order=order,
        tracking_number=(tracking_number or "").strip(),
        carrier=(carrier or "").strip(),
        tracking_url=(tracking_url or "").strip(),
        shipped_at=_parse_dt(shipped_date),
        status="submitted",
    )

    api_payload, result = _upsert(
        client,
        order,
        tracking_number=shipment.tracking_number,
        carrier=shipment.carrier,
        tracking_url=shipment.tracking_url,
        dispatched_at_iso=_to_iso(shipped_date),
        status=ship_status,
    )

    shipment.marketplace_request_json = {**api_payload, "auth": "***"}
    shipment.marketplace_response_json = result.data if result.ok else result.error
    shipment.status = "submitted" if result.ok else "failed"
    shipment.save(update_fields=["marketplace_request_json", "marketplace_response_json", "status"])

    if result.ok:
        order.shipping_status = "submitted"
        order.status = OrderStatus.SHIPPING_SUBMITTED
        order.save(update_fields=["shipping_status", "status", "updated_at"])

    return {
        "ok": result.ok,
        "message": result.message or ("Shipping info sent." if result.ok else ""),
        "shipment_id": str(shipment.id),
    }


def complete(order: MarketplaceOrder) -> dict:
    """Mark a shipment delivered (Lasoo) or confirm MyDeal already fulfilled."""
    kind = marketplace_kind(order.store.marketplace)
    if kind == "mydeal":
        # MyDeal has no separate "delivered" call — fulfill already marks Shipped.
        order.shipping_status = "complete"
        order.status = OrderStatus.SHIPPING_COMPLETE
        order.save(update_fields=["shipping_status", "status", "updated_at"])
        last = order.shipments.first()
        if last:
            last.status = "complete"
            last.save(update_fields=["status"])
        return {
            "ok": True,
            "message": "Marked complete locally (MyDeal ships via /orders/fulfill).",
        }
    if kind == "etsy":
        order.shipping_status = "complete"
        order.status = OrderStatus.SHIPPING_COMPLETE
        order.save(update_fields=["shipping_status", "status", "updated_at"])
        last = order.shipments.first()
        if last:
            last.status = "complete"
            last.save(update_fields=["status"])
        return {
            "ok": True,
            "message": "Marked complete locally (Etsy tracking already marks shipped).",
        }
    if kind == "bunnings":
        order.shipping_status = "complete"
        order.status = OrderStatus.SHIPPING_COMPLETE
        order.save(update_fields=["shipping_status", "status", "updated_at"])
        last = order.shipments.first()
        if last:
            last.status = "complete"
            last.save(update_fields=["status"])
        return {
            "ok": True,
            "message": "Marked complete locally (Bunnings ship is OR23 tracking + OR24 ship).",
        }
    if kind == "reverb":
        raise MarketplaceError("Shipping complete is not supported for Reverb yet.")
    if kind != "lasoo":
        raise MarketplaceError(
            f'Shipping complete is not supported yet for "{kind or "this marketplace"}".'
        )

    client = LasooClient(order.store, order.environment)

    last = order.shipments.first()  # ordered by -created_at
    _, result = _upsert(
        client,
        order,
        tracking_number=last.tracking_number if last else "",
        carrier=last.carrier if last else "",
        tracking_url=last.tracking_url if last else "",
        dispatched_at_iso=_to_iso(""),
        status="DELIVERED",
        note="Marked complete from store sync",
    )

    if result.ok:
        order.shipping_status = "complete"
        order.status = OrderStatus.SHIPPING_COMPLETE
        order.save(update_fields=["shipping_status", "status", "updated_at"])
        if last:
            last.status = "complete"
            last.marketplace_response_json = result.data
            last.save(update_fields=["status", "marketplace_response_json"])

    return {
        "ok": result.ok,
        "message": result.message or ("Shipping marked complete." if result.ok else ""),
    }
