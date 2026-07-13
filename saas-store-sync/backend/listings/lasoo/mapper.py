"""Converts simple user listing fields into Lasoo's Variants_BulkUpsert payload.

Key responsibilities:
- price (dollars) -> integer cents
- image URLs -> pipe-joined string ``a|b|c``
- build ``externalDataObject`` as a single valid JSON string (escaped exactly once)
- omit ``externalRegionKey`` (reserved by Lasoo for future use)
"""
import json
from decimal import Decimal, InvalidOperation

from .queries import build_payload


def dollars_to_cents(price) -> int:
    """Convert a dollar amount to integer cents. Raises ValueError if invalid."""
    try:
        return int((Decimal(str(price)) * 100).quantize(Decimal("1")))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid price value: {price!r}") from exc


def normalize_image_urls(raw) -> str:
    """Accept a string (any of |,;newline separated) or list -> pipe-joined string."""
    if raw is None:
        return ""
    if isinstance(raw, (list, tuple)):
        parts = [str(p).strip() for p in raw]
    else:
        text = str(raw)
        for sep in (",", ";", "\n", "\r"):
            text = text.replace(sep, "|")
        parts = [p.strip() for p in text.split("|")]
    return "|".join(p for p in parts if p)


def build_external_data_object(data: dict) -> str:
    """Build the externalDataObject JSON string.

    ``data`` keys: title, description, image_urls, brand, category, sku, barcode.
    Returns a JSON string; json.dumps escapes exactly once. The surrounding
    payload serialization (requests json=) handles the second-level escaping,
    so we must NOT pre-escape here.
    """
    obj = {
        "productName": (data.get("title") or "").strip(),
        "description": (data.get("description") or "").strip(),
        "Image URLS": normalize_image_urls(data.get("image_urls")),
        "Brand": (data.get("brand") or "").strip(),
        "Category": (data.get("category") or "").strip(),
        "SKU": (data.get("sku") or "").strip(),
    }
    barcode = (data.get("barcode") or "").strip()
    if barcode:
        obj["Barcode"] = barcode
    # externalRegionKey intentionally omitted (Lasoo: reserved for future use).
    return json.dumps(obj, ensure_ascii=False)


def resolve_keys(data: dict) -> tuple[str, str]:
    """Resolve (externalProductKey, externalVariantKey).

    Falls back to SKU for single-variant products where the user left the
    product/variant key blank.
    """
    sku = (data.get("sku") or "").strip()
    product_key = (data.get("product_key") or "").strip() or sku
    variant_key = (data.get("variant_key") or "").strip() or sku
    return product_key, variant_key


def build_variant(data: dict) -> dict:
    product_key, variant_key = resolve_keys(data)
    infinite = bool(data.get("infinite_quantity"))
    try:
        inventory = 0 if infinite else int(data.get("inventory") or 0)
    except (TypeError, ValueError):
        inventory = 0
    return {
        "externalProductKey": product_key,
        "externalVariantKey": variant_key,
        "variantInventoryCount": inventory,
        "variantInfiniteQuantity": infinite,
        "variantOriginalPriceCents": dollars_to_cents(data.get("original_price")),
        "variantSalePriceCents": dollars_to_cents(data.get("sale_price")),
        "externalDataObject": build_external_data_object(data),
        "externalDataFormat": "JSON",
    }


def build_bulk_upsert_payload(
    variants: list[dict], auth_key: str, delete_unreferenced: bool = False
) -> dict:
    """Assemble the full Variants_BulkUpsert payload."""
    return build_payload(
        "bulk_upsert",
        data={
            "variants": [build_variant(v) for v in variants],
            "deleteUnreferenced": delete_unreferenced,
        },
        auth=auth_key,
    )
