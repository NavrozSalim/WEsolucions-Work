"""Retrieve and persist orders/invoices from the store's marketplace (Lasoo / Reverb)."""
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
    """Pull orders from the store's marketplace and upsert them locally."""
    kind = marketplace_kind(store.marketplace)
    if kind == "reverb":
        from .reverb import orders as reverb_orders
        return reverb_orders.fetch(user, store)
    if kind != "lasoo":
        raise MarketplaceError(
            f'Order management is not supported yet for "{kind or "this marketplace"}". '
            'Currently Lasoo and Reverb managed stores can fetch orders.'
        )
    return _fetch_lasoo(user, store, page=page, take=take)


def _fetch_lasoo(user, store, page: int = 1, take: int = 50) -> dict:
    """Pull invoices from Lasoo (Invoices_Search) and upsert them locally."""
    environment = store.lasoo_environment or 'staging'
    client = LasooClient(store, environment)

    payload = build_payload(
        "orders",
        data=_orders_search_data(page=page, take=take),
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


def _orders_search_data(*, page: int = 1, take: int = 50, **extra) -> dict:
    """Flags for Invoices_Search so customer + shipping address come back with line items."""
    data = {
        "page": page,
        "take": take,
        "includeLineItems": True,
        "includeCustomer": True,
        "includeShipping": True,
        "includeShippingAddress": True,
        "includeDeliveryAddress": True,
        "includeAddresses": True,
        "includeInvoice": True,
        "includeShipments": True,
    }
    data.update(extra)
    return data


def create_test_order(user, store) -> dict:
    """Ask Lasoo to create a test order (staging) so the flow can be tested."""
    kind = marketplace_kind(store.marketplace)
    if kind == "reverb":
        raise MarketplaceError(
            "Reverb has no test-order API. Use Fetch orders to pull real selling orders "
            "from api.reverb.com."
        )
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

    # CreateTestOrder often returns a thin payload (no customer/shipping).
    # Immediately re-fetch invoices with full includes so the UI can show address.
    refresh = fetch(user, store, page=1, take=50)
    if refresh.get("ok"):
        return {
            "ok": True,
            "message": "Test order created and orders refreshed (including shipping when Lasoo provides it).",
        }
    return {
        "ok": True,
        "message": (
            "Test order created. Refresh orders to load it. "
            f"(Full refresh note: {refresh.get('message') or 'unavailable'})"
        ),
    }


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


def _address_candidates(raw: dict, cust: dict) -> list:
    """Collect possible address blobs from Lasoo invoice / customer shapes."""
    return [
        cust.get("shippingAddress"),
        cust.get("shipping_address"),
        cust.get("deliveryAddress"),
        cust.get("shipToAddress"),
        cust.get("shipTo"),
        cust.get("address"),
        raw.get("shippingAddress"),
        raw.get("shipping_address"),
        raw.get("deliveryAddress"),
        raw.get("delivery_address"),
        raw.get("shipToAddress"),
        raw.get("shipTo"),
        raw.get("destinationAddress"),
        _dig(raw, "shipping.address"),
        _dig(raw, "shipping.shippingAddress"),
        _dig(raw, "shipping.deliveryAddress"),
        _dig(raw, "shipping.destination"),
        _dig(raw, "shipment.address"),
        _dig(raw, "shipment.shippingAddress"),
        _dig(raw, "invoice.shippingAddress"),
        _dig(raw, "invoice.deliveryAddress"),
        _dig(raw, "invoice.shipTo"),
        _dig(raw, "invoice.customer.shippingAddress"),
        _dig(raw, "invoice.customer.address"),
        _flatten_prefixed_address(raw, "shipping"),
        _flatten_prefixed_address(raw, "delivery"),
        _flatten_prefixed_address(raw, "shipTo"),
        _flatten_prefixed_address(cust, "shipping"),
        _flatten_prefixed_address(cust, "delivery"),
    ]


def _flatten_prefixed_address(obj: dict | None, prefix: str) -> dict | None:
    """Build an address dict from flat keys like shippingLine1 / deliverySuburb."""
    if not isinstance(obj, dict):
        return None
    prefixes = (prefix, prefix.lower(), prefix[:1].upper() + prefix[1:] if prefix else prefix)
    keys = {
        "line1": ("Line1", "Address1", "Street", "Street1", "AddressLine1", "Address"),
        "line2": ("Line2", "Address2", "Street2", "AddressLine2"),
        "city": ("City", "Suburb", "Town"),
        "state": ("State", "Region", "Province"),
        "postcode": ("Postcode", "PostalCode", "Zip", "ZipCode"),
        "country": ("Country", "CountryCode", "CountryName"),
        "company": ("Company", "CompanyName", "BusinessName"),
    }
    built = {}
    for field, suffixes in keys.items():
        for pfx in prefixes:
            for suf in suffixes:
                for candidate in (f"{pfx}{suf}", f"{pfx}_{suf}", f"{pfx}{suf[0].lower()}{suf[1:]}"):
                    val = obj.get(candidate)
                    if val not in (None, ""):
                        built[field] = val
                        break
                if field in built:
                    break
            if field in built:
                break
    return built or None


def _normalize_customer(raw: dict) -> dict | None:
    """Flatten customer + contact fields from Lasoo's varied shapes."""
    cust = _first(
        raw.get("customer"),
        raw.get("customerInfo"),
        raw.get("buyer"),
        raw.get("recipient"),
        raw.get("consignee"),
        _dig(raw, "invoice.customer"),
        _dig(raw, "invoice.buyer"),
        _dig(raw, "shipping.customer"),
    )
    if not isinstance(cust, dict):
        cust = {}

    first = _first(
        cust.get("firstName"), cust.get("first_name"), cust.get("givenName"),
        raw.get("customerFirstName"), raw.get("shippingFirstName"),
        _dig(raw, "shipping.firstName"),
    )
    last = _first(
        cust.get("lastName"), cust.get("last_name"), cust.get("familyName"),
        raw.get("customerLastName"), raw.get("shippingLastName"),
        _dig(raw, "shipping.lastName"),
    )
    name = _first(
        cust.get("name"),
        cust.get("fullName"),
        raw.get("customerName"),
        raw.get("shippingName"),
        " ".join(p for p in (first, last) if p).strip() or None,
    )
    email = _first(
        cust.get("email"), cust.get("emailAddress"),
        raw.get("customerEmail"), raw.get("email"),
        _dig(raw, "shipping.email"),
    )
    phone = _first(
        cust.get("phone"), cust.get("phoneNumber"), cust.get("mobile"),
        cust.get("mobileNumber"), raw.get("customerPhone"), raw.get("phone"),
        _dig(raw, "shipping.phone"),
    )

    shipping = None
    for candidate in _address_candidates(raw, cust):
        shipping = _normalize_address(candidate)
        if shipping:
            break

    billing = _normalize_address(
        _first(
            cust.get("billingAddress"),
            cust.get("billing_address"),
            raw.get("billingAddress"),
            _dig(raw, "invoice.billingAddress"),
            _flatten_prefixed_address(raw, "billing"),
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
    if isinstance(addr, str) and addr.strip():
        # Single-line address string from some Lasoo payloads.
        return {
            "line1": addr.strip(),
            "line2": "",
            "city": "",
            "state": "",
            "postcode": "",
            "country": "",
            "company": "",
        }
    if not isinstance(addr, dict):
        return None
    # Nested { address: {...} } wrappers
    nested = _first(addr.get("address"), addr.get("shippingAddress"), addr.get("deliveryAddress"))
    if isinstance(nested, dict) and not any(
        addr.get(k) for k in ("line1", "address1", "street", "city", "suburb", "postcode", "postalCode")
    ):
        addr = nested
    line1 = _first(
        addr.get("line1"), addr.get("address1"), addr.get("street"), addr.get("street1"),
        addr.get("addressLine1"), addr.get("address"), addr.get("addressLine"),
    )
    line2 = _first(
        addr.get("line2"), addr.get("address2"), addr.get("street2"),
        addr.get("addressLine2"), addr.get("unit"),
    )
    city = _first(addr.get("city"), addr.get("suburb"), addr.get("town"), addr.get("locality"))
    state = _first(addr.get("state"), addr.get("region"), addr.get("province"), addr.get("stateCode"))
    postcode = _first(
        addr.get("postcode"), addr.get("postalCode"), addr.get("zip"),
        addr.get("zipCode"), addr.get("postCode"),
    )
    country = _first(
        addr.get("country"), addr.get("countryCode"), addr.get("countryName"), addr.get("nation"),
    )
    company = _first(addr.get("company"), addr.get("companyName"), addr.get("businessName"), addr.get("organisation"))
    out = {
        "line1": str(line1 or "").strip(),
        "line2": str(line2 or "").strip(),
        "city": str(city or "").strip(),
        "state": str(state or "").strip(),
        "postcode": str(postcode or "").strip(),
        "country": str(country or "").strip(),
        "company": str(company or "").strip(),
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
    ship = _first(
        raw.get("shipping"),
        raw.get("shipment"),
        raw.get("shipments"),
        raw.get("delivery"),
        _dig(raw, "invoice.shipping"),
        _dig(raw, "invoice.shipment"),
    )
    if isinstance(ship, list) and ship:
        ship = ship[0]
    if isinstance(ship, dict):
        addr = _normalize_address(_first(
            ship.get("address"),
            ship.get("shippingAddress"),
            ship.get("deliveryAddress"),
            ship.get("destination"),
            ship.get("shipTo"),
        ))
        info = {
            "status": _first(ship.get("status"), ship.get("shippingStatus"), ship.get("state")),
            "method": _first(ship.get("method"), ship.get("shippingMethod"), ship.get("carrier")),
            "trackingNumber": _first(
                ship.get("trackingNumber"), ship.get("shipmentTrackingNumber"), ship.get("tracking"),
            ),
            "trackingUrl": _first(
                ship.get("trackingUrl"), ship.get("shipmentTrackingLink"), ship.get("trackingLink"),
            ),
            "carrier": _first(ship.get("carrier"), ship.get("shipmentCarrier")),
            "dispatchedAt": _first(ship.get("dispatchedAt"), ship.get("shippedAt"), ship.get("dispatched_at")),
            "address": addr,
            "_raw": ship,
        }
        if addr or any(info.get(k) for k in ("status", "method", "trackingNumber", "carrier")):
            return info

    for candidate in _address_candidates(raw, {}):
        addr = _normalize_address(candidate)
        if addr:
            return {"address": addr}
    return None


def _merge_customer(stored: dict | None, from_raw: dict | None) -> dict:
    """Prefer non-empty stored fields; fill gaps from a fresh parse of Lasoo raw JSON."""
    a = stored if isinstance(stored, dict) else {}
    b = from_raw if isinstance(from_raw, dict) else {}
    out = {
        "firstName": a.get("firstName") or b.get("firstName") or "",
        "lastName": a.get("lastName") or b.get("lastName") or "",
        "name": a.get("name") or b.get("name") or "",
        "email": a.get("email") or b.get("email") or "",
        "phone": a.get("phone") or b.get("phone") or "",
    }
    ship = a.get("shippingAddress") if isinstance(a.get("shippingAddress"), dict) else None
    if not ship or not any(ship.values()):
        ship = b.get("shippingAddress") if isinstance(b.get("shippingAddress"), dict) else None
    bill = a.get("billingAddress") if isinstance(a.get("billingAddress"), dict) else None
    if not bill or not any(bill.values()):
        bill = b.get("billingAddress") if isinstance(b.get("billingAddress"), dict) else None
    if ship:
        out["shippingAddress"] = ship
    if bill:
        out["billingAddress"] = bill
    return out


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
    from_raw = _normalize_customer(raw) or {}
    stored = customer_info if isinstance(customer_info, dict) else {}
    # Always re-parse raw so shipping/customer apply even when older thin
    # customer_info_json was persisted (e.g. CreateTestOrder).
    customer = _merge_customer(stored, from_raw)
    items = line_items if isinstance(line_items, list) else _normalize_line_items(raw)
    # If stored line items look un-normalized (no title keys), re-normalize from raw.
    if items and isinstance(items[0], dict) and "title" not in items[0] and raw:
        items = _normalize_line_items(raw) or items
    totals = _normalize_totals(raw)
    if totals.get("totalCents") is None and total_cents is not None:
        totals["totalCents"] = total_cents
    shipping = _normalize_shipping_info(raw)
    if (not shipping or not shipping.get("address")) and isinstance(customer.get("shippingAddress"), dict):
        shipping = {**(shipping or {}), "address": customer["shippingAddress"]}
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


def _norm_match_key(value) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _listing_lookup_indexes(store):
    """Build match indexes from local StoreListing rows for order line enrichment."""
    from .models import StoreListing

    by_variant: dict[str, object] = {}
    by_sku: dict[str, object] = {}
    by_title: dict[str, object] = {}
    for listing in StoreListing.objects.filter(store=store).only(
        "sku", "external_variant_key", "external_product_key", "title", "vendor_url",
    ):
        vk = _norm_match_key(listing.external_variant_key)
        if vk and vk not in by_variant:
            by_variant[vk] = listing
        pk = _norm_match_key(listing.external_product_key)
        if pk and pk not in by_variant:
            by_variant[pk] = listing
        sku = _norm_match_key(listing.sku)
        if sku and sku not in by_sku:
            by_sku[sku] = listing
        title = _norm_match_key(listing.title)
        if title and title not in by_title:
            by_title[title] = listing
    return by_variant, by_sku, by_title


def _match_listing_for_line_item(item: dict, by_variant, by_sku, by_title):
    if not isinstance(item, dict):
        return None
    raw = item.get("_raw") if isinstance(item.get("_raw"), dict) else {}
    for key in (
        item.get("externalVariantKey"),
        item.get("externalProductKey"),
        item.get("sku"),
        raw.get("externalVariantKey"),
        raw.get("variantKey"),
        raw.get("externalProductKey"),
        raw.get("sku"),
        raw.get("SKU"),
        raw.get("sellerSku"),
    ):
        nk = _norm_match_key(key)
        if nk and nk in by_variant:
            return by_variant[nk]
        if nk and nk in by_sku:
            return by_sku[nk]
    title = _norm_match_key(item.get("title") or item.get("name") or raw.get("title") or raw.get("productName"))
    if title and title in by_title:
        return by_title[title]
    return None


def enrich_order_line_items(details: dict, store) -> dict:
    """Attach marketplaceSku / vendorUrl from local StoreListing matches.

    Also sets top-level ``sourceLinks`` for the orders list column:
    unique vendor URLs across line items (empty when none matched).
    """
    if not isinstance(details, dict) or store is None:
        return details
    items = details.get("lineItems")
    if not isinstance(items, list) or not items:
        details["sourceLinks"] = []
        return details

    by_variant, by_sku, by_title = _listing_lookup_indexes(store)
    enriched = []
    source_links: list[str] = []
    seen_links: set[str] = set()
    for it in items:
        if not isinstance(it, dict):
            enriched.append(it)
            continue
        row = dict(it)
        listing = _match_listing_for_line_item(row, by_variant, by_sku, by_title)
        marketplace_sku = ""
        vendor_url = ""
        if listing is not None:
            marketplace_sku = (listing.sku or listing.external_variant_key or "").strip()
            vendor_url = (listing.vendor_url or "").strip()
        # Prefer Lasoo-provided SKU when present; otherwise use our listing SKU.
        if not (row.get("sku") or "").strip() and marketplace_sku:
            row["sku"] = marketplace_sku
        row["marketplaceSku"] = marketplace_sku or (row.get("sku") or row.get("externalVariantKey") or "")
        row["vendorUrl"] = vendor_url
        if vendor_url and vendor_url not in seen_links:
            seen_links.add(vendor_url)
            source_links.append(vendor_url)
        enriched.append(row)

    details["lineItems"] = enriched
    details["sourceLinks"] = source_links
    return details


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


_CANCEL_BLOCKED = {
    OrderStatus.CANCELLED,
    OrderStatus.REFUNDED,
    OrderStatus.SHIPPING_COMPLETE,
}

# Lasoo (Marketplacer) seller cancel reasons — pre-dispatch.
# Connect does not expose RefundReason.preDispatchRefundReasons, so we ship the
# marketplace’s usual cancel set and merge any reasons already used via Refunds_Search.
LASOO_PRE_DISPATCH_CANCEL_REASONS = [
    "Out of stock",
    "Unable to fulfill",
    "Pricing error",
    "Incorrect listing",
    "Damaged / unsellable",
    "Shipping restriction",
    "Customer requested cancellation",
    "Other",
]


def _invoice_id_for_api(order: MarketplaceOrder):
    for candidate in (order.external_order_key, order.invoice_number):
        text = str(candidate or "").strip()
        if text.isdigit():
            return int(text)
    return order.external_order_key or order.invoice_number


def _refund_line_items(order: MarketplaceOrder, reason: str) -> list[dict]:
    items = order.line_items_json
    if not isinstance(items, list):
        return []

    out = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        nested = raw.get("_raw") if isinstance(raw.get("_raw"), dict) else {}
        line_id = (
            raw.get("lineItemId")
            or raw.get("id")
            or nested.get("id")
            or nested.get("lineItemId")
        )
        if line_id is None:
            continue
        qty = raw.get("quantity") or raw.get("qty") or nested.get("quantity") or 1
        price = (
            raw.get("priceCents")
            or raw.get("salePriceCents")
            or nested.get("salePriceCents")
            or 0
        )
        total = raw.get("totalCents") or nested.get("totalCents") or (int(price) * int(qty))
        out.append(
            {
                "lineItemId": line_id,
                "quantity": int(qty) if str(qty).isdigit() or isinstance(qty, int) else 1,
                "reason": reason,
                "salePriceCents": int(price or 0),
                "totalCents": int(total or 0),
                "amountPerItemCents": int(price or 0),
            }
        )
    return out


def _lasoo_results_ok(result) -> bool:
    """True when Lasoo HTTP layer succeeded and nested results.success is not false."""
    if not result.ok:
        return False
    data = result.data
    if not isinstance(data, dict):
        return True
    results = data.get("results")
    if isinstance(results, dict) and results.get("success") is False:
        return False
    return True


def _lasoo_error_message(result) -> str:
    data = result.data if isinstance(result.data, dict) else {}
    results = data.get("results") if isinstance(data.get("results"), dict) else {}
    for candidate in (
        results.get("message"),
        results.get("error"),
        result.message,
        data.get("message"),
        data.get("error"),
    ):
        text = str(candidate or "").strip()
        if not text or text.lower() == "no message":
            continue
        # Trim huge Prisma/stack dumps from Lasoo staging errors.
        if "prisma" in text.lower() or "Invalid `" in text:
            return "Lasoo refund API failed (server error). Cancel it in the Lasoo portal if needed."
        return text[:400]
    return "Lasoo cancel/refund request failed."


def _reason_option(label: str) -> dict:
    text = (label or "").strip()
    return {"value": text, "label": text}


def _lasoo_reasons_from_refunds(client: LasooClient) -> list[str]:
    """Collect distinct refundReason values already present for this retailer."""
    payload = build_payload(
        "refunds_search",
        data={"page": 1, "take": 100, "includeReasons": True},
        auth=client.auth_key,
    )
    result = client.send("refunds_search", payload)
    if not result.ok or not isinstance(result.data, dict):
        return []
    results = result.data.get("results") if isinstance(result.data.get("results"), dict) else {}
    refunds = results.get("refunds") if isinstance(results.get("refunds"), list) else []
    found = []
    seen = set()
    for raw in refunds:
        if not isinstance(raw, dict):
            continue
        reason = str(raw.get("refundReason") or raw.get("reason") or "").strip()
        if not reason or reason.lower() in seen:
            continue
        seen.add(reason.lower())
        found.append(reason)
    return found


def cancel_reasons(store) -> dict:
    """Return cancel/refund reasons for the store's marketplace (for the Orders UI)."""
    kind = marketplace_kind(store.marketplace)
    if kind != "lasoo":
        raise MarketplaceError(
            f'Cancel reasons are not available for "{kind or "this marketplace"}" yet.'
        )

    environment = store.lasoo_environment or "staging"
    client = LasooClient(store, environment)

    # Start with Lasoo pre-dispatch (cancellation) reasons, then merge live history.
    ordered = list(LASOO_PRE_DISPATCH_CANCEL_REASONS)
    seen = {r.lower() for r in ordered}
    source = "lasoo_pre_dispatch"
    try:
        for reason in _lasoo_reasons_from_refunds(client):
            if reason.lower() in seen:
                continue
            # Keep "Other" last if present.
            if ordered and ordered[-1].lower() == "other":
                ordered.insert(-1, reason)
            else:
                ordered.append(reason)
            seen.add(reason.lower())
            source = "lasoo_pre_dispatch+refunds_search"
    except Exception:  # noqa: BLE001
        logger.exception("Could not enrich Lasoo cancel reasons from Refunds_Search")

    return {
        "ok": True,
        "marketplace": "lasoo",
        "environment": environment,
        "source": source,
        "reasons": [_reason_option(r) for r in ordered],
    }


def cancel(order: MarketplaceOrder, *, reason: str = "") -> dict:
    """Cancel an order via Lasoo Refunds_Create and mark it cancelled locally.

    Lasoo uses Refunds_Create for pre-dispatch cancellations. The order is always
    marked cancelled in Store Sync so fulfillment can stop; ``marketplace_ok``
    reports whether Lasoo accepted the request.
    """
    _require_lasoo(order.store)

    if order.status in _CANCEL_BLOCKED:
        return {
            "ok": True,
            "marketplace_ok": True,
            "message": f"Order is already {order.status.replace('_', ' ')}.",
        }

    reason_text = (reason or "").strip() or "Seller cancellation"
    client = LasooClient(order.store, order.environment)
    line_items = _refund_line_items(order, reason_text)
    amount = order.total_amount_cents
    if amount is None:
        amount = sum(int(i.get("totalCents") or 0) for i in line_items)

    data = {
        "invoiceId": _invoice_id_for_api(order),
        "refundReason": reason_text,
        "items": line_items,
        "lineItemIds": [i["lineItemId"] for i in line_items],
        "itemsToBeReturned": False,
        "partialRefund": False,
        "refundAmountCents": int(amount or 0),
        "refundPostage": False,
        "returnAddress": "",
        "partialRefundNote": "",
        "notes": reason_text,
    }
    payload = build_payload("refunds_create", data=data, auth=client.auth_key)
    result = client.send("refunds_create", payload)
    marketplace_ok = _lasoo_results_ok(result)

    order.status = OrderStatus.CANCELLED
    raw = order.raw_response_json if isinstance(order.raw_response_json, dict) else {}
    order.raw_response_json = {
        **raw,
        "_local_cancel": {
            "reason": reason_text,
            "marketplace_ok": marketplace_ok,
            "marketplace_message": None if marketplace_ok else _lasoo_error_message(result),
        },
    }
    order.save(update_fields=["status", "raw_response_json", "updated_at"])

    if marketplace_ok:
        return {
            "ok": True,
            "marketplace_ok": True,
            "message": "Order cancelled on Lasoo and marked cancelled here.",
        }

    return {
        "ok": True,
        "marketplace_ok": False,
        "message": (
            "Order marked cancelled here. "
            f"{_lasoo_error_message(result)}"
        ),
    }
