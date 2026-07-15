"""Reverb managed-store listing helpers: validate, map payload, publish."""
from __future__ import annotations

import json
import logging
from decimal import Decimal, InvalidOperation

logger = logging.getLogger("listings.reverb")

# Common Reverb condition display names → UUID (Accept-Version 3.0 catalog).
# Users can also paste a raw UUID. Fetched live when possible at publish time.
CONDITION_NAME_HINTS = {
    "brand new": "brand new",
    "mint": "mint",
    "excellent": "excellent",
    "very good": "very good",
    "good": "good",
    "fair": "fair",
    "poor": "poor",
    "non functioning": "non functioning",
    "non-functioning": "non functioning",
    "b-stock": "b-stock",
}


def parse_extras(listing_or_json) -> dict:
    """Read Reverb extras from StoreListing.external_data_object_json or a dict."""
    raw = listing_or_json
    if hasattr(listing_or_json, "external_data_object_json"):
        raw = listing_or_json.external_data_object_json
    if isinstance(raw, dict):
        data = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            data = {}
    else:
        data = {}
    if not isinstance(data, dict):
        return {}
    if data.get("marketplace") and str(data.get("marketplace")).lower() != "reverb":
        # Lasoo stores a different shape here — ignore for Reverb fields.
        if "make" not in data and "model" not in data:
            return {}
    return data


def build_extras(data: dict) -> str:
    """Serialize Reverb-specific fields into external_data_object_json."""
    make = str(data.get("make") or data.get("brand") or "").strip()
    model = str(data.get("model") or "").strip()
    payload = {
        "marketplace": "reverb",
        "make": make,
        "model": model,
        "finish": str(data.get("finish") or "").strip(),
        "year": str(data.get("year") or "").strip(),
        "condition_uuid": str(data.get("condition_uuid") or data.get("condition") or "").strip(),
        "category_uuid": str(data.get("category_uuid") or data.get("category") or "").strip(),
        "currency": (str(data.get("currency") or "USD").strip().upper() or "USD"),
        "upc_does_not_apply": _truthy(data.get("upc_does_not_apply")),
    }
    return json.dumps(payload)


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("true", "1", "yes", "y", "t")


def _to_decimal(value):
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _photo_list(image_urls) -> list[str]:
    text = str(image_urls or "").strip()
    if not text:
        return []
    for sep in ("|", ";", "\n", ","):
        text = text.replace(sep, "|")
    return [p.strip() for p in text.split("|") if p.strip()]


def resolve_keys(data: dict) -> tuple[str, str]:
    sku = str(data.get("sku") or data.get("variant_key") or data.get("product_key") or "").strip()
    return sku, sku


def validate_listing(data: dict) -> list[str]:
    """Return human-readable errors (empty == valid) for a Reverb listing row."""
    errors: list[str] = []
    sku = str(data.get("sku") or "").strip() or "unknown"
    label = sku

    for field, name in (
        ("title", "Title"),
        ("make", "Make"),
        ("model", "Model"),
        ("sku", "SKU"),
        ("description", "Description"),
    ):
        # make can fall back to brand
        val = data.get(field)
        if field == "make" and not str(val or "").strip():
            val = data.get("brand")
        if not str(val or "").strip():
            errors.append(f"{name} is required for SKU {label}.")

    price = _to_decimal(data.get("sale_price") if data.get("sale_price") not in (None, "") else data.get("price"))
    if price is None:
        # also accept original_price as listing price
        price = _to_decimal(data.get("original_price"))
    if price is None:
        errors.append(f"Price must be a valid number for SKU {label}.")
    elif price < 0:
        errors.append(f"Price cannot be negative for SKU {label}.")

    inv = data.get("inventory")
    try:
        int(inv)
    except (TypeError, ValueError):
        errors.append(f"Inventory must be a valid number for SKU {label}.")

    condition = str(data.get("condition_uuid") or data.get("condition") or "").strip()
    if not condition:
        errors.append(
            f"Condition is required for SKU {label} "
            "(Reverb condition UUID, or a name like Brand New / Excellent)."
        )

    category = str(data.get("category_uuid") or data.get("category") or "").strip()
    if not category:
        errors.append(
            f"Category UUID is required for SKU {label} "
            "(from Reverb /api/categories/flat)."
        )

    if not _photo_list(data.get("image_urls")):
        errors.append(f"At least one photo URL is required for SKU {label}.")

    return errors


def listing_to_data(listing) -> dict:
    """Flatten a StoreListing (+ extras) into a dict for validation/publish."""
    extras = parse_extras(listing)
    price = listing.sale_price if listing.sale_price not in (None, Decimal("0")) else listing.original_price
    return {
        "sku": listing.sku or listing.external_variant_key,
        "title": listing.title,
        "description": listing.description,
        "make": extras.get("make") or listing.brand,
        "model": extras.get("model") or "",
        "finish": extras.get("finish") or "",
        "year": extras.get("year") or "",
        "brand": extras.get("make") or listing.brand,
        "category": extras.get("category_uuid") or listing.category,
        "category_uuid": extras.get("category_uuid") or listing.category,
        "condition_uuid": extras.get("condition_uuid") or "",
        "condition": extras.get("condition_uuid") or "",
        "barcode": listing.barcode,
        "image_urls": listing.image_urls,
        "inventory": listing.inventory,
        "sale_price": price,
        "original_price": listing.original_price or price,
        "currency": extras.get("currency") or "USD",
        "upc_does_not_apply": extras.get("upc_does_not_apply", False),
        "vendor_url": listing.vendor_url,
    }


def resolve_condition_uuid(adapter, condition_value: str) -> str | None:
    """Map a condition UUID or display name to a Reverb condition UUID."""
    text = str(condition_value or "").strip()
    if not text:
        return None
    # Looks like a UUID already
    if len(text) >= 32 and "-" in text:
        return text
    want = text.lower().replace("_", " ").strip()
    want = CONDITION_NAME_HINTS.get(want, want)
    try:
        conditions = adapter.list_listing_conditions()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load Reverb listing conditions: %s", exc)
        return None
    for item in conditions:
        if not isinstance(item, dict):
            continue
        name = str(item.get("display_name") or item.get("name") or "").strip().lower()
        uuid = str(item.get("uuid") or item.get("id") or "").strip()
        if name == want and uuid:
            return uuid
        # partial match e.g. "excellent" in "Excellent"
        if want and name and (want in name or name in want) and uuid:
            return uuid
    return None


def build_create_payload(data: dict, *, condition_uuid: str, publish: bool = True) -> dict:
    """Body for POST /api/listings."""
    price = _to_decimal(data.get("sale_price"))
    if price is None:
        price = _to_decimal(data.get("original_price"))
    if price is None:
        price = Decimal("0")
    currency = (str(data.get("currency") or "USD").strip().upper() or "USD")
    make = str(data.get("make") or data.get("brand") or "").strip()
    model = str(data.get("model") or "").strip()
    category_uuid = str(data.get("category_uuid") or data.get("category") or "").strip()
    photos = _photo_list(data.get("image_urls"))
    try:
        inventory = int(data.get("inventory") or 0)
    except (TypeError, ValueError):
        inventory = 0

    upc = str(data.get("barcode") or data.get("upc") or "").strip()
    upc_dna = _truthy(data.get("upc_does_not_apply")) or not upc

    body = {
        "make": make,
        "model": model,
        "title": str(data.get("title") or "").strip(),
        "description": str(data.get("description") or "").strip(),
        "sku": str(data.get("sku") or "").strip(),
        "price": {"amount": f"{price.quantize(Decimal('0.01'))}", "currency": currency},
        "condition": {"uuid": condition_uuid},
        "categories": [{"uuid": category_uuid}],
        "photos": photos,
        "has_inventory": True,
        "inventory": max(0, inventory),
        "upc_does_not_apply": "true" if upc_dna else "false",
        "publish": bool(publish),
    }
    if upc and not upc_dna:
        body["upc"] = upc
    finish = str(data.get("finish") or "").strip()
    year = str(data.get("year") or "").strip()
    if finish:
        body["finish"] = finish
    if year:
        body["year"] = year
    return body
