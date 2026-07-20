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


OPTION_SLOTS = (1, 2, 3, 4)


def collect_option_pairs(data: dict) -> list[tuple[str, str]]:
    """Return filled (name, value) pairs from option_1..4 fields."""
    pairs: list[tuple[str, str]] = []
    for i in OPTION_SLOTS:
        name = str(data.get(f"option_{i}_name") or "").strip()
        value = str(data.get(f"option_{i}_value") or "").strip()
        if name or value:
            pairs.append((name, value))
    return pairs


def format_options_summary(data: dict) -> str:
    """Combined Options string for Lasoo / display (Size=XL; Color=Blue)."""
    pairs = collect_option_pairs(data)
    if pairs:
        parts = []
        for name, value in pairs:
            if name and value:
                parts.append(f"{name}={value}")
            elif value:
                parts.append(value)
            elif name:
                parts.append(name)
        return "; ".join(parts)
    return str(data.get("options") or "").strip()


def build_external_data_object(data: dict) -> str:
    """Build the externalDataObject JSON string.

    Includes structured Option N Name/Value (up to 4), Variation Img URL,
    and a combined Options summary for Lasoo mapping.
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

    pairs = collect_option_pairs(data)
    for i, (name, value) in enumerate(pairs, start=1):
        if name:
            obj[f"Option {i} Name"] = name
        if value:
            obj[f"Option {i} Value"] = value

    options_summary = format_options_summary(data)
    if options_summary:
        obj["Options"] = options_summary

    variation_img = str(data.get("variation_image_url") or "").strip()
    if variation_img:
        obj["Variation Img URL"] = variation_img
        obj["Variation Image URL"] = variation_img

    # externalRegionKey intentionally omitted (Lasoo: reserved for future use).
    return json.dumps(obj, ensure_ascii=False)


_BLANK_KEY_TOKENS = frozenset({
    "",
    "n/a",
    "na",
    "n.a",
    "n.a.",
    "none",
    "null",
    "nil",
    "-",
    "--",
    ".",
})


def clean_key(value) -> str:
    """Normalize a product/variant key; treat N/A-style placeholders as blank."""
    text = str(value or "").strip()
    if not text or text.lower() in _BLANK_KEY_TOKENS:
        return ""
    return text


def resolve_keys(data: dict) -> tuple[str, str]:
    """Resolve (externalProductKey, externalVariantKey).

    Multi-variant products: same Product Key on every row, unique Variant Key
    (usually equal to SKU). Single-variant: blank keys fall back to SKU.
    """
    sku = clean_key(data.get("sku"))
    variant_key = clean_key(data.get("variant_key")) or sku
    # If Variant Key is set but SKU is blank, treat SKU as the variant key.
    if not sku and variant_key:
        sku = variant_key
    product_key = clean_key(data.get("product_key")) or sku or variant_key
    return product_key, variant_key


def resolve_sku(data: dict) -> str:
    """SKU used for Lasoo / local storage — prefer explicit SKU, else Variant Key."""
    sku = clean_key(data.get("sku"))
    if sku:
        return sku
    _, variant_key = resolve_keys(data)
    return variant_key


def build_variant(data: dict) -> dict:
    product_key, variant_key = resolve_keys(data)
    sku = resolve_sku(data)
    # Keep SKU aligned with variant key in the payload when user left SKU blank.
    payload_data = dict(data)
    payload_data["sku"] = sku
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
        "externalDataObject": build_external_data_object(payload_data),
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


def build_bulk_delete_payload(variant_keys: list[str], auth_key: str) -> dict:
    """Assemble the Variants_BulkDelete payload (keys mirror BulkUpsert)."""
    return build_payload(
        "bulk_delete",
        data={
            "variants": [
                {"externalVariantKey": key} for key in variant_keys if key
            ],
        },
        auth=auth_key,
    )
