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


def normalize_publish_status(value) -> str:
    """draft (save unpublished) or live (publish immediately). Default draft."""
    text = str(value or "").strip().lower()
    if text in ("live", "published", "publish", "true", "1"):
        return "live"
    return "draft"


def free_shipping_enabled(value) -> bool:
    """Default True — we usually offer free shipping on Reverb."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return True
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "y", "t")


def should_publish(data: dict) -> bool:
    """True when status is live (publish on create)."""
    status = data.get("publish_status") or data.get("status")
    return normalize_publish_status(status) == "live"


def build_extras(data: dict) -> str:
    """Serialize Reverb-specific fields into external_data_object_json."""
    make = str(data.get("make") or data.get("brand") or "").strip()
    model = str(data.get("model") or "").strip()
    upc = str(data.get("barcode") or data.get("upc") or "").strip()
    # A provided UPC always wins over "does not apply".
    upc_dna = False if upc else _truthy(data.get("upc_does_not_apply"))
    payload = {
        "marketplace": "reverb",
        "make": make,
        "model": model,
        "finish": str(data.get("finish") or "").strip(),
        "year": str(data.get("year") or "").strip(),
        "condition_uuid": str(data.get("condition_uuid") or data.get("condition") or "").strip(),
        "category_uuid": str(data.get("category_uuid") or data.get("category") or "").strip(),
        "currency": (str(data.get("currency") or "USD").strip().upper() or "USD"),
        "upc_does_not_apply": upc_dna,
        "publish_status": normalize_publish_status(
            data.get("publish_status") or data.get("status")
        ),
        "free_shipping": free_shipping_enabled(data.get("free_shipping")),
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
    from listings.lasoo.mapper import clean_key

    sku = (
        clean_key(data.get("sku"))
        or clean_key(data.get("variant_key"))
        or clean_key(data.get("product_key"))
    )
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
            f"Category is required for SKU {label} "
            "(Reverb category name from the catalog, or a category UUID)."
        )

    if not _photo_list(data.get("image_urls")):
        errors.append(f"At least one photo URL is required for SKU {label}.")

    vendor_url = str(data.get("vendor_url") or "").strip()
    source_code = str(
        data.get("source_vendor_code") or data.get("vendor_code") or ""
    ).strip().lower()
    nora_source = "nora" in source_code
    if nora_source:
        if not str(data.get("vendor_id") or "").strip():
            errors.append(
                f"Vendor ID is required for SKU {label} when Vendor is Nora Inventory."
            )
    elif not vendor_url:
        errors.append(
            f"Vendor URL is required for SKU {label} "
            "(Amazon/eBay/etc. link used to scrape price and inventory)."
        )
    elif not vendor_url.lower().startswith(("http://", "https://")):
        errors.append(f"Vendor URL must be a valid http(s) link for SKU {label}.")

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
        "publish_status": normalize_publish_status(extras.get("publish_status")),
        "status": normalize_publish_status(extras.get("publish_status")),
        "free_shipping": free_shipping_enabled(extras.get("free_shipping")),
        "vendor_url": listing.vendor_url,
    }


def _looks_like_uuid(text: str) -> bool:
    t = str(text or "").strip()
    return len(t) >= 32 and "-" in t


def _norm_label(text: str) -> str:
    return (
        str(text or "")
        .strip()
        .lower()
        .replace("_", " ")
        .replace("/", " ")
        .replace("  ", " ")
    )


def resolve_condition_uuid(adapter, condition_value: str) -> str | None:
    """Map a condition UUID or display name to a Reverb condition UUID."""
    text = str(condition_value or "").strip()
    if not text:
        return None
    if _looks_like_uuid(text):
        return text
    want = text.lower().replace("_", " ").strip()
    want = CONDITION_NAME_HINTS.get(want, want)
    try:
        conditions = adapter.list_listing_conditions()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load Reverb listing conditions: %s", exc)
        return None
    exact = None
    partial = None
    for item in conditions:
        if not isinstance(item, dict):
            continue
        name = str(item.get("display_name") or item.get("name") or "").strip().lower()
        uuid = str(item.get("uuid") or item.get("id") or "").strip()
        if not uuid:
            continue
        if name == want:
            exact = uuid
            break
        if want and name and (want in name or name in want) and not partial:
            partial = uuid
    return exact or partial


def resolve_category_uuid(adapter, category_value: str) -> str | None:
    """Map a category UUID or Reverb full_name (exact preferred) to a UUID."""
    text = str(category_value or "").strip()
    if not text:
        return None
    if _looks_like_uuid(text):
        return text
    want = _norm_label(text)
    want_raw = text.lower().strip()
    try:
        categories = adapter.list_categories_flat()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load Reverb categories: %s", exc)
        return None

    exact = None
    ends_with = None
    contains = None
    for item in categories:
        if not isinstance(item, dict):
            continue
        uuid = str(item.get("uuid") or item.get("id") or "").strip()
        if not uuid:
            continue
        full = str(item.get("full_name") or item.get("name") or "").strip()
        full_l = full.lower()
        full_n = _norm_label(full)
        # leaf name after last slash, e.g. "Accessories / Cables" → "cables"
        leaf = full_l.split("/")[-1].strip() if "/" in full_l else full_l

        if full_l == want_raw or full_n == want:
            exact = uuid
            break
        if leaf == want_raw or _norm_label(leaf) == want:
            if not ends_with:
                ends_with = uuid
        elif want and (want in full_n or want_raw in full_l):
            if not contains:
                contains = uuid
    return exact or ends_with or contains


def normalize_conditions_for_ui(raw: list) -> list[dict]:
    """[{uuid, name}] for condition dropdown."""
    out = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        uuid = str(item.get("uuid") or item.get("id") or "").strip()
        name = str(item.get("display_name") or item.get("name") or "").strip()
        if uuid and name:
            out.append({"uuid": uuid, "name": name})
    return out


def normalize_categories_for_ui(raw: list, *, q: str = "") -> list[dict]:
    """[{uuid, name}] for category dropdown; optional search filter."""
    needle = str(q or "").strip().lower()
    out = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        uuid = str(item.get("uuid") or item.get("id") or "").strip()
        name = str(item.get("full_name") or item.get("name") or "").strip()
        if not uuid or not name:
            continue
        if needle and needle not in name.lower():
            continue
        out.append({"uuid": uuid, "name": name})
    return out


def build_create_payload(
    data: dict, *, condition_uuid: str, category_uuid: str | None = None, publish: bool | None = None,
) -> dict:
    """Body for POST /api/listings.

    ``publish`` defaults from the row's status column (live → true, draft → false).
    ``free_shipping`` (default true) adds a $0 rate for Continental U.S. only.
    Local pickup is left off (``local: false``) so only shipping is offered.
    """
    price = _to_decimal(data.get("sale_price"))
    if price is None:
        price = _to_decimal(data.get("original_price"))
    if price is None:
        price = Decimal("0")
    currency = (str(data.get("currency") or "USD").strip().upper() or "USD")
    make = str(data.get("make") or data.get("brand") or "").strip()
    model = str(data.get("model") or "").strip()
    cat_uuid = (category_uuid or "").strip() or str(
        data.get("category_uuid") or data.get("category") or ""
    ).strip()
    photos = _photo_list(data.get("image_urls"))
    try:
        inventory = int(data.get("inventory") or 0)
    except (TypeError, ValueError):
        inventory = 0

    upc = str(data.get("barcode") or data.get("upc") or "").strip()
    # Send UPC whenever present; only mark "does not apply" when empty.
    upc_dna = not bool(upc)
    do_publish = bool(publish) if publish is not None else should_publish(data)

    body = {
        "make": make,
        "model": model,
        "title": str(data.get("title") or "").strip(),
        "description": str(data.get("description") or "").strip(),
        "sku": str(data.get("sku") or "").strip(),
        "price": {"amount": f"{price.quantize(Decimal('0.01'))}", "currency": currency},
        "condition": {"uuid": condition_uuid},
        "categories": [{"uuid": cat_uuid}],
        "photos": photos,
        "has_inventory": True,
        "inventory": max(0, inventory),
        "upc_does_not_apply": "true" if upc_dna else "false",
        "publish": do_publish,
    }
    if upc:
        body["upc"] = upc
    finish = str(data.get("finish") or "").strip()
    year = str(data.get("year") or "").strip()
    if finish:
        body["finish"] = finish
    if year:
        body["year"] = year

    if free_shipping_enabled(data.get("free_shipping")):
        zero = {"amount": "0.00", "currency": currency}
        # Continental U.S. only — do not add XX ("Everywhere else").
        body["shipping"] = {
            "rates": [
                {"rate": zero, "region_code": "US_CON"},
            ],
            "local": False,
        }
    return body
