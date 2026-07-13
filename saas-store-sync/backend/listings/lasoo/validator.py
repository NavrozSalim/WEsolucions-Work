"""Validates user listing data before conversion/upload to Lasoo.

Returns clear, per-variant error messages, e.g.:
  "Description is required for variant JJ-XZ216-BK."
  "Sale Price must be lower than or equal to Original Price."
"""
from decimal import Decimal, InvalidOperation

from .mapper import normalize_image_urls, resolve_keys

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


def validate_listing(data: dict) -> list[str]:
    """Return a list of human-readable error strings (empty == valid)."""
    errors: list[str] = []
    key = _variant_label(data)

    for field, label in REQUIRED_TEXT_FIELDS:
        if not str(data.get(field, "") or "").strip():
            errors.append(f"{label} is required for variant {key}.")

    # Product/variant keys fall back to SKU, so they only fail when SKU is empty too.
    product_key, variant_key = resolve_keys(data)
    if not product_key:
        errors.append(f"Product Key is required for variant {key}.")
    if not variant_key:
        errors.append(f"Variant Key is required for variant {key}.")

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
