"""Etsy managed-store listing helpers: validate, extras, create payload."""
from __future__ import annotations

import json
import logging
import re
from decimal import Decimal, InvalidOperation

logger = logging.getLogger("listings.etsy")

WHO_MADE_VALUES = frozenset({"i_did", "someone_else", "collective"})
# Common when_made values from Etsy docs (non-exhaustive).
WHEN_MADE_HINTS = frozenset({
    "made_to_order",
    "2020_2024",
    "2010_2019",
    "2000_2009",
    "1990_1999",
    "1980_1989",
    "1970_1979",
    "1960_1969",
    "1950_1959",
    "1940_1949",
    "1930_1939",
    "1920_1929",
    "1910_1919",
    "1900_1909",
    "1800s",
    "1700s",
    "before_1700",
})


def parse_extras(listing_or_json) -> dict:
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
    if data.get("marketplace") and str(data.get("marketplace")).lower() != "etsy":
        if "taxonomy_id" not in data and "who_made" not in data:
            return {}
    return data


def normalize_publish_status(value) -> str:
    text = str(value or "").strip().lower()
    if text in ("live", "published", "publish", "active", "true", "1"):
        return "live"
    return "draft"


def should_publish(data: dict) -> bool:
    return normalize_publish_status(data.get("publish_status") or data.get("status")) == "live"


def build_extras(data: dict) -> str:
    payload = {
        "marketplace": "etsy",
        "who_made": str(data.get("who_made") or "someone_else").strip(),
        "when_made": str(data.get("when_made") or "2020_2024").strip(),
        "taxonomy_id": str(data.get("taxonomy_id") or data.get("category") or "").strip(),
        "shipping_profile_id": str(data.get("shipping_profile_id") or "").strip(),
        "readiness_state_id": str(data.get("readiness_state_id") or "").strip(),
        "publish_status": normalize_publish_status(
            data.get("publish_status") or data.get("status")
        ),
        "listing_type": str(data.get("listing_type") or data.get("type") or "physical").strip()
        or "physical",
    }
    return json.dumps(payload)


def _to_decimal(value):
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def validate_listing(data: dict) -> list[str]:
    errors = []
    sku = str(data.get("sku") or data.get("variant_key") or "").strip()
    if not sku:
        errors.append("SKU is required.")
    title = str(data.get("title") or "").strip()
    if not title:
        errors.append("Title is required.")
    if len(title) > 140:
        errors.append("Title must be 140 characters or fewer for Etsy.")
    desc = str(data.get("description") or "").strip()
    if not desc:
        errors.append("Description is required.")
    price = _to_decimal(data.get("sale_price") or data.get("price") or data.get("original_price"))
    if price is None or price <= 0:
        errors.append("Price must be a positive number.")
    taxonomy = str(data.get("taxonomy_id") or data.get("category") or "").strip()
    if not taxonomy:
        errors.append("Taxonomy ID (category) is required.")
    elif not taxonomy.isdigit():
        errors.append("Taxonomy ID must be a numeric Etsy taxonomy_id.")
    who = str(data.get("who_made") or "").strip()
    if who and who not in WHO_MADE_VALUES:
        errors.append(
            'who_made must be one of: i_did, someone_else, collective '
            f'(got "{who}").'
        )
    when = str(data.get("when_made") or "").strip()
    if when and when not in WHEN_MADE_HINTS and not re.match(r"^[\w_]+$", when):
        errors.append(f'when_made looks invalid: "{when}".')
    images = str(data.get("image_urls") or "").strip()
    if should_publish(data) and not images:
        errors.append("Photo URLs are required to publish live on Etsy.")
    return errors


def listing_to_data(listing) -> dict:
    extras = parse_extras(listing)
    return {
        "product_key": listing.external_product_key,
        "variant_key": listing.external_variant_key,
        "title": listing.title,
        "description": listing.description,
        "brand": listing.brand,
        "category": listing.category or extras.get("taxonomy_id") or "",
        "sku": listing.sku,
        "barcode": listing.barcode,
        "vendor_url": listing.vendor_url,
        "vendor_id": listing.vendor_id,
        "source_vendor_code": listing.source_vendor_code,
        "image_urls": listing.image_urls,
        "inventory": listing.inventory,
        "original_price": listing.original_price,
        "sale_price": listing.sale_price,
        "who_made": extras.get("who_made") or "someone_else",
        "when_made": extras.get("when_made") or "2020_2024",
        "taxonomy_id": extras.get("taxonomy_id") or listing.category or "",
        "shipping_profile_id": extras.get("shipping_profile_id") or "",
        "readiness_state_id": extras.get("readiness_state_id") or "",
        "publish_status": extras.get("publish_status") or "draft",
        "listing_type": extras.get("listing_type") or "physical",
    }


def resolve_keys(data: dict) -> tuple[str, str]:
    sku = str(data.get("sku") or data.get("variant_key") or data.get("product_key") or "").strip()
    return sku, sku


def looks_like_etsy_listing_id(value: str) -> bool:
    text = str(value or "").strip()
    return bool(text.isdigit() and len(text) >= 6)


def parse_image_urls(raw: str) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    parts = re.split(r"[|\n,]+", text)
    return [p.strip() for p in parts if p.strip().startswith("http")]


def build_create_form(data: dict, *, shipping_profile_id=None, readiness_state_id=None) -> dict:
    price = _to_decimal(data.get("sale_price") or data.get("price") or data.get("original_price")) or Decimal("0")
    stock = max(0, int(data.get("inventory") or data.get("stock") or 0)) or 1
    taxonomy = str(data.get("taxonomy_id") or data.get("category") or "").strip()
    form = {
        "quantity": stock,
        "title": str(data.get("title") or "").strip(),
        "description": str(data.get("description") or data.get("title") or "").strip(),
        "price": float(price.quantize(Decimal("0.01"))),
        "who_made": str(data.get("who_made") or "someone_else").strip(),
        "when_made": str(data.get("when_made") or "2020_2024").strip(),
        "taxonomy_id": int(taxonomy),
        "type": str(data.get("listing_type") or "physical").strip() or "physical",
    }
    ship = shipping_profile_id or data.get("shipping_profile_id")
    ready = readiness_state_id or data.get("readiness_state_id")
    if ship:
        form["shipping_profile_id"] = int(ship)
    if ready:
        form["readiness_state_id"] = int(ready)
    return form
