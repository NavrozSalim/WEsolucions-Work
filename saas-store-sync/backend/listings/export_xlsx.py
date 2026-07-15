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


def _reverb_raw(order) -> dict:
    envelope = order.raw_response_json if isinstance(order.raw_response_json, dict) else {}
    rev = envelope.get("_reverb")
    return rev if isinstance(rev, dict) else {}


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
    Build an .xlsx matching the seller's highlighted Reverb-style columns:
    Order ID, Date, Quantity, Title, Buyer Name, Shipping Cost, Product Price,
    Order Payout, Status.

    One row per line item (Quantity / Title per item).
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Orders"
    reverb = _is_reverb(store)
    id_header = "Order ID" if reverb else "Invoice"
    headers = [
        id_header,
        "Date",
        "Quantity",
        "Title",
        "Buyer Name",
        "Shipping Cost",
        "Product Price",
        "Order Payout",
        "Status",
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

        order_id = order.invoice_number or order.external_order_key or ""
        ordered_at = dates.get("orderedAt") or order.created_at
        if not ordered_at and rev.get("created_at"):
            ordered_at = rev.get("created_at")
        date_str = _format_date_short(ordered_at)
        buyer = _customer_name(details, order.customer_info_json)

        shipping_cost = _money_amount(rev.get("shipping"))
        if shipping_cost is None:
            shipping_cost = _cents_to_amount(totals.get("shippingCents"))

        product_price = _money_amount(
            rev.get("amount_product")
            or rev.get("amount_product_subtotal")
        )
        if product_price is None:
            product_price = _cents_to_amount(totals.get("subtotalCents"))

        payout = _money_amount(
            rev.get("direct_checkout_payout")
            or rev.get("payout")
            or rev.get("order_payout")
            or rev.get("amount_payout")
        )

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
        status_out = marketplace_status or order.status or ""

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

            item_price = _cents_to_amount(it.get("priceCents"))
            row_product_price = item_price if item_price is not None else product_price

            ws.append([
                order_id,
                date_str,
                qty,
                title,
                buyer,
                shipping_cost if shipping_cost is not None else 0,
                row_product_price if row_product_price is not None else "",
                payout if payout is not None else "",
                status_out,
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
