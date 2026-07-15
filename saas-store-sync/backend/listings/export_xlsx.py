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


def _format_address(addr) -> str:
    if not isinstance(addr, dict):
        return ""
    parts = [
        addr.get("name") or addr.get("fullName"),
        addr.get("line1") or addr.get("street") or addr.get("address1"),
        addr.get("line2") or addr.get("address2"),
        ", ".join(
            p for p in (
                addr.get("city"),
                addr.get("state") or addr.get("region"),
                addr.get("postcode") or addr.get("postal_code") or addr.get("zip"),
            ) if p
        ),
        addr.get("country") or addr.get("country_code"),
    ]
    return ", ".join(str(p).strip() for p in parts if p and str(p).strip())


def _items_text(details: dict, line_items_json) -> str:
    items = details.get("lineItems") if isinstance(details.get("lineItems"), list) else None
    if not items and isinstance(line_items_json, list):
        items = line_items_json
    if not items:
        return ""
    parts = []
    for it in items:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or it.get("name") or "").strip()
        sku = str(it.get("sku") or it.get("externalVariantKey") or "").strip()
        qty = it.get("quantity") if it.get("quantity") is not None else it.get("qty")
        try:
            qty = int(qty) if qty is not None else 1
        except (TypeError, ValueError):
            qty = 1
        bit = title or sku or "item"
        if sku and title:
            bit = f"{title} ({sku})"
        elif sku and not title:
            bit = sku
        parts.append(f"{qty}× {bit}")
    return "; ".join(parts)


def build_orders_xlsx(orders, store) -> bytes:
    """Build an .xlsx workbook for a list of MarketplaceOrder instances."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Orders"
    headers = [
        "Invoice",
        "Order key",
        "Customer",
        "Email",
        "Phone",
        "Items",
        "Total",
        "Currency",
        "Status",
        "Shipping status",
        "Ordered at",
        "Shipping address",
        "Environment",
        "Related tickets",
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
        customer = details.get("customer") if isinstance(details.get("customer"), dict) else {}
        if not customer and isinstance(order.customer_info_json, dict):
            customer = order.customer_info_json
        totals = details.get("totals") if isinstance(details.get("totals"), dict) else {}
        dates = details.get("dates") if isinstance(details.get("dates"), dict) else {}
        shipping = details.get("shipping") if isinstance(details.get("shipping"), dict) else {}
        addr = details.get("shippingAddress") or shipping.get("address")

        total_cents = totals.get("totalCents")
        if total_cents is None:
            total_cents = order.total_amount_cents
        total_display = ""
        if total_cents is not None:
            try:
                total_display = f"{float(total_cents) / 100:.2f}"
            except (TypeError, ValueError):
                total_display = str(total_cents)

        ticket_keys = []
        # related tickets may be attached via serializer context; fall back to blank
        related = getattr(order, "_related_tickets_export", None)
        if isinstance(related, list):
            for t in related:
                if isinstance(t, dict):
                    ticket_keys.append(str(t.get("subject") or t.get("id") or "").strip())
                else:
                    ticket_keys.append(str(t))

        ordered_at = dates.get("orderedAt") or order.created_at
        if isinstance(ordered_at, datetime):
            ordered_at = ordered_at.isoformat()

        ws.append([
            order.invoice_number or "",
            order.external_order_key or "",
            _customer_name(details, order.customer_info_json),
            str(customer.get("email") or ""),
            str(customer.get("phone") or ""),
            _items_text(details, order.line_items_json),
            total_display,
            str(totals.get("currency") or ""),
            order.status or "",
            order.shipping_status or "",
            str(ordered_at or ""),
            _format_address(addr) if isinstance(addr, dict) else "",
            order.environment or "",
            "; ".join(p for p in ticket_keys if p),
        ])

    for col in ws.columns:
        max_len = 0
        letter = col[0].column_letter
        for cell in col:
            val = "" if cell.value is None else str(cell.value)
            max_len = max(max_len, min(len(val), 60))
        ws.column_dimensions[letter].width = max(12, max_len + 2)

    return _workbook_response_bytes(wb)


def build_tickets_xlsx(tickets) -> bytes:
    """Build an .xlsx workbook for SupportTicket queryset/list."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Tickets"
    headers = [
        "Customer",
        "Email",
        "Subject",
        "Invoice",
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

    for col in ws.columns:
        max_len = 0
        letter = col[0].column_letter
        for cell in col:
            val = "" if cell.value is None else str(cell.value)
            max_len = max(max_len, min(len(val), 60))
        ws.column_dimensions[letter].width = max(12, max_len + 2)

    return _workbook_response_bytes(wb)
