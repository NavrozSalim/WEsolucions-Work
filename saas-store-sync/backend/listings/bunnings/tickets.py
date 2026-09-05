"""Bunnings / Mirakl inbox threads (M11 list, M12 reply)."""
from __future__ import annotations

import logging
from datetime import datetime

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from ..errors import MarketplaceError
from ..models import (
    SupportTicket,
    TicketMessage,
    TicketMessageDirection,
    TicketStatus,
)
from .client import BunningsClient
from .orders import store_environment

logger = logging.getLogger("listings.bunnings")


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


def _thread_id(raw: dict) -> str:
    return str(raw.get("id") or raw.get("thread_id") or "").strip()


def _extract_threads(payload) -> list[dict]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("data", "threads"):
            val = payload.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
        if payload.get("id") and (payload.get("messages") is not None or payload.get("topic")):
            return [payload]
    return []


def _next_page_token(payload) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(
        payload.get("next_page_token")
        or payload.get("nextPageToken")
        or (payload.get("pagination") or {}).get("next_page_token")
        or ""
    ).strip()


def _order_key(raw: dict) -> str:
    entities = raw.get("entities") or raw.get("entity") or []
    if isinstance(entities, dict):
        entities = [entities]
    if not isinstance(entities, list):
        return ""
    for ent in entities:
        if not isinstance(ent, dict):
            continue
        etype = str(ent.get("type") or ent.get("entity_type") or "").upper()
        eid = str(ent.get("id") or ent.get("entity_id") or "").strip()
        if eid and "ORDER" in etype:
            return eid
    return ""


def _subject(raw: dict) -> str:
    topic = raw.get("topic") if isinstance(raw.get("topic"), dict) else {}
    value = str(topic.get("value") or topic.get("label") or "").strip()
    return value or "Bunnings message"


def _customer_name(raw: dict) -> str:
    for msg in raw.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        sender = msg.get("from") if isinstance(msg.get("from"), dict) else {}
        if str(sender.get("type") or "").upper() == "CUSTOMER":
            return str(sender.get("display_name") or sender.get("name") or "").strip()
    return ""


def _direction(msg: dict) -> str:
    sender = msg.get("from") if isinstance(msg.get("from"), dict) else {}
    stype = str(sender.get("type") or "").upper()
    if stype in ("SHOP", "SELLER", "OPERATOR_TO_SHOP"):
        return TicketMessageDirection.OUTBOUND
    if stype in ("OPERATOR",):
        return TicketMessageDirection.SYSTEM
    return TicketMessageDirection.INBOUND


def _ticket_status(raw: dict) -> str:
    meta = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    if meta.get("shop_reply_needed_since"):
        return TicketStatus.OPEN
    messages = raw.get("messages") or []
    if not messages:
        return TicketStatus.OPEN
    last = messages[-1] if isinstance(messages[-1], dict) else {}
    if _direction(last) == TicketMessageDirection.OUTBOUND:
        return TicketStatus.ANSWERED
    return TicketStatus.PENDING


def upsert_thread(user, store, raw: dict) -> SupportTicket | None:
    if not isinstance(raw, dict):
        return None
    tid = _thread_id(raw)
    if not tid:
        return None
    environment = store_environment(store)
    messages = raw.get("messages") if isinstance(raw.get("messages"), list) else []
    ticket, _created = SupportTicket.objects.update_or_create(
        store=store,
        external_ticket_key=tid,
        environment=environment,
        defaults={
            "user": user,
            "subject": _subject(raw)[:500],
            "customer_name": _customer_name(raw)[:255],
            "related_order_key": _order_key(raw),
            "status": _ticket_status(raw),
            "raw_response_json": raw,
        },
    )
    latest = None
    latest_customer = None
    unread = 0
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        mid = str(msg.get("id") or "").strip()
        body = str(msg.get("body") or "").strip()
        if not mid and not body:
            continue
        if not mid:
            mid = f"{tid}-{hash(body)}"
        direction = _direction(msg)
        sender = msg.get("from") if isinstance(msg.get("from"), dict) else {}
        sent_at = _parse_dt(msg.get("date_created") or msg.get("dateCreated"))
        TicketMessage.objects.update_or_create(
            ticket=ticket,
            external_message_key=mid[:255],
            defaults={
                "direction": direction,
                "body": body,
                "sender_name": str(sender.get("display_name") or sender.get("name") or "").strip(),
                "sender_type": str(sender.get("type") or "").strip().lower(),
                "sent_at": sent_at,
                "delivered_to_marketplace": True,
                "marketplace_response_json": msg,
            },
        )
        if latest is None or (sent_at and sent_at > latest):
            latest = sent_at
        if direction == TicketMessageDirection.INBOUND:
            if latest_customer is None or (sent_at and sent_at > latest_customer):
                latest_customer = sent_at
            unread += 1
    ticket.last_message_at = latest
    ticket.last_customer_message_at = latest_customer
    if ticket.status == TicketStatus.ANSWERED:
        unread = 0
    ticket.unread_count = unread
    ticket.save(
        update_fields=[
            "last_message_at",
            "last_customer_message_at",
            "unread_count",
            "updated_at",
        ]
    )
    return ticket


def fetch(user, store) -> dict:
    try:
        client = BunningsClient(store)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "marketplace_ok": False,
            "marketplace_supported": True,
            "endpoint": "inbox/threads",
            "message": str(exc),
            "fetched": 0,
        }

    saved = 0
    token = ""
    last_error = ""
    for _ in range(20):
        result = client.list_threads(with_messages=True, page_token=token)
        if not result.ok:
            last_error = result.message or "M11 inbox list failed."
            break
        threads = _extract_threads(result.data)
        for raw in threads:
            if upsert_thread(user, store, raw):
                saved += 1
        token = _next_page_token(result.data)
        if not token or not threads:
            last_error = ""
            break

    if saved == 0 and last_error:
        return {
            "ok": False,
            "marketplace_ok": False,
            "marketplace_supported": True,
            "endpoint": "inbox/threads",
            "message": last_error[:400],
            "fetched": 0,
        }
    return {
        "ok": True,
        "marketplace_ok": True,
        "marketplace_supported": True,
        "endpoint": "inbox/threads",
        "message": f"Retrieved {saved} Bunnings message thread(s).",
        "fetched": saved,
    }


def reply(ticket: SupportTicket, *, body: str, sender_name: str = "") -> dict:
    text = (body or "").strip()
    if not text:
        raise MarketplaceError("Reply body is required.")
    store = ticket.store
    thread_id = (ticket.external_ticket_key or "").strip()
    if not thread_id:
        raise MarketplaceError("This ticket has no Bunnings thread id.")
    client = BunningsClient(store)
    result = client.reply_thread(thread_id, text)
    msg = TicketMessage.objects.create(
        ticket=ticket,
        external_message_key="",
        direction=TicketMessageDirection.OUTBOUND,
        body=text,
        sender_name=sender_name or "",
        sender_type="shop",
        sent_at=timezone.now(),
        delivered_to_marketplace=bool(result.ok),
        marketplace_response_json=result.data if result.ok else {"error": result.message},
    )
    ticket.status = TicketStatus.ANSWERED if result.ok else ticket.status
    ticket.last_message_at = msg.sent_at
    ticket.unread_count = 0
    ticket.save(update_fields=["status", "last_message_at", "unread_count", "updated_at"])
    if not result.ok:
        return {
            "ok": False,
            "marketplace_ok": False,
            "message": result.message or "Bunnings did not accept the reply.",
        }
    return {
        "ok": True,
        "marketplace_ok": True,
        "message": "Reply sent to Bunnings.",
    }


def create_test_ticket(user, store) -> dict:
    raise MarketplaceError(
        "Bunnings has no test-ticket API. Use Fetch from marketplace to pull Mirakl inbox threads."
    )
