"""Validates user listing data before conversion/upload to Lasoo.

Returns clear, per-variant error messages, e.g.:
  "Description is required for variant JJ-XZ216-BK."
  "Sale Price must be lower than or equal to Original Price."
"""
from decimal import Decimal, InvalidOperation

from .mapper import (
    collect_option_pairs,
    format_options_summary,
    normalize_image_urls,
    resolve_keys,
)

REQUIRED_TEXT_FIELDS = [
    ("title", "Title"),
    ("description", "Description"),
    ("brand", "Brand"),
    ("sku", "SKU"),
]


def _variant_label(data: dict) -> str:
    return (
        (data.get("variant_key") or "").strip()
        or (data.get("sku") or "").strip()
        or "unknown"
    )


def _is_multi_variant(product_key: str, variant_key: str) -> bool:
    return bool(product_key and variant_key and product_key != variant_key)


def validate_listing(data: dict) -> list[str]:
    """Return a list of human-readable error strings (empty == valid)."""
    errors: list[str] = []
    key = _variant_label(data)

    for field, label in REQUIRED_TEXT_FIELDS:
        # SKU may be filled from Parent SKU when the SKU cell is blank.
        if field == "sku":
            continue
        if not str(data.get(field, "") or "").strip():
            errors.append(f"{label} is required for variant {key}.")

    # Product/variant keys fall back to SKU, so they only fail when SKU is empty too.
    product_key, variant_key = resolve_keys(data)
    sku = (data.get("sku") or "").strip() or variant_key
    if not sku:
        errors.append(f"Parent SKU or SKU is required for variant {key}.")

    # Validate option name/value pairs (incomplete pairs are errors).
    for i in (1, 2, 3, 4):
        name = str(data.get(f"option_{i}_name") or "").strip()
        value = str(data.get(f"option_{i}_value") or "").strip()
        if name and not value:
            errors.append(
                f"Option {i} Value is required for variant {key} when Option {i} Name is set."
            )
        if value and not name:
            errors.append(
                f"Option {i} Name is required for variant {key} when Option {i} Value is set."
            )

    pairs = collect_option_pairs(data)
    legacy_options = format_options_summary(data)
    variation_img = str(data.get("variation_image_url") or "").strip()

    # Multi-variant rows need at least Option 1 + variation image.
    if _is_multi_variant(product_key, variant_key):
        if not pairs and not legacy_options:
            errors.append(
                f"At least Option 1 Name and Option 1 Value are required for variant {key} "
                "(e.g. Size / XL)."
            )
        elif pairs:
            name0, value0 = pairs[0]
            if not name0 or not value0:
                errors.append(
                    f"Option 1 Name and Option 1 Value are required for variant {key}."
                )
        if not variation_img:
            errors.append(
                f"Variation Img URL is required for variant {key} when Parent SKU "
                "differs from SKU."
            )

    if not normalize_image_urls(data.get("image_urls")):
        errors.append(f"Image URLs are required for variant {key}.")

    # Inventory must be a number unless infinite quantity is set.
    if not data.get("infinite_quantity"):
        inventory = data.get("inventory")
        try:
            int(inventory)
        except (TypeError, ValueError):
            errors.append(f"Inventory must be a valid number for variant {key}.")

    orig = _to_decimal(data.get("original_price"))
    sale = _to_decimal(data.get("sale_price"))
    if orig is None:
        errors.append(f"Original Price must be a valid number for variant {key}.")
    if sale is None:
        errors.append(f"Sale Price must be a valid number for variant {key}.")
    if orig is not None and sale is not None:
        if orig < 0 or sale < 0:
            errors.append(f"Prices cannot be negative for variant {key}.")
        if sale > orig:
            errors.append(
                f"Sale Price must be lower than or equal to Original Price for variant {key}."
            )

    return errors


def _to_decimal(value):
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
