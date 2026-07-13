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

    for raw in _extract_orders(result.data):
        if isinstance(raw, dict):
            _upsert_order(user, store, environment, raw)

    return {"ok": True, "message": "Test order created. Refresh orders to load it."}


def _extract_orders(data) -> list:
    """Dig through Lasoo's response envelope to find the list of invoices."""
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


def _first(*values):
    for v in values:
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        if isinstance(v, (list, dict)) and not v:
            continue
        return v
    return None


def _dig(obj, *paths):
    """Return the first non-empty value found by dotted-key paths on a dict."""
    if not isinstance(obj, dict):
        return None
    for path in paths:
        cur = obj
        ok = True
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                ok = False
                break
            cur = cur[part]
        if ok and cur not in (None, "", [], {}):
            return cur
    return None


def _normalize_customer(raw: dict) -> dict | None:
    """Flatten customer + contact fields from Lasoo's varied shapes."""
    cust = _first(
        raw.get("customer"),
        raw.get("customerInfo"),
        raw.get("buyer"),
        _dig(raw, "invoice.customer"),
    )
    if not isinstance(cust, dict):
        cust = {}

    first = _first(cust.get("firstName"), cust.get("first_name"), cust.get("givenName"))
    last = _first(cust.get("lastName"), cust.get("last_name"), cust.get("familyName"))
    name = _first(
        cust.get("name"),
        cust.get("fullName"),
        " ".join(p for p in (first, last) if p).strip() or None,
    )
    email = _first(cust.get("email"), cust.get("emailAddress"), raw.get("customerEmail"))
    phone = _first(
        cust.get("phone"), cust.get("phoneNumber"), cust.get("mobile"),
        cust.get("mobileNumber"), raw.get("customerPhone"),
    )

    shipping = _normalize_address(
        _first(
            cust.get("shippingAddress"),
            cust.get("shipping_address"),
            cust.get("address"),
            raw.get("shippingAddress"),
            raw.get("shipping_address"),
            _dig(raw, "shipping.address"),
            _dig(raw, "shipping.shippingAddress"),
            _dig(raw, "invoice.shippingAddress"),
        )
    )
    billing = _normalize_address(
        _first(
            cust.get("billingAddress"),
            cust.get("billing_address"),
            raw.get("billingAddress"),
            _dig(raw, "invoice.billingAddress"),
        )
    )

    out = {
        "firstName": first or "",
        "lastName": last or "",
        "name": name or "",
        "email": email or "",
        "phone": phone or "",
    }
    if shipping:
        out["shippingAddress"] = shipping
    if billing:
        out["billingAddress"] = billing
    # Keep original customer blob for anything we didn't map.
    if cust:
        out["_raw"] = cust
    return out if any(out.get(k) for k in ("name", "email", "phone", "firstName", "lastName")) or shipping or billing else None


def _normalize_address(addr) -> dict | None:
    if not isinstance(addr, dict):
        return None
    line1 = _first(addr.get("line1"), addr.get("address1"), addr.get("street"), addr.get("street1"), addr.get("addressLine1"))
    line2 = _first(addr.get("line2"), addr.get("address2"), addr.get("street2"), addr.get("addressLine2"))
    city = _first(addr.get("city"), addr.get("suburb"), addr.get("town"))
    state = _first(addr.get("state"), addr.get("region"), addr.get("province"))
    postcode = _first(addr.get("postcode"), addr.get("postalCode"), addr.get("zip"), addr.get("zipCode"))
    country = _first(addr.get("country"), addr.get("countryCode"), addr.get("countryName"))
    company = _first(addr.get("company"), addr.get("companyName"), addr.get("businessName"))
    out = {
        "line1": line1 or "",
        "line2": line2 or "",
        "city": city or "",
        "state": state or "",
        "postcode": postcode or "",
        "country": country or "",
        "company": company or "",
    }
    if not any(out.values()):
        return None
    return out


def _normalize_line_items(raw: dict) -> list:
    items = _first(
        raw.get("lineItems"),
        raw.get("items"),
        raw.get("products"),
        raw.get("variants"),
        _dig(raw, "invoice.lineItems"),
        _dig(raw, "invoice.items"),
    )
    if not isinstance(items, list):
        return []

    normalized = []
    for it in items:
        if not isinstance(it, dict):
            continue
        title = _first(
            it.get("title"), it.get("name"), it.get("productName"),
            it.get("variantName"), it.get("description"),
        )
        sku = _first(it.get("sku"), it.get("SKU"), it.get("sellerSku"))
        variant_key = _first(
            it.get("externalVariantKey"), it.get("variantKey"),
            it.get("external_variant_key"), sku,
        )
        product_key = _first(
            it.get("externalProductKey"), it.get("productKey"),
            it.get("external_product_key"),
        )
        qty = it.get("quantity") if it.get("quantity") is not None else it.get("qty")
        try:
            qty = int(qty) if qty is not None else 1
        except (TypeError, ValueError):
            qty = 1
        price_cents = _to_cents(
            _first(
                it.get("priceCents"), it.get("salePriceCents"),
                it.get("unitPriceCents"), it.get("lineTotalCents"),
                it.get("price"), it.get("salePrice"), it.get("unitPrice"), it.get("amount"),
            )
        )
        normalized.append({
            "title": title or "",
            "sku": sku or "",
            "externalVariantKey": variant_key or "",
            "externalProductKey": product_key or "",
            "quantity": qty,
            "priceCents": price_cents,
            "lineItemId": _first(it.get("lineItemId"), it.get("id"), it.get("lineId")),
            "imageUrl": _first(it.get("imageUrl"), it.get("image"), it.get("thumbnail")),
            "_raw": it,
        })
    return normalized


def _normalize_totals(raw: dict) -> dict:
    return {
        "totalCents": _to_cents(_first(
            raw.get("totalCents"), raw.get("grandTotalCents"),
            raw.get("total"), raw.get("totalAmount"), raw.get("grandTotal"),
            _dig(raw, "invoice.totalCents"), _dig(raw, "invoice.total"),
        )),
        "subtotalCents": _to_cents(_first(
            raw.get("subtotalCents"), raw.get("subTotalCents"),
            raw.get("subtotal"), raw.get("subTotal"),
            _dig(raw, "invoice.subtotalCents"),
        )),
        "shippingCents": _to_cents(_first(
            raw.get("shippingCents"), raw.get("shippingTotalCents"),
            raw.get("shippingCost"), raw.get("shippingFee"),
            _dig(raw, "shipping.totalCents"), _dig(raw, "invoice.shippingCents"),
        )),
        "taxCents": _to_cents(_first(
            raw.get("taxCents"), raw.get("gstCents"), raw.get("tax"), raw.get("gst"),
            _dig(raw, "invoice.taxCents"),
        )),
        "discountCents": _to_cents(_first(
            raw.get("discountCents"), raw.get("discount"),
            _dig(raw, "invoice.discountCents"),
        )),
        "currency": _first(raw.get("currency"), raw.get("currencyCode"), "AUD") or "AUD",
    }


def _normalize_shipping_info(raw: dict) -> dict | None:
    ship = _first(raw.get("shipping"), raw.get("shipment"), raw.get("shipments"), _dig(raw, "invoice.shipping"))
    if isinstance(ship, list) and ship:
        ship = ship[0]
    if not isinstance(ship, dict):
        # Still surface address-only shipping even without a shipping block.
        addr = _normalize_address(_first(raw.get("shippingAddress"), raw.get("shipping_address")))
        return {"address": addr} if addr else None

    return {
        "status": _first(ship.get("status"), ship.get("shippingStatus"), ship.get("state")),
        "method": _first(ship.get("method"), ship.get("shippingMethod"), ship.get("carrier")),
        "trackingNumber": _first(ship.get("trackingNumber"), ship.get("shipmentTrackingNumber"), ship.get("tracking")),
        "trackingUrl": _first(ship.get("trackingUrl"), ship.get("shipmentTrackingLink"), ship.get("trackingLink")),
        "carrier": _first(ship.get("carrier"), ship.get("shipmentCarrier")),
        "dispatchedAt": _first(ship.get("dispatchedAt"), ship.get("shippedAt"), ship.get("dispatched_at")),
        "address": _normalize_address(_first(
            ship.get("address"), ship.get("shippingAddress"), ship.get("destination"),
        )),
        "_raw": ship,
    }


def _order_dates(raw: dict) -> dict:
    return {
        "orderedAt": _first(
            raw.get("orderedAt"), raw.get("orderDate"), raw.get("createdAt"),
            raw.get("created_at"), raw.get("date"),
            _dig(raw, "invoice.createdAt"), _dig(raw, "invoice.orderDate"),
        ),
        "paidAt": _first(raw.get("paidAt"), raw.get("paid_at"), _dig(raw, "invoice.paidAt")),
        "updatedAt": _first(raw.get("updatedAt"), raw.get("updated_at")),
    }


def build_order_details(raw: dict | None, customer_info=None, line_items=None, total_cents=None) -> dict:
    """Normalized view of an order for the API/UI (works from raw and/or stored fields)."""
    raw = raw if isinstance(raw, dict) else {}
    customer = customer_info if isinstance(customer_info, dict) else _normalize_customer(raw) or {}
    items = line_items if isinstance(line_items, list) else _normalize_line_items(raw)
    # If stored line items look un-normalized (no title keys), re-normalize from raw.
    if items and isinstance(items[0], dict) and "title" not in items[0] and raw:
        items = _normalize_line_items(raw) or items
    totals = _normalize_totals(raw)
    if totals.get("totalCents") is None and total_cents is not None:
        totals["totalCents"] = total_cents
    shipping = _normalize_shipping_info(raw)
    if not shipping and isinstance(customer.get("shippingAddress"), dict):
        shipping = {"address": customer["shippingAddress"]}
    dates = _order_dates(raw)
    return {
        "customer": {
            "firstName": customer.get("firstName") or "",
            "lastName": customer.get("lastName") or "",
            "name": customer.get("name") or "",
            "email": customer.get("email") or "",
            "phone": customer.get("phone") or "",
        },
        "shippingAddress": (
            (shipping or {}).get("address")
            or customer.get("shippingAddress")
            or None
        ),
        "billingAddress": customer.get("billingAddress") or None,
        "lineItems": items,
        "totals": totals,
        "shipping": shipping,
        "dates": dates,
        "marketplaceStatus": _first(raw.get("status"), raw.get("invoiceStatus"), _dig(raw, "invoice.status")),
    }


def _upsert_order(user, store, environment, raw: dict) -> MarketplaceOrder:
    invoice_id = _first(
        raw.get("id"),
        raw.get("invoiceId"),
        raw.get("invoiceNumber"),
        raw.get("externalOrderKey"),
        raw.get("orderKey"),
        _dig(raw, "invoice.id"),
        _dig(raw, "invoice.invoiceNumber"),
    )
    order_key = str(invoice_id or "")
    invoice_number = str(
        _first(raw.get("invoiceNumber"), raw.get("invoice"), _dig(raw, "invoice.invoiceNumber"), invoice_id)
        or ""
    )
    customer = _normalize_customer(raw)
    line_items = _normalize_line_items(raw)
    totals = _normalize_totals(raw)

    defaults = {
        "user": user,
        "invoice_number": invoice_number,
        "customer_info_json": customer,
        "line_items_json": line_items,
        "total_amount_cents": totals.get("totalCents"),
        "status": _map_status(_first(raw.get("status"), _dig(raw, "invoice.status"))),
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
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            # Heuristic: values >= 1000 without a decimal are usually already cents
            # for marketplace totals; leave small ints as cents too (Lasoo uses cents).
            return value
        text = str(value).strip().replace(",", "")
        if "." in text:
            return int(round(float(text) * 100))
        return int(text)
    except (TypeError, ValueError):
        return None


def _map_status(raw_status) -> str:
    if not raw_status:
        return OrderStatus.NEW
    text = str(raw_status).strip().lower().replace(" ", "_").replace("-", "_")
    mapping = {
        "new": OrderStatus.NEW,
        "paid": OrderStatus.PAID,
        "cancelled": OrderStatus.CANCELLED,
        "canceled": OrderStatus.CANCELLED,
        "refunded": OrderStatus.REFUNDED,
        "sent": OrderStatus.SENT,
        "shipped": OrderStatus.SENT,
        "out_for_delivery": OrderStatus.SHIPPING_SUBMITTED,
        "shipping_submitted": OrderStatus.SHIPPING_SUBMITTED,
        "delivered": OrderStatus.SHIPPING_COMPLETE,
        "shipping_complete": OrderStatus.SHIPPING_COMPLETE,
        "complete": OrderStatus.SHIPPING_COMPLETE,
        "completed": OrderStatus.SHIPPING_COMPLETE,
    }
    return mapping.get(text, OrderStatus.NEW)
