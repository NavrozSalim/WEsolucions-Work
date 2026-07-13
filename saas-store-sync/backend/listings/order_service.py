"""Retrieve and persist orders/invoices from the store's marketplace (Lasoo)."""
import logging

from stores.credentials import marketplace_kind

from .errors import MarketplaceError
from .lasoo.client import LasooClient
from .lasoo.queries import build_payload
from .models import MarketplaceOrder, OrderStatus

logger = logging.getLogger("listings")


def _require_lasoo(store):
    kind = marketplace_kind(store.marketplace)
    if kind != "lasoo":
        raise MarketplaceError(
            f'Order management is not supported yet for "{kind or "this marketplace"}". '
            'Currently only Lasoo stores can fetch orders.'
        )


def fetch(user, store, page: int = 1, take: int = 50) -> dict:
    """Pull invoices from Lasoo (Invoices_Search) and upsert them locally."""
    _require_lasoo(store)
    environment = store.lasoo_environment or 'staging'
    client = LasooClient(store, environment)

    payload = build_payload(
        "orders",
        data={
            "page": page,
            "take": take,
            "includeLineItems": True,
            "includeCustomer": True,
            "includeShipping": True,
            "includeInvoice": True,
        },
        auth=client.auth_key,
    )
    result = client.send("orders", payload)
    if not result.ok:
        return {"ok": False, "message": result.message, "fetched": 0}

    raw_orders = _extract_orders(result.data)
    saved = 0
    for raw in raw_orders:
        if isinstance(raw, dict):
            _upsert_order(user, store, environment, raw)
            saved += 1

    return {"ok": True, "message": f"Retrieved {saved} order(s) from {environment}.", "fetched": saved}


def create_test_order(user, store) -> dict:
    """Ask Lasoo to create a test order (staging) so the flow can be tested."""
    _require_lasoo(store)
    environment = store.lasoo_environment or 'staging'
    client = LasooClient(store, environment)

    payload = build_payload("create_test_order", data={}, auth=client.auth_key)
    result = client.send("create_test_order", payload)
    if not result.ok:
        return {"ok": False, "message": result.message}

    # Best-effort: persist any returned order so it shows up immediately.
    for raw in _extract_orders(result.data):
        if isinstance(raw, dict):
            _upsert_order(user, store, environment, raw)

    return {"ok": True, "message": "Test order created. Refresh orders to load it."}


def _extract_orders(data) -> list:
    """Dig through Lasoo's response envelope to find the list of invoices.

    Lasoo wraps payloads like ``{"results": {"body": {"invoices": [...]}}}`` so we
    recurse through the common container keys until we hit a list of dicts.
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("invoices", "orders", "items", "records", "results", "body", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                nested = _extract_orders(value)
                if nested:
                    return nested
    return []


def _upsert_order(user, store, environment, raw: dict) -> MarketplaceOrder:
    invoice_id = (
        raw.get("id")
        or raw.get("invoiceId")
        or raw.get("invoiceNumber")
        or raw.get("externalOrderKey")
        or raw.get("orderKey")
    )
    order_key = str(invoice_id or "")
    invoice_number = str(raw.get("invoiceNumber") or raw.get("invoice") or invoice_id or "")
    defaults = {
        "user": user,
        "invoice_number": invoice_number,
        "customer_info_json": raw.get("customer") or raw.get("customerInfo"),
        "line_items_json": raw.get("lineItems") or raw.get("items") or raw.get("products"),
        "total_amount_cents": _to_cents(
            raw.get("totalCents")
            or raw.get("total")
            or raw.get("totalAmount")
            or raw.get("grandTotal")
        ),
        "status": _map_status(raw.get("status")),
        "raw_response_json": raw,
    }
    order, _ = MarketplaceOrder.objects.update_or_create(
        store=store,
        external_order_key=order_key,
        environment=environment,
        defaults=defaults,
    )
    return order


def _to_cents(value):
    if value is None:
        return None
    try:
        if isinstance(value, int):
            return value
        return int(round(float(value) * 100))
    except (TypeError, ValueError):
        return None


def _map_status(raw_status) -> str:
    if not raw_status:
        return OrderStatus.NEW
    text = str(raw_status).strip().lower()
    mapping = {
        "new": OrderStatus.NEW,
        "paid": OrderStatus.PAID,
        "cancelled": OrderStatus.CANCELLED,
        "canceled": OrderStatus.CANCELLED,
        "refunded": OrderStatus.REFUNDED,
        "sent": OrderStatus.SENT,
        "shipped": OrderStatus.SENT,
        "out_for_delivery": OrderStatus.SHIPPING_SUBMITTED,
        "delivered": OrderStatus.SHIPPING_COMPLETE,
    }
    return mapping.get(text, OrderStatus.NEW)
