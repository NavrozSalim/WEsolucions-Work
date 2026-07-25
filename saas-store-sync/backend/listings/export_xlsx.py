"""Excel (.xlsx) exports for managed-store orders and tickets."""
from __future__ import annotations

import io
from datetime import datetime

from openpyxl import Workbook

from .order_service import build_order_details, enrich_order_line_items


def _workbook_response_bytes(wb: Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _is_reverb(store) -> bool:
    code = ""
    try:
        mp = getattr(store, "marketplace", None)
        code = (getattr(mp, "code", None) or "").strip().lower()
    except Exception:
        code = ""
    return code == "reverb"


def _customer_name(details: dict, customer_info) -> str:
    c = details.get("customer") if isinstance(details.get("customer"), dict) else {}
    if not c and isinstance(customer_info, dict):
        c = customer_info
    name = " ".join(
        p for p in (
            c.get("firstName") or c.get("first_name"),
            c.get("lastName") or c.get("last_name"),
        ) if p
    ).strip()
    return name or str(c.get("name") or c.get("email") or "").strip()


def _money_amount(value) -> float | None:
    """Return decimal amount from cents int, float, str, or Reverb money dict."""
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        cents = value.get("amount_cents")
        if cents is not None:
            try:
                return float(cents) / 100.0
            except (TypeError, ValueError):
                pass
        amount = value.get("amount")
        if amount is not None:
            try:
                return float(amount)
            except (TypeError, ValueError):
                return None
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _cents_to_amount(cents) -> float | None:
    if cents is None:
        return None
    try:
        return float(cents) / 100.0
    except (TypeError, ValueError):
        return None


def _format_date_short(value) -> str:
    """M/D/YYYY to match Reverb export style."""
    if not value:
        return ""
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return str(value)[:10]
    return f"{dt.month}/{dt.day}/{dt.year}"


def _format_datetime(value) -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).isoformat(sep=" ", timespec="seconds")
    except ValueError:
        return str(value)


def _reverb_raw(order) -> dict:
    envelope = order.raw_response_json if isinstance(order.raw_response_json, dict) else {}
    rev = envelope.get("_reverb")
    return rev if isinstance(rev, dict) else {}


def _addr_get(addr: dict | None, *keys) -> str:
    if not isinstance(addr, dict):
        return ""
    for key in keys:
        val = addr.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def _address_parts(addr: dict | None) -> dict:
    if not isinstance(addr, dict):
        return {
            "company": "",
            "line1": "",
            "line2": "",
            "city": "",
            "state": "",
            "postcode": "",
            "country": "",
            "full": "",
        }
    company = _addr_get(addr, "company", "companyName")
    line1 = _addr_get(addr, "line1", "address1", "street")
    line2 = _addr_get(addr, "line2", "address2")
    city = _addr_get(addr, "city", "suburb")
    state = _addr_get(addr, "state", "region")
    postcode = _addr_get(addr, "postcode", "postalCode", "zip")
    country = _addr_get(addr, "country", "countryCode")
    city_line = " ".join(p for p in (city, state, postcode) if p)
    full = "\n".join(p for p in (company, line1, line2, city_line, country) if p)
    return {
        "company": company,
        "line1": line1,
        "line2": line2,
        "city": city,
        "state": state,
        "postcode": postcode,
        "country": country,
        "full": full,
    }


def _best_local_shipment(order) -> dict:
    """Latest shipment submitted from this app, if any."""
    try:
        shipments = list(order.shipments.all())
    except Exception:
        shipments = []
    if not shipments:
        return {}
    shipments.sort(key=lambda s: getattr(s, "created_at", None) or datetime.min, reverse=True)
    sh = shipments[0]
    return {
        "carrier": (sh.carrier or "").strip(),
        "tracking_number": (sh.tracking_number or "").strip(),
        "tracking_url": (sh.tracking_url or "").strip(),
        "status": (sh.status or "").strip(),
        "shipped_at": _format_datetime(sh.shipped_at),
    }


def _autosize(ws) -> None:
    for col in ws.columns:
        max_len = 0
        letter = col[0].column_letter
        for cell in col:
            val = "" if cell.value is None else str(cell.value)
            max_len = max(max_len, min(len(val), 60))
        ws.column_dimensions[letter].width = max(12, max_len + 2)


def build_orders_xlsx(orders, store) -> bytes:
    """
    Build a detailed .xlsx for managed-store orders.

    One row per line item, including customer contact, shipping address,
    SKU/source URL, totals, marketplace tracking, and app-submitted shipments.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Orders"
    reverb = _is_reverb(store)
    id_header = "Order ID" if reverb else "Invoice"
    headers = [
        id_header,
        "Order key",
        "Date",
        "Paid at",
        "Local status",
        "Marketplace status",
        "Shipping status",
        "Environment",
        "Buyer Name",
        "Email",
        "Phone",
        "Ship company",
        "Ship address 1",
        "Ship address 2",
        "Ship city",
        "Ship state",
        "Ship postcode",
        "Ship country",
        "Ship address full",
        "Quantity",
        "Title",
        "SKU",
        "Marketplace SKU",
        "Vendor URL",
        "Item price",
        "Line total",
        "Subtotal",
        "Shipping Cost",
        "Tax",
        "Order total",
        "Order Payout",
        "Currency",
        "Carrier (marketplace)",
        "Tracking (marketplace)",
        "Tracking URL (marketplace)",
        "Ship method",
        "Dispatched at",
        "Carrier (submitted)",
        "Tracking (submitted)",
        "Tracking URL (submitted)",
        "Shipment status (submitted)",
        "Shipped at (submitted)",
    ]
    ws.append(headers)

    for order in orders:
        details = build_order_details(
            order.raw_response_json,
            customer_info=order.customer_info_json,
            line_items=order.line_items_json,
            total_cents=order.total_amount_cents,
        )
        try:
            details = enrich_order_line_items(details, store)
        except Exception:
            pass

        rev = _reverb_raw(order)
        totals = details.get("totals") if isinstance(details.get("totals"), dict) else {}
        dates = details.get("dates") if isinstance(details.get("dates"), dict) else {}
        customer = details.get("customer") if isinstance(details.get("customer"), dict) else {}
        marketplace_ship = details.get("shipping") if isinstance(details.get("shipping"), dict) else {}
        ship_addr = _address_parts(
            details.get("shippingAddress")
            or marketplace_ship.get("address")
        )
        local_ship = _best_local_shipment(order)

        order_id = order.invoice_number or order.external_order_key or ""
        order_key = order.external_order_key or ""
        ordered_at = dates.get("orderedAt") or order.created_at
        if not ordered_at and rev.get("created_at"):
            ordered_at = rev.get("created_at")
        date_str = _format_date_short(ordered_at)
        paid_str = _format_date_short(dates.get("paidAt"))
        buyer = _customer_name(details, order.customer_info_json)
        email = str(customer.get("email") or "").strip()
        phone = str(customer.get("phone") or "").strip()

        shipping_cost = _money_amount(rev.get("shipping"))
        if shipping_cost is None:
            shipping_cost = _cents_to_amount(totals.get("shippingCents"))

        product_price = _money_amount(
            rev.get("amount_product")
            or rev.get("amount_product_subtotal")
        )
        if product_price is None:
            product_price = _cents_to_amount(totals.get("subtotalCents"))

        tax = _cents_to_amount(totals.get("taxCents"))
        order_total = _cents_to_amount(totals.get("totalCents"))
        if order_total is None:
            order_total = _cents_to_amount(order.total_amount_cents)

        payout = _money_amount(
            rev.get("direct_checkout_payout")
            or rev.get("payout")
            or rev.get("order_payout")
            or rev.get("amount_payout")
        )
        currency = str(totals.get("currency") or ("USD" if reverb else "AUD"))

        items = details.get("lineItems") if isinstance(details.get("lineItems"), list) else None
        if not items and isinstance(order.line_items_json, list):
            items = order.line_items_json
        if not items:
            items = [{}]

        marketplace_status = ""
        if isinstance(details.get("marketplaceStatus"), str):
            marketplace_status = details["marketplaceStatus"]
        elif rev.get("status"):
            marketplace_status = str(rev.get("status"))

        mp_carrier = str(marketplace_ship.get("carrier") or "").strip()
        mp_tracking = str(
            marketplace_ship.get("trackingNumber")
            or marketplace_ship.get("tracking_number")
            or ""
        ).strip()
        mp_tracking_url = str(
            marketplace_ship.get("trackingUrl")
            or marketplace_ship.get("tracking_url")
            or ""
        ).strip()
        mp_method = str(marketplace_ship.get("method") or "").strip()
        mp_ship_status = str(marketplace_ship.get("status") or "").strip()
        mp_dispatched = _format_datetime(marketplace_ship.get("dispatchedAt"))

        for it in items:
            if not isinstance(it, dict):
                it = {}
            title = str(it.get("title") or it.get("name") or rev.get("title") or "").strip()
            qty = it.get("quantity") if it.get("quantity") is not None else it.get("qty")
            if qty is None:
                qty = rev.get("quantity")
            try:
                qty = int(qty) if qty is not None else 1
            except (TypeError, ValueError):
                qty = 1

            sku = str(it.get("sku") or "").strip()
            marketplace_sku = str(
                it.get("marketplaceSku")
                or it.get("externalVariantKey")
                or it.get("variantKey")
                or sku
                or ""
            ).strip()
            vendor_url = str(it.get("vendorUrl") or it.get("vendor_url") or "").strip()

            item_price = _cents_to_amount(it.get("priceCents"))
            row_product_price = item_price if item_price is not None else product_price
            line_total = None
            if row_product_price is not None:
                line_total = round(float(row_product_price) * qty, 2)

            ws.append([
                order_id,
                order_key,
                date_str,
                paid_str,
                order.status or "",
                marketplace_status or "",
                mp_ship_status or order.shipping_status or "",
                order.environment or "",
                buyer,
                email,
                phone,
                ship_addr["company"],
                ship_addr["line1"],
                ship_addr["line2"],
                ship_addr["city"],
                ship_addr["state"],
                ship_addr["postcode"],
                ship_addr["country"],
                ship_addr["full"],
                qty,
                title,
                sku,
                marketplace_sku,
                vendor_url,
                row_product_price if row_product_price is not None else "",
                line_total if line_total is not None else "",
                product_price if product_price is not None else "",
                shipping_cost if shipping_cost is not None else 0,
                tax if tax is not None else "",
                order_total if order_total is not None else "",
                payout if payout is not None else "",
                currency,
                mp_carrier,
                mp_tracking,
                mp_tracking_url,
                mp_method,
                mp_dispatched,
                local_ship.get("carrier") or "",
                local_ship.get("tracking_number") or "",
                local_ship.get("tracking_url") or "",
                local_ship.get("status") or "",
                local_ship.get("shipped_at") or "",
            ])

    _autosize(ws)
    return _workbook_response_bytes(wb)


def build_tickets_xlsx(tickets, *, order_id_label: str = "Invoice") -> bytes:
    """Build an .xlsx workbook for SupportTicket queryset/list."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Tickets"
    headers = [
        "Customer",
        "Email",
        "Subject",
        order_id_label,
        "Status",
        "Unread",
        "Last message",
        "Last customer message",
        "Environment",
        "External key",
        "Created at",
    ]
    ws.append(headers)

    for ticket in tickets:
        last_msg = ticket.last_message_at
        last_cust = ticket.last_customer_message_at
        created = ticket.created_at
        if isinstance(last_msg, datetime):
            last_msg = last_msg.isoformat()
        if isinstance(last_cust, datetime):
            last_cust = last_cust.isoformat()
        if isinstance(created, datetime):
            created = created.isoformat()
        ws.append([
            ticket.customer_name or "",
            ticket.customer_email or "",
            ticket.subject or "",
            ticket.related_order_key or "",
            ticket.status or "",
            ticket.unread_count or 0,
            str(last_msg or ""),
            str(last_cust or ""),
            ticket.environment or "",
            ticket.external_ticket_key or "",
            str(created or ""),
        ])

    _autosize(ws)
    return _workbook_response_bytes(wb)
