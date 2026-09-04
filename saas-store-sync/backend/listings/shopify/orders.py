"""Create and update Shopify Admin orders for marketplace orders.

Create on first fetch (or retry after a failed create). Later fetch / ship /
cancel updates the existing Shopify order instead of duplicating it.

Phone numbers are converted to E.164; invalid/sandbox phones are omitted so
Shopify ``orderCreate`` does not fail with "Order Phone is invalid".
"""
from __future__ import annotations

import logging
import re
from decimal import Decimal, ROUND_HALF_UP

from django.utils import timezone

from stores.credentials import marketplace_kind

from .client import (
    ShopifyError,
    graphql,
    location_gid,
    normalize_shop_domain,
    numeric_id_from_gid,
    shop_handle,
    store_shopify_ready,
)

logger = logging.getLogger("listings.shopify")

SHOPIFY_ORDER_MARKETPLACES = ("reverb", "lasoo", "mydeal", "etsy", "bunnings")

FIND_ORDER_QUERY = """
query FindMarketplaceOrder($query: String!) {
  orders(first: 5, query: $query) {
    nodes {
      id
      name
      tags
      customAttributes { key value }
    }
  }
}
"""

VARIANT_BY_SKU_QUERY = """
query VariantBySku($query: String!) {
  productVariants(first: 1, query: $query) {
    nodes {
      id
      sku
    }
  }
}
"""

ORDER_CREATE_MUTATION = """
mutation OrderCreate($order: OrderCreateOrderInput!, $options: OrderCreateOptionsInput) {
  orderCreate(order: $order, options: $options) {
    order {
      id
      name
    }
    userErrors {
      field
      message
    }
  }
}
"""

GET_ORDER_QUERY = """
query ShopifyOrderMirror($id: ID!) {
  order(id: $id) {
    id
    name
    tags
    customAttributes { key value }
    shippingLines(first: 5) { nodes { title } }
  }
}
"""

ORDER_UPDATE_MUTATION = """
mutation OrderUpdate($input: OrderInput!) {
  orderUpdate(input: $input) {
    order {
      id
      name
      tags
    }
    userErrors {
      field
      message
    }
  }
}
"""

TAGS_REMOVE_MUTATION = """
mutation TagsRemove($id: ID!, $tags: [String!]!) {
  tagsRemove(id: $id, tags: $tags) {
    node { id }
    userErrors { field message }
  }
}
"""

TAGS_ADD_MUTATION = """
mutation TagsAdd($id: ID!, $tags: [String!]!) {
  tagsAdd(id: $id, tags: $tags) {
    node { id }
    userErrors { field message }
  }
}
"""

ORDER_EDIT_BEGIN_MUTATION = """
mutation OrderEditBegin($id: ID!) {
  orderEditBegin(id: $id) {
    calculatedOrder { id }
    userErrors { field message }
  }
}
"""

ORDER_EDIT_ADD_SHIPPING_MUTATION = """
mutation OrderEditAddShippingLine($id: ID!, $shippingLine: OrderEditAddShippingLineInput!) {
  orderEditAddShippingLine(id: $id, shippingLine: $shippingLine) {
    calculatedOrder { id }
    userErrors { field message }
  }
}
"""

ORDER_EDIT_COMMIT_MUTATION = """
mutation OrderEditCommit($id: ID!, $notifyCustomer: Boolean) {
  orderEditCommit(id: $id, notifyCustomer: $notifyCustomer) {
    order { id name }
    userErrors { field message }
  }
}
"""

ORDER_FULFILLMENT_ORDERS_QUERY = """
query OrderFulfillmentOrders($id: ID!) {
  order(id: $id) {
    id
    fulfillmentOrders(first: 20) {
      nodes {
        id
        status
        lineItems(first: 50) {
          nodes {
            id
            remainingQuantity
          }
        }
      }
    }
  }
}
"""

FULFILLMENT_CREATE_MUTATION = """
mutation FulfillmentCreate($fulfillment: FulfillmentInput!) {
  fulfillmentCreate(fulfillment: $fulfillment) {
    fulfillment { id status }
    userErrors { field message }
  }
}
"""

ORDER_CANCEL_MUTATION = """
mutation OrderCancel(
  $orderId: ID!,
  $reason: OrderCancelReason!,
  $refund: Boolean!,
  $restock: Boolean!,
  $notifyCustomer: Boolean
) {
  orderCancel(
    orderId: $orderId,
    reason: $reason,
    refund: $refund,
    restock: $restock,
    notifyCustomer: $notifyCustomer
  ) {
    job { id done }
    orderCancelUserErrors { field message code }
  }
}
"""

_COUNTRY_ALIASES = {
    "australia": "AU",
    "au": "AU",
    "united states": "US",
    "usa": "US",
    "us": "US",
    "united kingdom": "GB",
    "uk": "GB",
    "gb": "GB",
    "new zealand": "NZ",
    "nz": "NZ",
    "canada": "CA",
    "ca": "CA",
}

# ISO 3166-1 alpha-2 → ITU country calling code (no plus).
_COUNTRY_DIAL = {
    "AU": "61",
    "US": "1",
    "CA": "1",
    "GB": "44",
    "NZ": "64",
}

def marketplace_tag(kind: str) -> str:
    code = re.sub(r"[^a-z0-9]+", "-", (kind or "").strip().lower()).strip("-")
    return code or "marketplace"


def tracking_tag(kind: str, order_key: str) -> str:
    key = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(order_key or "").strip()).strip("-")[:80]
    return f"sp-{marketplace_tag(kind)}-{key}"[:255]


def admin_order_url(store, order) -> str | None:
    gid = (getattr(order, "shopify_order_gid", None) or "").strip()
    numeric = (getattr(order, "shopify_order_id", None) or "").strip() or numeric_id_from_gid(gid)
    handle = shop_handle(getattr(store, "shopify_shop_domain", "") or "")
    if not numeric or not handle:
        return None
    return f"https://admin.shopify.com/store/{handle}/orders/{numeric}"


def normalize_shopify_phone(raw, country_code: str = "") -> str:
    """Return E.164 (``+61412345678``) or ``''`` so Shopify will not reject the order.

    Marketplace sandbox phones (``0400000000``, ``0``, placeholders) are omitted
    rather than sent. Shopify ``orderCreate`` requires a valid E.164 number.
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    compact = re.sub(r"[^\d+]", "", text)
    if compact.startswith("00"):
        compact = "+" + compact[2:]
    digits = compact[1:] if compact.startswith("+") else compact
    if not digits.isdigit():
        return ""
    country = (country_code or "").strip().upper()
    if len(country) != 2:
        country = ""
    dial = _COUNTRY_DIAL.get(country, "")

    nanp = len(digits) == 10 and digits[0] not in "01"
    if compact.startswith("+"):
        body = digits.lstrip("0")
    elif country == "AU" and digits.startswith("0") and len(digits) == 10:
        body = "61" + digits.lstrip("0")
    elif country == "AU" and len(digits) == 9:
        body = "61" + digits
    elif country == "AU" and nanp:
        # MyDeal/WMP sandbox often sends a US NANP number on an AU address.
        body = "1" + digits
    elif dial and digits.startswith("0"):
        body = dial + digits.lstrip("0")
    elif dial and digits.startswith(dial):
        body = digits
    elif country in ("US", "CA") and nanp:
        body = "1" + digits
    elif nanp:
        body = "1" + digits
    elif dial:
        body = dial + digits.lstrip("0")
    else:
        body = digits.lstrip("0")

    if not re.fullmatch(r"\d{8,15}", body or ""):
        return ""
    if len(set(body)) <= 1:
        return ""

    # Longest calling code first so "61" wins over "1".
    for iso, code in sorted(_COUNTRY_DIAL.items(), key=lambda kv: -len(kv[1])):
        if not body.startswith(code):
            continue
        national = body[len(code):]
        if iso == "AU" and national.startswith("0"):
            national = national.lstrip("0")
            body = code + national
        if not national or set(national) == {"0"}:
            return ""
        if iso == "AU":
            if len(national) != 9 or national[0] not in "23478":
                return ""
            # Reject sandbox-style 400000000 / 200000000.
            if set(national[1:]) == {"0"}:
                return ""
        elif iso in ("US", "CA"):
            if len(national) != 10 or national[0] in "01":
                return ""
        break
    else:
        # Unknown country: keep only if it already looked international.
        if not compact.startswith("+") and not (dial and digits.startswith(dial)):
            return ""

    return f"+{body}"


def push_new_order_to_shopify(order, store, *, created: bool) -> None:
    """Create on first insert; update tags/note/source when Shopify already has the order."""
    if order is None or store is None:
        return
    kind = marketplace_kind(getattr(store, "marketplace", None))
    if kind not in SHOPIFY_ORDER_MARKETPLACES:
        return
    if not store_shopify_ready(store):
        return
    has_id = bool(
        (getattr(order, "shopify_order_id", None) or "").strip()
        or (getattr(order, "shopify_order_gid", None) or "").strip()
    )
    try:
        if has_id:
            _update_existing(order, store, kind)
            return
        prev_error = (getattr(order, "shopify_sync_error", None) or "").strip()
        if not created and not prev_error:
            return
        _push(order, store, kind)
    except Exception as exc:
        logger.warning(
            "Shopify order sync failed store=%s order=%s err=%s",
            getattr(store, "id", None),
            getattr(order, "external_order_key", None),
            exc,
        )
        _save_sync_error(order, str(exc)[:500])


def push_fulfillment_to_shopify(order, store, *, tracking_number: str, carrier: str = "",
                                tracking_url: str = "") -> None:
    """Create a Shopify fulfillment when SellerPilot submits tracking."""
    if order is None or store is None:
        return
    if not store_shopify_ready(store):
        return
    kind = marketplace_kind(getattr(store, "marketplace", None))
    if kind not in SHOPIFY_ORDER_MARKETPLACES:
        return
    if not _order_gid(order):
        return
    tracking = (tracking_number or "").strip()
    if not tracking:
        return
    try:
        _fulfill_existing(order, store, tracking_number=tracking, carrier=carrier, tracking_url=tracking_url)
    except Exception as exc:
        logger.warning(
            "Shopify fulfillment failed store=%s order=%s err=%s",
            getattr(store, "id", None),
            getattr(order, "external_order_key", None),
            exc,
        )
        _save_sync_error(order, str(exc)[:500])


def cancel_shopify_order(order, store) -> None:
    """Cancel the mirrored Shopify order when SellerPilot cancels."""
    if order is None or store is None:
        return
    if not store_shopify_ready(store):
        return
    kind = marketplace_kind(getattr(store, "marketplace", None))
    if kind not in SHOPIFY_ORDER_MARKETPLACES:
        return
    gid = _order_gid(order)
    if not gid:
        return
    try:
        data = graphql(store, ORDER_CANCEL_MUTATION, {
            "orderId": gid,
            "reason": "OTHER",
            "refund": False,
            "restock": False,
            "notifyCustomer": False,
        })
        errors = ((data.get("orderCancel") or {}).get("orderCancelUserErrors") or [])
        if errors:
            first = errors[0] if isinstance(errors[0], dict) else {"message": str(errors[0])}
            message = str(first.get("message") or "Shopify orderCancel failed.")
            lowered = message.lower()
            if "already" in lowered and "cancel" in lowered:
                return
            raise ShopifyError(message)
    except Exception as exc:
        logger.warning(
            "Shopify cancel failed store=%s order=%s err=%s",
            getattr(store, "id", None),
            getattr(order, "external_order_key", None),
            exc,
        )
        _save_sync_error(order, str(exc)[:500])


def _save_sync_error(order, message: str) -> None:
    order.shopify_sync_error = message[:500]
    order.save(update_fields=["shopify_sync_error", "updated_at"])


def _save_shopify_ids(order, *, gid: str, name: str = "") -> None:
    numeric = numeric_id_from_gid(gid)
    order.shopify_order_gid = gid[:128]
    order.shopify_order_id = numeric[:64]
    order.shopify_order_name = (name or "")[:64]
    order.shopify_synced_at = timezone.now()
    order.shopify_sync_error = ""
    order.save(update_fields=[
        "shopify_order_gid",
        "shopify_order_id",
        "shopify_order_name",
        "shopify_synced_at",
        "shopify_sync_error",
        "updated_at",
    ])


def _order_gid(order, existing: dict | None = None) -> str:
    if existing and existing.get("id"):
        return str(existing.get("id") or "").strip()
    gid = (getattr(order, "shopify_order_gid", None) or "").strip()
    if gid:
        return gid
    numeric = (getattr(order, "shopify_order_id", None) or "").strip()
    if numeric:
        if numeric.startswith("gid://"):
            return numeric
        return f"gid://shopify/Order/{numeric}"
    return ""


def _push(order, store, kind: str) -> None:
    order_key = str(order.external_order_key or order.invoice_number or "").strip()
    if not order_key:
        raise ShopifyError("Marketplace order is missing an order key.")
    tag = tracking_tag(kind, order_key)
    existing = _find_existing(store, order_key=order_key, tag=tag)
    if existing:
        _save_shopify_ids(order, gid=existing.get("id") or "", name=existing.get("name") or "")
        _update_existing(order, store, kind, existing=existing)
        return
    payload = build_order_create_input(order, store, kind=kind, tag=tag)
    data = graphql(store, ORDER_CREATE_MUTATION, {
        "order": payload["order"],
        "options": payload["options"],
    })
    result = (data.get("orderCreate") or {}) if isinstance(data, dict) else {}
    errors = result.get("userErrors") or []
    if errors:
        first = errors[0] if isinstance(errors[0], dict) else {"message": str(errors[0])}
        raise ShopifyError(first.get("message") or "Shopify orderCreate failed.")
    created = result.get("order") or {}
    gid = created.get("id") or ""
    if not gid:
        raise ShopifyError("Shopify orderCreate returned no order id.")
    _save_shopify_ids(order, gid=gid, name=created.get("name") or "")


def _update_existing(order, store, kind: str, existing: dict | None = None) -> None:
    gid = _order_gid(order, existing)
    if not gid:
        raise ShopifyError("Shopify order id is missing.")
    data = graphql(store, GET_ORDER_QUERY, {"id": gid})
    current = (data.get("order") or {}) if isinstance(data, dict) else {}
    if not current:
        current = existing or {}
    if current.get("id") and not (getattr(order, "shopify_order_gid", None) or "").strip():
        _save_shopify_ids(order, gid=current.get("id") or gid, name=current.get("name") or "")
    order_key = str(order.external_order_key or order.invoice_number or "").strip()
    meta = _order_metadata(order, kind, tag=tracking_tag(kind, order_key))
    _replace_order_tags(store, gid, current.get("tags") or [], meta["tags"])
    attrs = {}
    for row in current.get("customAttributes") or []:
        if isinstance(row, dict) and row.get("key"):
            attrs[str(row["key"])] = str(row.get("value") or "")
    for row in meta["customAttributes"]:
        attrs[row["key"]] = row["value"]
    update_input = {
        "id": gid,
        "note": meta["note"],
        "customAttributes": [{"key": k, "value": v} for k, v in attrs.items()],
    }
    customer = _customer(order)
    country_hint = _country_code(getattr(store, "region", "") or "")
    address = _shipping_address(customer, country_hint=country_hint)
    phone = normalize_shopify_phone(
        customer.get("phone") or "",
        (address or {}).get("countryCode") or country_hint,
    )
    if not phone and address:
        phone = address.get("phone") or ""
    if phone:
        update_input["phone"] = phone
    if address:
        update_input["shippingAddress"] = address
    data = graphql(store, ORDER_UPDATE_MUTATION, {
        "input": update_input,
    })
    result = (data.get("orderUpdate") or {}) if isinstance(data, dict) else {}
    errors = result.get("userErrors") or []
    if errors:
        first = errors[0] if isinstance(errors[0], dict) else {"message": str(errors[0])}
        raise ShopifyError(first.get("message") or "Shopify orderUpdate failed.")
    updated = result.get("order") or {}
    if updated.get("id"):
        _save_shopify_ids(order, gid=updated.get("id") or gid, name=updated.get("name") or "")
    _ensure_shipping_line(order, store, gid, current)


def _raise_user_errors(data: dict | None, key: str, *, label: str = "") -> dict:
    result = (data or {}).get(key) or {}
    errors = result.get("userErrors") or []
    if errors:
        first = errors[0] if isinstance(errors[0], dict) else {"message": str(errors[0])}
        raise ShopifyError(first.get("message") or f"Shopify {label or key} failed.")
    return result if isinstance(result, dict) else {}


def _replace_order_tags(store, gid: str, existing_tags, wanted: list[str]) -> None:
    wanted = [str(t).strip() for t in (wanted or []) if str(t or "").strip()]
    existing_list = [str(t).strip() for t in (existing_tags or []) if str(t or "").strip()]
    to_remove = [tag for tag in existing_list if tag not in wanted]
    if to_remove:
        data = graphql(store, TAGS_REMOVE_MUTATION, {"id": gid, "tags": to_remove})
        _raise_user_errors(data, "tagsRemove")
    remaining_lower = {tag.lower() for tag in existing_list if tag in wanted}
    to_add = [tag for tag in wanted if tag.lower() not in remaining_lower]
    if to_add:
        data = graphql(store, TAGS_ADD_MUTATION, {"id": gid, "tags": to_add})
        _raise_user_errors(data, "tagsAdd")


def _ensure_shipping_line(order, store, gid: str, current: dict) -> None:
    lines = current.get("shippingLines") or []
    if isinstance(lines, dict):
        lines = lines.get("nodes") or lines.get("edges") or []
    titles = []
    for row in lines:
        if isinstance(row, dict):
            titles.append(str(row.get("title") or "").strip())
        elif isinstance(row, str):
            titles.append(row.strip())
    if any(titles):
        return
    currency = _currency(order, store)
    try:
        began = graphql(store, ORDER_EDIT_BEGIN_MUTATION, {"id": gid})
        calc = (_raise_user_errors(began, "orderEditBegin").get("calculatedOrder") or {})
        calc_id = str(calc.get("id") or "").strip()
        if not calc_id:
            return
        added = graphql(store, ORDER_EDIT_ADD_SHIPPING_MUTATION, {
            "id": calc_id,
            "shippingLine": {
                "title": _shipping_title(order),
                "price": {
                    "amount": _money(_shipping_cents(order), currency)["shopMoney"]["amount"],
                    "currencyCode": currency[:3],
                },
            },
        })
        _raise_user_errors(added, "orderEditAddShippingLine")
        committed = graphql(store, ORDER_EDIT_COMMIT_MUTATION, {
            "id": calc_id,
            "notifyCustomer": False,
        })
        _raise_user_errors(committed, "orderEditCommit")
    except Exception as exc:
        logger.info(
            "Shopify shipping-line edit skipped store=%s order=%s err=%s",
            getattr(store, "id", None),
            getattr(order, "external_order_key", None),
            exc,
        )


def _fulfill_existing(order, store, *, tracking_number: str, carrier: str = "",
                      tracking_url: str = "") -> None:
    gid = _order_gid(order)
    data = graphql(store, ORDER_FULFILLMENT_ORDERS_QUERY, {"id": gid})
    nodes = ((((data.get("order") or {}).get("fulfillmentOrders") or {}).get("nodes")) or [])
    line_items_by_fo = []
    for node in nodes:
        if not isinstance(node, dict) or not node.get("id"):
            continue
        status = str(node.get("status") or "").upper()
        if status not in ("OPEN", "IN_PROGRESS", "SCHEDULED"):
            continue
        remaining = []
        for li in ((node.get("lineItems") or {}).get("nodes") or []):
            if not isinstance(li, dict) or not li.get("id"):
                continue
            try:
                qty = int(li.get("remainingQuantity") or 0)
            except (TypeError, ValueError):
                qty = 0
            if qty > 0:
                remaining.append({"id": li["id"], "quantity": qty})
        if remaining:
            line_items_by_fo.append({
                "fulfillmentOrderId": node["id"],
                "fulfillmentOrderLineItems": remaining,
            })
    if not line_items_by_fo:
        return
    tracking = {"number": tracking_number[:255]}
    if (carrier or "").strip():
        tracking["company"] = (carrier or "").strip()[:255]
    if (tracking_url or "").strip():
        tracking["url"] = (tracking_url or "").strip()
    data = graphql(store, FULFILLMENT_CREATE_MUTATION, {
        "fulfillment": {
            "lineItemsByFulfillmentOrder": line_items_by_fo,
            "notifyCustomer": False,
            "trackingInfo": tracking,
        }
    })
    result = (data.get("fulfillmentCreate") or {}) if isinstance(data, dict) else {}
    errors = result.get("userErrors") or []
    if errors:
        first = errors[0] if isinstance(errors[0], dict) else {"message": str(errors[0])}
        message = str(first.get("message") or "Shopify fulfillmentCreate failed.")
        lowered = message.lower()
        if "already" in lowered and "fulfill" in lowered:
            return
        raise ShopifyError(message)


def _find_existing(store, tag: str = "", *, order_key: str = "") -> dict | None:
    parts = []
    key = (order_key or "").strip()
    if key:
        parts.append(f"source_identifier:{key}")
    if tag:
        parts.append(f"tag:{tag}")
    if not parts:
        return None
    data = graphql(store, FIND_ORDER_QUERY, {"query": " OR ".join(parts)})
    nodes = ((data.get("orders") or {}).get("nodes")) or []
    tag_l = (tag or "").lower()
    if tag_l:
        for node in nodes:
            if not isinstance(node, dict) or not node.get("id"):
                continue
            tags = [str(t).lower() for t in (node.get("tags") or [])]
            if tag_l in tags:
                return node
    for node in nodes:
        if isinstance(node, dict) and node.get("id"):
            return node
    return None


def build_order_create_input(order, store, *, kind: str, tag: str) -> dict:
    customer = _customer(order)
    country_hint = _country_code(getattr(store, "region", "") or "")
    address = _shipping_address(customer, country_hint=country_hint)
    currency = _currency(order, store)
    line_items = _line_items(order, store, currency)
    if not line_items:
        line_items = [{
            "title": f"{marketplace_tag(kind).title()} order {order.external_order_key}",
            "quantity": 1,
            "requiresShipping": True,
            "priceSet": _money(order.total_amount_cents or 0, currency),
        }]
    meta = _order_metadata(order, kind, tag=tag)
    email = (customer.get("email") or "").strip()
    country = (address or {}).get("countryCode") or country_hint
    phone = normalize_shopify_phone(customer.get("phone") or "", country)
    if not phone and address:
        phone = address.get("phone") or ""
    total_cents = order.total_amount_cents
    if total_cents is None:
        total_cents = 0
        for item in line_items:
            qty = int(item.get("quantity") or 1)
            amount = (((item.get("priceSet") or {}).get("shopMoney") or {}).get("amount")) or "0"
            try:
                total_cents += int(round(float(amount) * 100)) * qty
            except (TypeError, ValueError):
                pass
    order_input = {
        "email": email or None,
        "phone": phone or None,
        "note": meta["note"],
        "tags": meta["tags"],
        "financialStatus": "PAID",
        "sourceName": "sellerpilot",
        "sourceIdentifier": str(order.external_order_key or "")[:255],
        "customAttributes": meta["customAttributes"],
        "lineItems": line_items,
        "transactions": [{
            "kind": "SALE",
            "status": "SUCCESS",
            "gateway": marketplace_tag(kind),
            "amountSet": _money(total_cents, currency),
        }],
    }
    ship_cents = _shipping_cents(order)
    order_input["shippingLines"] = [{
        "title": _shipping_title(order),
        "priceSet": _money(ship_cents, currency),
    }]
    if _tax_inclusive(order, kind):
        gst_cents = _gst_cents_from_inclusive(total_cents)
        order_input["taxesIncluded"] = True
        if gst_cents > 0:
            order_input["taxLines"] = [{
                "title": "GST (AU)",
                "rate": "0.1",
                "priceSet": _money(gst_cents, currency),
            }]
    if address:
        order_input["shippingAddress"] = address
        order_input["billingAddress"] = address
    # Drop empty optional fields Shopify rejects.
    order_input = {k: v for k, v in order_input.items() if v not in (None, "", [])}
    options = {"inventoryBehaviour": "BYPASS"}
    loc = location_gid(getattr(store, "shopify_location_id", "") or "")
    if loc:
        options["inventoryBehaviour"] = "DECREMENT_IGNORING_POLICY"
    return {"order": order_input, "options": options}


def _order_ref(order) -> str:
    return str(order.invoice_number or order.external_order_key or "").strip()


def _channel_label(kind: str, order_source: str = "") -> str:
    """Omnivore-style channel name shown in Shopify notes and attributes."""
    if kind == "mydeal":
        return "Woolworths MarketPlus"
    src = (order_source or "").strip()
    if src and src.lower() != marketplace_tag(kind):
        return src
    return marketplace_tag(kind).title()


def _display_tags(kind: str, order_source: str = "") -> list[str]:
    """Single Shopify tag: marketplace OrderSource (BigW) or the marketplace code."""
    source = (order_source or "").strip()
    if source:
        return [source[:40]]
    fallback = marketplace_tag(kind)
    return [fallback] if fallback else []


def _order_metadata(order, kind: str, *, tag: str) -> dict:
    order_source = _order_source(order)
    label = _channel_label(kind, order_source)
    ref = _order_ref(order)
    note = f"{label} order number: {ref}"
    tags = _display_tags(kind, order_source)
    attrs = [
        {"key": f"{label} order ID", "value": ref},
        {"key": "marketplace", "value": marketplace_tag(kind)},
        {"key": "marketplace_order_key", "value": str(order.external_order_key or "")},
        {"key": "marketplace_invoice", "value": str(order.invoice_number or "")},
    ]
    if order_source:
        attrs.append({"key": "order_source", "value": order_source})
    return {"note": note[:5000], "tags": tags, "customAttributes": attrs}


def _order_source(order) -> str:
    """Storefront channel from the marketplace payload (e.g. MyDeal OrderSource=BigW)."""
    raw = order.raw_response_json if isinstance(order.raw_response_json, dict) else {}
    source = raw.get("orderSource") or raw.get("OrderSource") or ""
    nested = raw.get("_mydeal") if isinstance(raw.get("_mydeal"), dict) else {}
    if not source:
        source = nested.get("OrderSource") or nested.get("orderSource") or ""
    text = str(source or "").strip()
    if not text or text == "-":
        return ""
    return text[:255]


def _raw_payload(order) -> dict:
    raw = order.raw_response_json if isinstance(order.raw_response_json, dict) else {}
    return raw if isinstance(raw, dict) else {}


def _mydeal_raw(order) -> dict:
    raw = _raw_payload(order)
    nested = raw.get("_mydeal")
    return nested if isinstance(nested, dict) else {}


def _cents_from_money(value) -> int:
    if value is None or value == "":
        return 0
    try:
        if isinstance(value, bool):
            return 0
        if isinstance(value, int):
            return max(0, value)
        return max(0, int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)))
    except Exception:
        return 0


def _shipping_cents(order) -> int:
    raw = _raw_payload(order)
    nested = _mydeal_raw(order)
    for candidate in (raw.get("shippingCents"), raw.get("shippingTotalCents")):
        if candidate is None or candidate == "":
            continue
        try:
            return max(0, int(candidate))
        except (TypeError, ValueError):
            pass
    for source in (nested, raw):
        for key in ("TotalShippingPrice", "totalShippingPrice"):
            if source.get(key) is not None:
                return _cents_from_money(source.get(key))
    return 0


def _shipping_title(order) -> str:
    raw = _raw_payload(order)
    nested = _mydeal_raw(order)
    for source in (nested, raw):
        for key in ("ShippingMethod", "shippingMethod", "DeliveryMethod", "deliveryMethod"):
            text = str(source.get(key) or "").strip()
            if text and text != "-":
                return text[:255]
    return "Standard Delivery"


def _tax_inclusive(order, kind: str) -> bool:
    raw = _raw_payload(order)
    nested = _mydeal_raw(order)
    for source in (nested, raw):
        if "TaxInclusive" in source:
            return bool(source.get("TaxInclusive"))
        if "taxInclusive" in source:
            return bool(source.get("taxInclusive"))
    return kind == "mydeal"


def _gst_cents_from_inclusive(total_cents) -> int:
    try:
        total = int(total_cents or 0)
    except (TypeError, ValueError):
        return 0
    if total <= 0:
        return 0
    return int((Decimal(total) * Decimal("10") / Decimal("110")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _customer(order) -> dict:
    raw = order.customer_info_json if isinstance(order.customer_info_json, dict) else {}
    if not raw and isinstance(order.raw_response_json, dict):
        raw = order.raw_response_json.get("customer") or {}
    return raw if isinstance(raw, dict) else {}


def _shipping_address(customer: dict, *, country_hint: str = "") -> dict | None:
    addr = customer.get("shippingAddress") or customer.get("shipping_address") or {}
    if not isinstance(addr, dict):
        addr = {}
    first = (customer.get("firstName") or customer.get("first_name") or "").strip()
    last = (customer.get("lastName") or customer.get("last_name") or "").strip()
    name = (addr.get("name") or customer.get("name") or "").strip()
    if not first and name:
        parts = name.split(None, 1)
        first = parts[0]
        last = parts[1] if len(parts) > 1 else last
    country = _country_code(addr.get("country") or "") or (country_hint or "").strip().upper()
    phone = normalize_shopify_phone(
        addr.get("phone") or customer.get("phone") or "",
        country,
    )
    out = {
        "firstName": first or "Customer",
        "lastName": last or "-",
        "address1": (addr.get("line1") or addr.get("address1") or "").strip(),
        "address2": (addr.get("line2") or addr.get("address2") or "").strip(),
        "city": (addr.get("city") or "").strip(),
        "province": (addr.get("state") or addr.get("province") or "").strip(),
        "zip": (addr.get("postcode") or addr.get("zip") or "").strip(),
        "countryCode": country,
        "phone": phone,
        "company": (addr.get("company") or "").strip(),
    }
    if not any(out.get(k) for k in ("address1", "city", "zip")):
        return None
    if not out["countryCode"]:
        out.pop("countryCode", None)
    return {k: v for k, v in out.items() if v}


def _country_code(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    alias = _COUNTRY_ALIASES.get(text.lower())
    if alias:
        return alias
    compact = re.sub(r"[^A-Za-z]", "", text)
    if len(compact) == 2:
        return compact.upper()
    return ""


def _currency(order, store) -> str:
    raw = order.raw_response_json if isinstance(order.raw_response_json, dict) else {}
    totals = raw.get("totals") if isinstance(raw.get("totals"), dict) else {}
    for candidate in (raw.get("currency"), totals.get("currency"), raw.get("Currency")):
        if candidate and str(candidate).strip():
            return str(candidate).strip().upper()[:3]
    region = (getattr(store, "region", None) or "").upper()
    return "AUD" if region == "AU" else "USD"


def _money(cents, currency: str) -> dict:
    try:
        amount = f"{(int(cents or 0) / 100):.2f}"
    except (TypeError, ValueError):
        amount = "0.00"
    return {"shopMoney": {"amount": amount, "currencyCode": (currency or "USD")[:3]}}


def _line_items(order, store, currency: str) -> list[dict]:
    items = order.line_items_json if isinstance(order.line_items_json, list) else []
    if not items and isinstance(order.raw_response_json, dict):
        raw_items = order.raw_response_json.get("lineItems")
        items = raw_items if isinstance(raw_items, list) else []
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        qty = item.get("quantity") if item.get("quantity") is not None else item.get("qty")
        try:
            qty = int(qty) if qty is not None else 1
        except (TypeError, ValueError):
            qty = 1
        qty = max(1, qty)
        cents = item.get("priceCents")
        if cents is None:
            cents = item.get("salePriceCents")
        row = {
            "title": (item.get("title") or item.get("name") or "Item")[:255],
            "quantity": qty,
            "requiresShipping": True,
            "priceSet": _money(cents or 0, currency),
        }
        sku = str(item.get("sku") or item.get("marketplaceSku") or item.get("externalVariantKey") or "").strip()
        if sku:
            row["sku"] = sku[:255]
            variant_id = _variant_id_for_sku(store, sku)
            if variant_id:
                row["variantId"] = variant_id
        out.append(row)
    return out


def _variant_id_for_sku(store, sku: str) -> str:
    sku = (sku or "").strip()
    if not sku:
        return ""
    try:
        data = graphql(store, VARIANT_BY_SKU_QUERY, {"query": f"sku:{sku}"})
    except Exception as exc:
        logger.info("Shopify variant lookup skipped sku=%s err=%s", sku, exc)
        return ""
    nodes = ((data.get("productVariants") or {}).get("nodes")) or []
    if not nodes:
        return ""
    return str(nodes[0].get("id") or "")
