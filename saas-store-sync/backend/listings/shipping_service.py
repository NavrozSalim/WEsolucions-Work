"""Send shipping/tracking info to Lasoo via Shipments_Upsert.

Lasoo's shipment API:
- ``Shipments_Upsert`` (/Shipments/Upsert/1.0.0): create/update a shipment with
  tracking number, carrier, status and the items that were shipped.
There is no separate "complete" endpoint; marking complete is an upsert with
``status = "DELIVERED"``.
"""
import logging

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

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


def submit(order: MarketplaceOrder, *, tracking_number: str, carrier: str,
           tracking_url: str = "", shipped_date: str = "", status: str = "") -> dict:
    """Submit tracking info for an order (status defaults to OUT_FOR_DELIVERY)."""
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
    """Mark a shipment delivered (Shipments_Upsert with status DELIVERED)."""
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
