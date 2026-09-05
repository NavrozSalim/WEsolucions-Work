"""Customer support tickets: fetch from marketplace, store locally, reply.

Supports:
- Lasoo Connect (when Tickets/Messages queries exist)
- Reverb conversations (GET /api/my/conversations + reply)
- Bunnings / Mirakl inbox (M11 list threads, M12 reply)
"""
from __future__ import annotations

import logging
from datetime import datetime

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from stores.credentials import marketplace_kind

from .errors import MarketplaceError
from .lasoo.client import LasooClient
from .lasoo.queries import build_payload
from .models import (
    SupportTicket,
    TicketMessage,
    TicketMessageDirection,
    TicketStatus,
)

logger = logging.getLogger("listings")

# Tried in order until one exists / returns data.
_TICKET_SEARCH_KEYS = ("tickets_search", "messages_search", "chat_messages_search")
_TICKET_REPLY_KEYS = ("tickets_reply", "messages_create", "chat_messages_create")


def _require_lasoo(store):
    kind = marketplace_kind(store.marketplace)
    if kind != "lasoo":
        raise MarketplaceError(
            f'Ticket management is not supported yet for "{kind or "this marketplace"}". '
            "Currently Lasoo, Reverb, and Bunnings managed stores are supported."
        )


def fetch(user, store, page: int = 1, take: int = 50) -> dict:
    """Pull tickets/conversations from the store's marketplace and upsert locally."""
    kind = marketplace_kind(store.marketplace)
    if kind == "reverb":
        from .reverb import tickets as reverb_tickets
        return reverb_tickets.fetch(user, store)
    if kind == "bunnings":
        from .bunnings import tickets as bunnings_tickets
        return bunnings_tickets.fetch(user, store)
    if kind != "lasoo":
        raise MarketplaceError(
            f'Ticket management is not supported yet for "{kind or "this marketplace"}". '
            "Currently Lasoo, Reverb, and Bunnings managed stores are supported."
        )
    return _fetch_lasoo(user, store, page=page, take=take)


def reply(ticket: SupportTicket, *, body: str, sender_name: str = "") -> dict:
    """Store an outbound reply and attempt delivery to the marketplace/customer."""
    kind = marketplace_kind(ticket.store.marketplace)
    if kind == "reverb":
        from .reverb import tickets as reverb_tickets
        return reverb_tickets.reply(ticket, body=body, sender_name=sender_name)
    if kind == "bunnings":
        from .bunnings import tickets as bunnings_tickets
        return bunnings_tickets.reply(ticket, body=body, sender_name=sender_name)
    if kind != "lasoo":
        raise MarketplaceError(
            f'Ticket replies are not supported yet for "{kind or "this marketplace"}".'
        )
    return _reply_lasoo(ticket, body=body, sender_name=sender_name)


def create_test_ticket(user, store) -> dict:
    """Create a local sample inbound ticket so the UI can be exercised."""
    kind = marketplace_kind(store.marketplace)
    if kind == "reverb":
        from .reverb import tickets as reverb_tickets
        return reverb_tickets.create_test_ticket(user, store)
    if kind == "bunnings":
        from .bunnings import tickets as bunnings_tickets
        return bunnings_tickets.create_test_ticket(user, store)
    _require_lasoo(store)
    return _create_test_ticket_lasoo(user, store)


def _query_missing(result) -> bool:
    text = " ".join(
        str(x or "")
        for x in (
            getattr(result, "message", None),
            getattr(result, "error", None),
            (result.data or {}) if isinstance(getattr(result, "data", None), dict) else {},
        )
    ).lower()
    if isinstance(result.data, dict):
        text += " " + str(result.data.get("error") or "").lower()
        text += " " + str(result.data.get("message") or "").lower()
        for dm in result.data.get("devMessages") or []:
            text += " " + str(dm).lower()
    return "query does not exist" in text or "does not exist" in text


def _lasoo_nested_ok(result) -> bool:
    if not result.ok:
        return False
    data = result.data if isinstance(result.data, dict) else {}
    results = data.get("results")
    if isinstance(results, dict) and results.get("success") is False:
        return False
    return True


def _parse_dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        dt = parse_datetime(str(value).replace("Z", "+00:00"))
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_default_timezone())
    return dt


def _first(*vals):
    for v in vals:
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        return v
    return None


def _extract_list(data, *keys) -> list:
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in keys:
        val = data.get(key)
        if isinstance(val, list):
            return val
        if isinstance(val, dict):
            nested = _extract_list(val, *keys)
            if nested:
                return nested
    results = data.get("results")
    if isinstance(results, dict):
        return _extract_list(results, *keys)
    return []


def _fetch_lasoo(user, store, page: int = 1, take: int = 50) -> dict:
    """Pull tickets/messages from Lasoo and upsert them locally."""
    environment = store.lasoo_environment or "staging"
    client = LasooClient(store, environment)

    last_error = ""
    used_endpoint = None
    raw_items = []

    for endpoint_key in _TICKET_SEARCH_KEYS:
        payload = build_payload(
            endpoint_key,
            data={
                "page": page,
                "take": take,
                "includeMessages": True,
                "includeCustomer": True,
            },
            auth=client.auth_key,
        )
        result = client.send(endpoint_key, payload)
        if _query_missing(result):
            last_error = result.message or "Query does not exist"
            continue
        used_endpoint = endpoint_key
        if not _lasoo_nested_ok(result):
            data = result.data if isinstance(result.data, dict) else {}
            results = data.get("results") if isinstance(data.get("results"), dict) else {}
            last_error = (
                str(results.get("message") or results.get("error") or result.message or "Ticket fetch failed.")
            )
            return {
                "ok": False,
                "marketplace_ok": False,
                "marketplace_supported": True,
                "endpoint": endpoint_key,
                "message": last_error[:400],
                "fetched": 0,
            }
        raw_items = _extract_list(
            result.data,
            "tickets",
            "messages",
            "conversations",
            "threads",
            "items",
            "records",
            "chatMessages",
        )
        break
    else:
        return {
            "ok": True,
            "marketplace_ok": False,
            "marketplace_supported": False,
            "endpoint": None,
            "message": (
                "Lasoo Connect does not expose a Tickets/Messages API yet "
                f"({last_error or 'no matching query'}). "
                "Hourly sync is armed and will import tickets when Lasoo enables the endpoint."
            ),
            "fetched": 0,
        }

    saved = 0
    for raw in raw_items:
        if isinstance(raw, dict):
            _upsert_ticket(user, store, environment, raw)
            saved += 1

    return {
        "ok": True,
        "marketplace_ok": True,
        "marketplace_supported": True,
        "endpoint": used_endpoint,
        "message": f"Retrieved {saved} ticket(s) from {environment}.",
        "fetched": saved,
    }


def _reply_lasoo(ticket: SupportTicket, *, body: str, sender_name: str = "") -> dict:
    """Store an outbound reply and attempt delivery via Lasoo."""
    text = (body or "").strip()
    if not text:
        raise MarketplaceError("Reply body is required.")

    client = LasooClient(ticket.store, ticket.environment)
    now = timezone.now()

    message = TicketMessage.objects.create(
        ticket=ticket,
        direction=TicketMessageDirection.OUTBOUND,
        body=text,
        sender_name=(sender_name or "").strip() or "Seller",
        sender_type="seller",
        sent_at=now,
        delivered_to_marketplace=False,
    )

    delivered = False
    delivery_message = ""
    used_endpoint = None
    api_response = None

    for endpoint_key in _TICKET_REPLY_KEYS:
        data = {
            "ticketId": _external_id(ticket.external_ticket_key),
            "messageId": ticket.external_ticket_key,
            "conversationId": ticket.external_ticket_key,
            "invoiceId": _external_id(ticket.related_order_key) if ticket.related_order_key else None,
            "message": text,
            "body": text,
            "note": text,
        }
        # Drop nulls — some Lasoo handlers crash on explicit nulls.
        data = {k: v for k, v in data.items() if v is not None}
        payload = build_payload(endpoint_key, data=data, auth=client.auth_key)
        result = client.send(endpoint_key, payload)
        if _query_missing(result):
            delivery_message = result.message or "Query does not exist"
            continue
        used_endpoint = endpoint_key
        api_response = result.data
        message.marketplace_request_json = {**payload, "auth": "***"}
        message.marketplace_response_json = result.data if result.ok else result.error
        if _lasoo_nested_ok(result):
            delivered = True
            delivery_message = result.message or "Reply delivered to marketplace."
            break
        results = (result.data or {}).get("results") if isinstance(result.data, dict) else {}
        delivery_message = str(
            (results or {}).get("message")
            or (results or {}).get("error")
            or result.message
            or "Marketplace rejected the reply."
        )
        break

    message.delivered_to_marketplace = delivered
    message.save(
        update_fields=[
            "delivered_to_marketplace",
            "marketplace_request_json",
            "marketplace_response_json",
        ]
    )

    ticket.status = TicketStatus.ANSWERED
    ticket.last_message_at = now
    ticket.unread_count = 0
    ticket.save(update_fields=["status", "last_message_at", "unread_count", "updated_at"])

    if delivered:
        return {
            "ok": True,
            "marketplace_ok": True,
            "marketplace_supported": True,
            "endpoint": used_endpoint,
            "message": "Reply sent to the customer via the marketplace.",
            "message_id": str(message.id),
        }

    return {
        "ok": True,
        "marketplace_ok": False,
        "marketplace_supported": used_endpoint is not None,
        "endpoint": used_endpoint,
        "message": (
            "Reply saved in Tickets. "
            + (
                f"Marketplace delivery failed: {delivery_message[:240]}"
                if used_endpoint
                else (
                    "Lasoo Connect does not expose a ticket reply API yet, "
                    "so the customer was not notified through the marketplace. "
                    "Reply will sync when messaging endpoints are enabled."
                )
            )
        ),
        "message_id": str(message.id),
        "marketplace_response": api_response,
    }


def _create_test_ticket_lasoo(user, store) -> dict:
    """Create a local sample inbound ticket so the UI can be exercised in staging."""
    environment = store.lasoo_environment or "staging"
    now = timezone.now()
    key = f"local-test-{int(now.timestamp())}"
    ticket = SupportTicket.objects.create(
        user=user,
        store=store,
        external_ticket_key=key,
        subject="Test customer enquiry",
        customer_name="Jane Customer",
        customer_email="jane.customer@example.com",
        status=TicketStatus.OPEN,
        unread_count=1,
        last_message_at=now,
        last_customer_message_at=now,
        environment=environment,
        raw_response_json={"source": "create_test_ticket"},
    )
    TicketMessage.objects.create(
        ticket=ticket,
        external_message_key=f"{key}-1",
        direction=TicketMessageDirection.INBOUND,
        body="Hi, I have a question about my recent order. Can you help?",
        sender_name="Jane Customer",
        sender_type="customer",
        sent_at=now,
        delivered_to_marketplace=True,
    )
    return {
        "ok": True,
        "message": "Test ticket created locally (marketplace has no CreateTestTicket API).",
        "ticket_id": str(ticket.id),
    }


def _external_id(value):
    text = str(value or "").strip()
    if text.isdigit():
        return int(text)
    return text or None


def _upsert_ticket(user, store, environment: str, raw: dict) -> SupportTicket:
    ticket_key = str(
        _first(
            raw.get("id"),
            raw.get("ticketId"),
            raw.get("conversationId"),
            raw.get("threadId"),
            raw.get("externalTicketKey"),
            raw.get("key"),
        )
        or ""
    )
    if not ticket_key:
        ticket_key = f"anon-{timezone.now().timestamp()}"

    customer = raw.get("customer") if isinstance(raw.get("customer"), dict) else {}
    subject = str(
        _first(raw.get("subject"), raw.get("title"), raw.get("topic"), "Customer message") or "Customer message"
    )
    status_raw = _first(raw.get("status"), raw.get("state"))
    status = _map_ticket_status(status_raw)

    defaults = {
        "user": user,
        "subject": subject[:500],
        "customer_name": str(
            _first(
                raw.get("customerName"),
                customer.get("name"),
                " ".join(
                    [
                        str(customer.get("firstName") or customer.get("first_name") or ""),
                        str(customer.get("surname") or customer.get("lastName") or customer.get("last_name") or ""),
                    ]
                ).strip(),
            )
            or ""
        )[:255],
        "customer_email": str(
            _first(raw.get("customerEmail"), customer.get("email"), customer.get("emailAddress")) or ""
        )[:255],
        "related_order_key": str(
            _first(raw.get("invoiceId"), raw.get("orderId"), raw.get("externalOrderKey"), "") or ""
        )[:255],
        "status": status,
        "raw_response_json": raw,
    }

    ticket, created = SupportTicket.objects.update_or_create(
        store=store,
        external_ticket_key=ticket_key,
        environment=environment,
        defaults=defaults,
    )

    messages = _extract_list(raw, "messages", "items", "replies", "chatMessages")
    if not messages and _first(raw.get("message"), raw.get("body"), raw.get("text")):
        messages = [raw]

    latest = ticket.last_message_at
    latest_customer = ticket.last_customer_message_at
    unread = ticket.unread_count if not created else 0

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        msg_key = str(
            _first(msg.get("id"), msg.get("messageId"), msg.get("externalMessageKey"))
            or f"{ticket_key}-{hash(str(msg.get('body') or msg.get('message') or msg)) & 0xFFFFFFFF}"
        )
        body = str(_first(msg.get("body"), msg.get("message"), msg.get("text"), "") or "")
        sent_at = _parse_dt(_first(msg.get("createdAt"), msg.get("sentAt"), msg.get("created_at"))) or timezone.now()
        sender_type = str(_first(msg.get("sentByType"), msg.get("senderType"), msg.get("from"), "") or "").lower()
        direction = TicketMessageDirection.INBOUND
        if sender_type in ("seller", "retailer", "admin", "operator", "staff"):
            direction = TicketMessageDirection.OUTBOUND
        elif sender_type in ("system",):
            direction = TicketMessageDirection.SYSTEM

        obj, msg_created = TicketMessage.objects.update_or_create(
            ticket=ticket,
            external_message_key=msg_key,
            defaults={
                "direction": direction,
                "body": body,
                "sender_name": str(_first(msg.get("senderName"), msg.get("fromName"), "") or "")[:255],
                "sender_type": sender_type[:50],
                "sent_at": sent_at,
                "delivered_to_marketplace": True,
                "marketplace_response_json": msg,
            },
        )
        if latest is None or (obj.sent_at and obj.sent_at > latest):
            latest = obj.sent_at
        if direction == TicketMessageDirection.INBOUND:
            if latest_customer is None or (obj.sent_at and obj.sent_at > latest_customer):
                latest_customer = obj.sent_at
            if msg_created:
                unread += 1

    ticket.last_message_at = latest
    ticket.last_customer_message_at = latest_customer
    ticket.unread_count = unread
    if unread and ticket.status == TicketStatus.ANSWERED:
        ticket.status = TicketStatus.OPEN
    ticket.save(
        update_fields=[
            "last_message_at",
            "last_customer_message_at",
            "unread_count",
            "status",
            "updated_at",
        ]
    )
    return ticket


def _map_ticket_status(raw) -> str:
    if not raw:
        return TicketStatus.OPEN
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    text = str(raw).strip().lower().replace(" ", "_").replace("-", "_")
    mapping = {
        "open": TicketStatus.OPEN,
        "new": TicketStatus.OPEN,
        "pending": TicketStatus.PENDING,
        "waiting": TicketStatus.PENDING,
        "answered": TicketStatus.ANSWERED,
        "replied": TicketStatus.ANSWERED,
        "closed": TicketStatus.CLOSED,
        "resolved": TicketStatus.CLOSED,
        "done": TicketStatus.CLOSED,
    }
    return mapping.get(text, TicketStatus.OPEN)
