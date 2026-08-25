"""Create Shopify Admin orders for newly fetched marketplace orders.

Existing MarketplaceOrder rows are never backfilled unless a previous Shopify
create failed (retry). If Shopify already has the order (Omnivore or a previous
push), we attach that id instead of duplicating.

Phone numbers are converted to E.164; invalid/sandbox phones are omitted so
Shopify ``orderCreate`` does not fail with "Order Phone is invalid".
"""
from __future__ import annotations

import logging
import re

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

SHOPIFY_ORDER_MARKETPLACES = ("reverb", "lasoo", "mydeal", "etsy")

FIND_ORDER_QUERY = """
query FindMarketplaceOrder($query: String!) {
  orders(first: 5, query: $query) {
    nodes {
      id
      name
      tags
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

    if compact.startswith("+"):
        body = digits.lstrip("0")
    elif dial and digits.startswith("0"):
        body = dial + digits.lstrip("0")
    elif dial and digits.startswith(dial):
        body = digits
    elif country in ("US", "CA") and len(digits) == 10:
        body = "1" + digits
    elif country == "AU" and len(digits) == 9:
        body = "61" + digits
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
    """Push on first insert, or retry when a previous Shopify create failed.

    Older local orders that were never attempted are not backfilled.
    """
    if order is None or store is None:
        return
    if (getattr(order, "shopify_order_id", None) or "").strip():
        return
    prev_error = (getattr(order, "shopify_sync_error", None) or "").strip()
    if not created and not prev_error:
        return
    kind = marketplace_kind(getattr(store, "marketplace", None))
    if kind not in SHOPIFY_ORDER_MARKETPLACES:
        return
    if not store_shopify_ready(store):
        return
    try:
        _push(order, store, kind)
    except Exception as exc:
        logger.warning(
            "Shopify order push failed store=%s order=%s err=%s",
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


def _push(order, store, kind: str) -> None:
    order_key = str(order.external_order_key or order.invoice_number or "").strip()
    if not order_key:
        raise ShopifyError("Marketplace order is missing an order key.")
    tag = tracking_tag(kind, order_key)
    existing = _find_existing(store, tag)
    if existing:
        _save_shopify_ids(order, gid=existing.get("id") or "", name=existing.get("name") or "")
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


def _find_existing(store, tag: str) -> dict | None:
    data = graphql(store, FIND_ORDER_QUERY, {"query": f"tag:{tag}"})
    nodes = ((data.get("orders") or {}).get("nodes")) or []
    for node in nodes:
        if isinstance(node, dict) and node.get("id"):
            tags = [str(t).lower() for t in (node.get("tags") or [])]
            if tag.lower() in tags:
                return node
    return nodes[0] if nodes else None


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
            "priceSet": _money(order.total_amount_cents or 0, currency),
        }]
    order_source = _order_source(order)
    note = (
        f"{_channel_note_prefix(kind, order_source)} {order.invoice_number or order.external_order_key}. "
        "Created by SellerPilot. Do not duplicate if Omnivore already imported this order."
    )
    tags = ["sellerpilot", marketplace_tag(kind), tag]
    src_tag = re.sub(r"[^a-z0-9]+", "-", order_source.lower()).strip("-") if order_source else ""
    if src_tag and src_tag != marketplace_tag(kind) and src_tag not in tags:
        tags.append(src_tag)
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
        "note": note[:5000],
        "tags": tags,
        "financialStatus": "PAID",
        "sourceName": "sellerpilot",
        "sourceIdentifier": str(order.external_order_key or "")[:255],
        "customAttributes": [
            {"key": "marketplace", "value": marketplace_tag(kind)},
            {"key": "marketplace_order_key", "value": str(order.external_order_key or "")},
            {"key": "marketplace_invoice", "value": str(order.invoice_number or "")},
            *([{"key": "order_source", "value": order_source}] if order_source else []),
        ],
        "lineItems": line_items,
        "transactions": [{
            "kind": "SALE",
            "status": "SUCCESS",
            "gateway": marketplace_tag(kind),
            "amountSet": _money(total_cents, currency),
        }],
    }
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


def _channel_note_prefix(kind: str, order_source: str) -> str:
    mp = marketplace_tag(kind).title()
    src = (order_source or "").strip()
    if src and src.lower() != marketplace_tag(kind):
        return f"{mp} / {src} order"
    return f"{mp} order"


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
