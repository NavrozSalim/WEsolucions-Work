"""Reverb conversation sync for Tickets Management.

Uses production api.reverb.com:
  GET  /api/my/conversations
  GET  /api/my/conversations/{id}
  POST /api/my/conversations/{id}/messages
  PUT  /api/my/conversations/{id}  {"read": true}

Token needs message/conversation scopes (typically read_messages / write_messages).
"""
from __future__ import annotations

import logging
from datetime import datetime

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from store_adapters import get_adapter
from store_adapters.reverb_adapter import ReverbAPIError

from ..errors import MarketplaceError
from ..models import (
    Environment,
    MarketplaceOrder,
    SupportTicket,
    TicketMessage,
    TicketMessageDirection,
    TicketStatus,
)

logger = logging.getLogger("listings.reverb")


def _first(*vals):
    for v in vals:
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        return v
    return None


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


def _dig(obj, *paths):
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


def _conversation_id(raw: dict) -> str:
    return str(
        _first(
            raw.get("id"),
            raw.get("conversation_id"),
            raw.get("uuid"),
            _dig(raw, "_links.self.href"),
        )
        or ""
    ).rstrip("/").split("/")[-1]


def _messages_from(raw: dict) -> list:
    messages = raw.get("messages")
    if isinstance(messages, list):
        return [m for m in messages if isinstance(m, dict)]
    embedded = raw.get("_embedded")
    if isinstance(embedded, dict):
        messages = embedded.get("messages")
        if isinstance(messages, list):
            return [m for m in messages if isinstance(m, dict)]
    return []


def _other_party(raw: dict) -> dict:
    """Buyer / other participant details from a conversation payload.

    Production Reverb payloads use ``other_party`` (name, uuid, shop_id).
    Older / alternate shapes may use ``other_user``, ``buyer``, etc.
    """
    for key in ("other_party", "other_user", "buyer", "recipient", "participant", "user"):
        node = raw.get(key)
        if isinstance(node, dict):
            return node
    members = raw.get("members") or raw.get("participants")
    if isinstance(members, list):
        for m in members:
            if isinstance(m, dict) and not m.get("is_you") and not m.get("self"):
                return m
    return {}


def _message_direction(msg: dict, shop_user_id=None) -> str:
    """Infer inbound vs outbound from Reverb message shape."""
    author = msg.get("author") if isinstance(msg.get("author"), dict) else {}
    sender = msg.get("sender") if isinstance(msg.get("sender"), dict) else {}
    party = author or sender

    # Reverb conversation messages: authored=true means the shop wrote it.
    if msg.get("authored") is True:
        return TicketMessageDirection.OUTBOUND
    if msg.get("authored") is False:
        return TicketMessageDirection.INBOUND

    if msg.get("from_me") is True or msg.get("is_mine") is True or msg.get("mine") is True:
        return TicketMessageDirection.OUTBOUND
    if msg.get("from_me") is False or msg.get("is_mine") is False:
        return TicketMessageDirection.INBOUND

    sender_type = str(
        _first(msg.get("sender_type"), msg.get("sent_by_type"), party.get("type"), "") or ""
    ).lower()
    if sender_type in ("seller", "shop", "store", "me", "self"):
        return TicketMessageDirection.OUTBOUND
    if sender_type in ("buyer", "customer", "user", "other"):
        return TicketMessageDirection.INBOUND

    if shop_user_id is not None:
        aid = _first(party.get("id"), msg.get("author_id"), msg.get("user_id"), msg.get("sender_id"))
        if aid is not None and str(aid) == str(shop_user_id):
            return TicketMessageDirection.OUTBOUND

    # Default: treat as customer inbound so unread counts stay useful.
    return TicketMessageDirection.INBOUND


def _explicit_related_order(raw: dict) -> str:
    related_order = str(
        _first(
            raw.get("order_number"),
            raw.get("order_id"),
            raw.get("orderNumber"),
            raw.get("invoice_number"),
            raw.get("invoiceNumber"),
            _dig(raw, "order.order_number"),
            _dig(raw, "order.id"),
            _dig(raw, "order.number"),
            _dig(raw, "about.order_number"),
            _dig(raw, "about.order_id"),
            (
                str(_dig(raw, "_links.order.href") or "").rstrip("/").split("/")[-1]
                if _dig(raw, "_links.order.href") else None
            ),
            "",
        )
        or ""
    ).strip()[:255]
    if related_order.lower() in ("orders", "selling", "order", "api", "my"):
        return ""
    return related_order


def _order_line_product_ids(order: MarketplaceOrder) -> set[str]:
    ids: set[str] = set()
    items = order.line_items_json if isinstance(order.line_items_json, list) else []
    for it in items:
        if not isinstance(it, dict):
            continue
        for key in ("externalProductKey", "lineItemId"):
            val = str(it.get(key) or "").strip()
            if val:
                ids.add(val)
        raw = it.get("_raw") if isinstance(it.get("_raw"), dict) else {}
        pid = str(raw.get("product_id") or "").strip()
        if pid:
            ids.add(pid)
    rev = None
    envelope = order.raw_response_json if isinstance(order.raw_response_json, dict) else {}
    if isinstance(envelope.get("_reverb"), dict):
        rev = envelope["_reverb"]
    if isinstance(rev, dict):
        pid = str(rev.get("product_id") or "").strip()
        if pid:
            ids.add(pid)
    return ids


def _order_line_skus(order: MarketplaceOrder) -> set[str]:
    skus: set[str] = set()
    items = order.line_items_json if isinstance(order.line_items_json, list) else []
    for it in items:
        if not isinstance(it, dict):
            continue
        for key in ("sku", "externalVariantKey"):
            val = str(it.get(key) or "").strip()
            if val:
                skus.add(val)
    envelope = order.raw_response_json if isinstance(order.raw_response_json, dict) else {}
    rev = envelope.get("_reverb") if isinstance(envelope.get("_reverb"), dict) else {}
    if isinstance(rev, dict):
        sku = str(rev.get("sku") or "").strip()
        if sku:
            skus.add(sku)
    return skus


def _order_customer_name(order: MarketplaceOrder) -> str:
    info = order.customer_info_json if isinstance(order.customer_info_json, dict) else {}
    name = str(info.get("name") or "").strip()
    if name:
        return name
    first = str(info.get("firstName") or info.get("first_name") or "").strip()
    last = str(info.get("lastName") or info.get("last_name") or "").strip()
    return " ".join(p for p in (first, last) if p).strip()


def _resolve_related_order(
    user,
    store,
    raw: dict,
    *,
    customer_name: str = "",
) -> tuple[str, str]:
    """
    Return (invoice_or_order_key, customer_name_from_order).

    Reverb conversations usually omit order_number; they include ``listing.id`` /
    ``listing.sku``. Match those against synced MarketplaceOrder line items,
    preferring buyer-name agreement when multiple orders share a listing.
    """
    explicit = _explicit_related_order(raw)
    if explicit:
        return explicit, ""

    listing = raw.get("listing") if isinstance(raw.get("listing"), dict) else {}
    listing_id = str(listing.get("id") or "").strip()
    listing_sku = str(listing.get("sku") or "").strip()
    want_name = (customer_name or "").strip().lower()

    if not listing_id and not listing_sku and not want_name:
        return "", ""

    candidates: list[tuple[int, MarketplaceOrder]] = []
    qs = MarketplaceOrder.objects.filter(store=store, user=user).only(
        "id",
        "invoice_number",
        "external_order_key",
        "customer_info_json",
        "line_items_json",
        "raw_response_json",
        "created_at",
    )
    for order in qs.iterator():
        pids = _order_line_product_ids(order)
        skus = _order_line_skus(order)
        name = _order_customer_name(order).lower()
        product_hit = bool(listing_id and listing_id in pids)
        sku_hit = bool(listing_sku and listing_sku in skus)
        name_hit = bool(want_name and name and want_name == name)
        if not (product_hit or sku_hit or name_hit):
            continue
        score = 0
        if product_hit:
            score += 40
        if sku_hit:
            score += 20
        if name_hit:
            score += 30
        # Prefer newer orders when scores tie.
        created = order.created_at.timestamp() if order.created_at else 0
        candidates.append((score * 1_000_000_000 + int(created), order))

    if not candidates:
        return "", ""

    best_score_key, best = max(candidates, key=lambda c: c[0])
    best_score = best_score_key // 1_000_000_000

    # Name-only matches are only safe when exactly one order shares that buyer name.
    if best_score == 30:
        same = [o for s, o in candidates if (s // 1_000_000_000) == 30]
        if len(same) != 1:
            return "", ""

    key = str(best.invoice_number or best.external_order_key or "").strip()
    return key[:255], _order_customer_name(best)


def upsert_conversation(user, store, raw: dict, *, shop_user_id=None) -> SupportTicket | None:
    """Upsert one Reverb conversation (+ messages) into SupportTicket."""
    cid = _conversation_id(raw)
    if not cid:
        logger.warning("Skipping Reverb conversation without id store=%s", store.id)
        return None

    other = _other_party(raw)
    subject = str(
        _first(
            raw.get("subject"),
            raw.get("title"),
            _dig(raw, "listing.title"),
            _dig(raw, "listing.name"),
            _dig(raw, "listing.model"),
            "Reverb conversation",
        )
        or "Reverb conversation"
    )[:500]

    customer_name = str(
        _first(
            other.get("name"),
            other.get("display_name"),
            other.get("username"),
            " ".join(
                p for p in (
                    other.get("first_name"),
                    other.get("last_name"),
                ) if p
            ).strip(),
            raw.get("buyer_name"),
        )
        or ""
    )[:255]
    customer_email = str(_first(other.get("email"), other.get("email_address"), "") or "")[:255]
    related_order, order_customer = _resolve_related_order(
        user, store, raw, customer_name=customer_name,
    )
    if not customer_name and order_customer:
        customer_name = order_customer[:255]

    unread_flag = raw.get("unread")
    if unread_flag is None:
        unread_flag = raw.get("has_unread")
    # List payloads use ``read``; treat unread missing + read=false as unread.
    if unread_flag is None and "read" in raw:
        unread_flag = not bool(raw.get("read"))
    status = TicketStatus.OPEN
    if raw.get("closed") or str(raw.get("state") or "").lower() in ("closed", "archived"):
        status = TicketStatus.CLOSED
    elif unread_flag is False and _messages_from(raw):
        # Has messages and no unread — treat as answered/pending.
        status = TicketStatus.ANSWERED

    ticket, created = SupportTicket.objects.update_or_create(
        store=store,
        external_ticket_key=cid,
        environment=Environment.PRODUCTION,
        defaults={
            "user": user,
            "subject": subject,
            "customer_name": customer_name,
            "customer_email": customer_email,
            "related_order_key": related_order,
            "status": status,
            "raw_response_json": raw,
        },
    )

    messages = _messages_from(raw)
    latest = ticket.last_message_at
    latest_customer = ticket.last_customer_message_at
    unread = 0 if created else ticket.unread_count

    for msg in messages:
        msg_key = str(
            _first(msg.get("id"), msg.get("uuid"), msg.get("message_id"))
            or f"{cid}-{hash(str(msg.get('body') or msg.get('message') or msg)) & 0xFFFFFFFF}"
        )
        body = str(_first(msg.get("body"), msg.get("message"), msg.get("text"), "") or "")
        sent_at = (
            _parse_dt(
                _first(
                    msg.get("created_at"),
                    msg.get("sent_at"),
                    msg.get("createdAt"),
                    msg.get("timestamp"),
                )
            )
            or timezone.now()
        )
        direction = _message_direction(msg, shop_user_id=shop_user_id)
        author = msg.get("author") if isinstance(msg.get("author"), dict) else {}
        sender_name = str(
            _first(author.get("name"), author.get("display_name"), msg.get("sender_name"), "") or ""
        )[:255]
        sender_type = "seller" if direction == TicketMessageDirection.OUTBOUND else "customer"

        obj, msg_created = TicketMessage.objects.update_or_create(
            ticket=ticket,
            external_message_key=msg_key,
            defaults={
                "direction": direction,
                "body": body,
                "sender_name": sender_name,
                "sender_type": sender_type,
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
            if msg_created and (msg.get("read") is False or unread_flag):
                unread += 1
            elif msg_created and unread_flag is None:
                unread += 1

    if unread_flag is True and unread == 0:
        unread = 1
    if unread_flag is False:
        unread = 0

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


def fetch(user, store) -> dict:
    """Pull Reverb conversations (with message detail) into SupportTicket."""
    if not (getattr(store, "api_token", None) or "").strip():
        raise MarketplaceError(
            "No Reverb API token configured for this store. Add it in store settings."
        )

    adapter = get_adapter(store)
    if not hasattr(adapter, "iter_conversations"):
        raise MarketplaceError("Store adapter is not Reverb — cannot fetch conversations.")

    # Re-link names/invoices from already-stored conversation JSON (e.g. after
    # orders synced later, or after mapping fixes) before hitting the API.
    for ticket in SupportTicket.objects.filter(store=store, user=user).exclude(
        raw_response_json=None,
    ).iterator():
        raw = ticket.raw_response_json
        if not isinstance(raw, dict):
            continue
        if raw.get("source") == "create_test_ticket":
            continue
        try:
            upsert_conversation(user, store, raw)
        except Exception:
            logger.exception(
                "Reverb ticket re-enrich failed store=%s ticket=%s",
                store.id, ticket.id,
            )

    saved = 0
    try:
        for summary in adapter.iter_conversations():
            cid = _conversation_id(summary)
            detail = summary
            if cid:
                try:
                    full = adapter.get_conversation(cid)
                    if isinstance(full, dict):
                        # Prefer detail (has messages) but keep list-level flags.
                        detail = {**summary, **full}
                except ReverbAPIError as exc:
                    logger.warning(
                        "Reverb conversation detail failed store=%s id=%s: %s",
                        store.id, cid, exc,
                    )
            if upsert_conversation(user, store, detail):
                saved += 1
    except ReverbAPIError as exc:
        status_code = getattr(exc, "status_code", None)
        logger.error(
            "Reverb conversation sync failed store=%s status=%s err=%s",
            store.id, status_code, exc,
        )
        if status_code == 401:
            return {
                "ok": False,
                "marketplace_ok": False,
                "marketplace_supported": True,
                "endpoint": "conversations",
                "message": (
                    "Reverb rejected the API token (401). "
                    "Reconnect with a token that can read conversations/messages."
                ),
                "fetched": 0,
            }
        if status_code == 429:
            return {
                "ok": False,
                "marketplace_ok": False,
                "marketplace_supported": True,
                "endpoint": "conversations",
                "message": "Reverb rate-limited this request. Try again in a minute.",
                "fetched": 0,
            }
        return {
            "ok": False,
            "marketplace_ok": False,
            "marketplace_supported": True,
            "endpoint": "conversations",
            "message": str(exc)[:400] or "Reverb conversation sync failed.",
            "fetched": 0,
        }

    return {
        "ok": True,
        "marketplace_ok": True,
        "marketplace_supported": True,
        "endpoint": "conversations",
        "message": f"Retrieved {saved} conversation(s) from Reverb.",
        "fetched": saved,
    }


def reply(ticket: SupportTicket, *, body: str, sender_name: str = "") -> dict:
    """Post a reply to the Reverb conversation and store it locally."""
    text = (body or "").strip()
    if not text:
        raise MarketplaceError("Reply body is required.")

    store = ticket.store
    if not (getattr(store, "api_token", None) or "").strip():
        raise MarketplaceError("No Reverb API token configured for this store.")

    adapter = get_adapter(store)
    if not hasattr(adapter, "reply_to_conversation"):
        raise MarketplaceError("Store adapter is not Reverb — cannot reply to conversations.")

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
    api_response = None
    try:
        api_response = adapter.reply_to_conversation(ticket.external_ticket_key, text)
        delivered = True
        delivery_message = "Reply sent to the customer via Reverb."
        message.marketplace_request_json = {
            "conversation_id": ticket.external_ticket_key,
            "body": text,
        }
        message.marketplace_response_json = api_response
        try:
            adapter.mark_conversation_read(ticket.external_ticket_key)
        except ReverbAPIError:
            pass
    except ReverbAPIError as exc:
        status_code = getattr(exc, "status_code", None)
        delivery_message = str(exc)[:240]
        message.marketplace_request_json = {
            "conversation_id": ticket.external_ticket_key,
            "body": text,
        }
        message.marketplace_response_json = {
            "error": str(exc),
            "status_code": status_code,
            "body": getattr(exc, "response_body", None),
        }
        if status_code == 401:
            delivery_message = (
                "Reverb rejected the API token (401). "
                "Token needs write access for conversations/messages."
            )

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

    return {
        "ok": True,
        "marketplace_ok": delivered,
        "marketplace_supported": True,
        "endpoint": "conversations/messages",
        "message": delivery_message if delivered else (
            f"Reply saved in Tickets. Marketplace delivery failed: {delivery_message}"
        ),
        "message_id": str(message.id),
        "marketplace_response": api_response,
    }


def create_test_ticket(user, store) -> dict:
    """Local sample ticket for UI testing (Reverb has no create-test-ticket API)."""
    now = timezone.now()
    key = f"reverb-local-test-{int(now.timestamp())}"
    ticket = SupportTicket.objects.create(
        user=user,
        store=store,
        external_ticket_key=key,
        subject="Test Reverb conversation",
        customer_name="Example Buyer",
        customer_email="",
        status=TicketStatus.OPEN,
        unread_count=1,
        last_message_at=now,
        last_customer_message_at=now,
        environment=Environment.PRODUCTION,
        raw_response_json={"source": "create_test_ticket", "marketplace": "reverb"},
    )
    TicketMessage.objects.create(
        ticket=ticket,
        external_message_key=f"{key}-1",
        direction=TicketMessageDirection.INBOUND,
        body="Hi, I have a question about my Reverb order. Can you help?",
        sender_name="Example Buyer",
        sender_type="customer",
        sent_at=now,
        delivered_to_marketplace=True,
    )
    return {
        "ok": True,
        "message": "Test ticket created locally (does not create a real Reverb conversation).",
        "ticket_id": str(ticket.id),
    }
