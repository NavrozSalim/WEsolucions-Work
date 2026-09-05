"""Map managed StoreListing rows to Mirakl product (P41) + offer (OF01) CSVs."""
from __future__ import annotations

import csv
import io
import json
import logging
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from ..errors import MarketplaceError
from ..models import ListingAction, ListingStatus, StoreListing
from .client import BunningsClient, extract_import_id, line_errors_by_sku

logger = logging.getLogger("listings.bunnings")

OFFER_STATE_NEW = "11"
PRODUCT_ID_TYPE_SHOP_SKU = "SHOP_SKU"
PRODUCT_CSV_HEADERS = [
    "category",
    "product-id",
    "product-id-type",
    "variant-group-code",
    "title",
    "description",
    "brand",
    "ean",
    "image-1",
    "image-2",
    "image-3",
    "image-4",
    "image-5",
    "image-6",
]
VARIANT_GROUP_HEADER = "variant-group-code"
OPTION_ATTR_ALIASES = {
    "size": "size",
    "colour": "colour",
    "color": "color",
    "finish": "finish",
    "width": "width",
    "length": "product-length",
    "height": "product-height",
}
OFFER_CSV_HEADERS = [
    "sku",
    "product-id",
    "product-id-type",
    "description",
    "price",
    "quantity",
    "state",
    "logistic-class",
    "leadtime-to-ship",
    "update-delete",
]


def _dec(value, default="0") -> Decimal:
    try:
        return Decimal(str(value if value is not None and value != "" else default))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _photo_urls(listing_or_raw) -> list[str]:
    raw = listing_or_raw
    if hasattr(listing_or_raw, "image_urls"):
        raw = listing_or_raw.image_urls
    text = str(raw or "").strip()
    if not text:
        return []
    for sep in ("|", ";", "\n", ","):
        if sep in text:
            return [p.strip() for p in text.replace(";", "|").replace("\n", "|").replace(",", "|").split("|") if p.strip()][:6]
    return [text] if text.startswith("http") else []


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
    if data.get("marketplace") and str(data.get("marketplace")).lower() != "bunnings":
        return {}
    return data


CORE_PRODUCT_CODES = frozenset({
    "category",
    "product-id",
    "product-id-type",
    "variant-group-code",
    "title",
    "description",
    "brand",
    "ean",
    "sku",
    "image-1",
    "image-2",
    "image-3",
    "image-4",
    "image-5",
    "image-6",
})
_ATTR_CACHE: dict[tuple, list] = {}
_DIM_FALLBACKS = {
    "weight": ("weight", "product-weight", "gross-weight"),
    "length": ("length", "product-length", "package-length"),
    "height": ("height", "product-height", "package-height"),
    "width": ("width", "product-width", "package-width"),
}


def attributes_from_data(data: dict) -> dict:
    raw = data.get("attributes")
    if isinstance(raw, str) and raw.strip():
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            raw = {}
    out = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            code = str(key or "").strip()
            if code and value not in (None, ""):
                out[code] = str(value).strip()
    for key, value in (data or {}).items():
        if key in ("attributes",):
            continue
        text = str(key or "").strip()
        if text.startswith("attribute_") or text.startswith("pdb_"):
            if value not in (None, ""):
                out[text] = str(value).strip()
    return out


def build_extras(data: dict) -> str:
    extras = {
        "marketplace": "bunnings",
        "gtin": str(data.get("gtin") or data.get("barcode") or "").strip(),
        "mpn": str(data.get("mpn") or "").strip(),
        "logistic_class": str(data.get("logistic_class") or "").strip(),
        "leadtime_to_ship": str(data.get("leadtime_to_ship") or "").strip() or "2",
        "product_id_type": str(data.get("product_id_type") or "").strip() or PRODUCT_ID_TYPE_SHOP_SKU,
        "weight": str(data.get("weight") or "").strip(),
        "weight_unit": str(data.get("weight_unit") or "").strip() or "kg",
        "length": str(data.get("length") or "").strip(),
        "height": str(data.get("height") or "").strip(),
        "width": str(data.get("width") or "").strip(),
        "dimension_unit": str(data.get("dimension_unit") or "").strip() or "cm",
        "attributes": attributes_from_data(data),
    }
    return json.dumps(extras)


def listing_sku(listing: StoreListing) -> str:
    return (listing.sku or listing.external_variant_key or "").strip()


def _slug_attr(name: str) -> str:
    text = str(name or "").strip().lower()
    if not text:
        return ""
    if text in OPTION_ATTR_ALIASES:
        return OPTION_ATTR_ALIASES[text]
    out: list[str] = []
    for ch in text:
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")


def collect_option_pairs(listing_or_data) -> list[tuple[str, str]]:
    data = listing_or_data
    if not isinstance(listing_or_data, dict):
        data = {
            f"option_{i}_{part}": getattr(listing_or_data, f"option_{i}_{part}", "")
            for i in (1, 2, 3, 4)
            for part in ("name", "value")
        }
    pairs: list[tuple[str, str]] = []
    for i in (1, 2, 3, 4):
        name = str(data.get(f"option_{i}_name") or "").strip()
        value = str(data.get(f"option_{i}_value") or "").strip()
        if name or value:
            pairs.append((name, value))
    return pairs


def variant_group_code(listing_or_data, sku: str = "") -> str:
    if isinstance(listing_or_data, dict):
        key = str(
            listing_or_data.get("product_key")
            or listing_or_data.get("variant_group_code")
            or ""
        ).strip()
        sku = sku or str(listing_or_data.get("sku") or listing_or_data.get("variant_key") or "").strip()
    else:
        key = str(getattr(listing_or_data, "external_product_key", "") or "").strip()
        sku = sku or listing_sku(listing_or_data)
    if key and key != sku:
        return key
    if collect_option_pairs(listing_or_data):
        return key or sku
    return ""


def listing_price(listing: StoreListing) -> Decimal:
    price = _dec(listing.sale_price) if listing.sale_price else _dec(listing.original_price)
    if price > 0:
        return price
    cents = listing.sale_price_cents or listing.original_price_cents or 0
    return (Decimal(cents) / Decimal("100")) if cents else Decimal("0")


def _looks_like_placeholder(value) -> bool:
    return "REPLACE_WITH_" in str(value or "").upper()


def validate_listing(data: dict, store=None) -> list[str]:
    errors: list[str] = []
    sku = str(data.get("sku") or data.get("variant_key") or data.get("product_key") or "").strip()
    label = sku or "unknown"
    if not sku:
        errors.append("SKU is required for Bunnings.")
    if not str(data.get("title") or "").strip():
        errors.append(f"Title is required for SKU {label}.")
    if not str(data.get("description") or "").strip():
        errors.append(f"Description is required for SKU {label}.")
    if not str(data.get("brand") or "").strip():
        errors.append(f"Brand is required for SKU {label}.")
    elif _looks_like_placeholder(data.get("brand")):
        errors.append(
            f"Brand for SKU {label} is still a placeholder. "
            "Use a brand already approved on your Bunnings shop."
        )
    category = str(data.get("category") or data.get("category_uuid") or "").strip()
    if not category:
        errors.append(f"Bunnings category code is required for SKU {label}.")
    elif _looks_like_placeholder(category):
        errors.append(
            f"Category for SKU {label} is still a placeholder. "
            "Pick the hierarchy code from the Bunnings category search."
        )
    if not str(data.get("logistic_class") or "").strip():
        errors.append(
            f"Logistic class is required for SKU {label} "
            "(from Bunnings Mirakl shop settings)."
        )
    elif _looks_like_placeholder(data.get("logistic_class")):
        errors.append(
            f"Logistic class for SKU {label} is still a placeholder. "
            "Use a code from the Bunnings logistic class dropdown."
        )
    photos = _photo_urls(data.get("image_urls"))
    if not photos:
        errors.append(f"At least one image URL is required for SKU {label}.")
    elif any(_looks_like_placeholder(url) for url in photos):
        errors.append(
            f"Image URL for SKU {label} is still a placeholder. "
            "Use a public https image URL."
        )
    price = _dec(data.get("sale_price"))
    if price <= 0:
        price = _dec(data.get("original_price"))
    if price <= 0:
        errors.append(f"Price must be greater than 0 for SKU {label} (GST inclusive).")
    pairs = collect_option_pairs(data)
    for name, value in pairs:
        if not name or not value:
            errors.append(
                f"Option name and value must both be set for SKU {label} "
                "(e.g. Size / M)."
            )
            break
    if pairs:
        product_key = str(data.get("product_key") or "").strip()
        if not product_key:
            errors.append(
                f"Parent SKU is required for variation listings (SKU {label}). "
                "Use the same Parent SKU on every size/colour and a unique SKU per row."
            )
        elif product_key == sku:
            errors.append(
                f"Parent SKU must differ from SKU {label} so Bunnings can group "
                "sizes/colours on one product page."
            )
    attrs_in = attributes_from_data(data)
    category = str(data.get("category") or data.get("category_uuid") or "").strip()
    if store is not None and category and not _looks_like_placeholder(category):
        for item in load_category_attributes(store, category):
            if not item.get("required"):
                continue
            code = item.get("code") or ""
            if _attribute_satisfied(code, data, attrs_in):
                continue
            label_attr = item.get("label") or code
            errors.append(
                f"{label_attr} is required for this Bunnings category (SKU {label})."
            )
    return errors


def flatten_product_attributes(payload) -> list[dict]:
    """PM11 → form fields. Only required/recommended, skip core P41 columns."""
    items = []
    if isinstance(payload, dict):
        items = payload.get("attributes") or payload.get("data") or []
    elif isinstance(payload, list):
        items = payload
    if not isinstance(items, list):
        return []
    out = []
    seen = set()
    for raw in items:
        if not isinstance(raw, dict):
            continue
        code = str(raw.get("code") or raw.get("name") or "").strip()
        if not code or code in CORE_PRODUCT_CODES or code in seen:
            continue
        if code.startswith("image-"):
            continue
        seen.add(code)
        level = str(raw.get("requirement_level") or raw.get("requirementLevel") or "").strip().upper()
        required = bool(raw.get("required")) or level in ("REQUIRED", "MANDATORY")
        recommended = level in ("RECOMMENDED",)
        if not required and not recommended:
            continue
        values = []
        raw_values = raw.get("values") or []
        if isinstance(raw_values, list):
            for val in raw_values:
                if isinstance(val, dict):
                    vcode = str(val.get("code") or val.get("label") or "").strip()
                    vlabel = str(val.get("label") or val.get("code") or vcode).strip()
                    if vcode:
                        values.append({"code": vcode, "label": vlabel})
                elif val not in (None, ""):
                    values.append({"code": str(val), "label": str(val)})
        out.append({
            "code": code,
            "label": str(raw.get("label") or raw.get("description") or code).strip(),
            "required": required,
            "recommended": recommended,
            "type": str(raw.get("type") or "TEXT").strip(),
            "values": values,
            "variant": bool(raw.get("variant")),
        })
    out.sort(key=lambda r: (not r["required"], r["label"].lower()))
    return out


def load_category_attributes(store, hierarchy_code: str) -> list[dict]:
    code = str(hierarchy_code or "").strip()
    if not code or store is None:
        return []
    cache_key = (str(getattr(store, "id", "")), code)
    if cache_key in _ATTR_CACHE:
        return _ATTR_CACHE[cache_key]
    try:
        client = BunningsClient(store)
        result = client.list_product_attributes(code)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Bunnings PM11 failed hierarchy=%s: %s", code, exc)
        return []
    if not result.ok:
        logger.warning("Bunnings PM11 rejected hierarchy=%s: %s", code, result.message)
        return []
    rows = flatten_product_attributes(result.data)
    _ATTR_CACHE[cache_key] = rows
    return rows


def _attribute_satisfied(code: str, data: dict, attrs: dict) -> bool:
    if str(attrs.get(code) or "").strip():
        return True
    low = (code or "").strip().lower()
    if low in ("ean", "gtin", "barcode"):
        return bool(str(data.get("gtin") or data.get("barcode") or "").strip())
    if low == "brand":
        return bool(str(data.get("brand") or "").strip())
    if low in ("title", "description"):
        return bool(str(data.get(low) or "").strip())
    if low.startswith("image"):
        return bool(_photo_urls(data.get("image_urls")))
    extras_map = {
        "weight": "weight",
        "product-weight": "weight",
        "gross-weight": "weight",
        "length": "length",
        "product-length": "length",
        "package-length": "length",
        "height": "height",
        "product-height": "height",
        "width": "width",
        "product-width": "width",
    }
    mapped = extras_map.get(low)
    if mapped and str(data.get(mapped) or "").strip():
        return True
    return False


def _write_csv(headers: list[str], rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=headers,
        delimiter=";",
        quoting=csv.QUOTE_MINIMAL,
        lineterminator="\n",
        extrasaction="ignore",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, "") for k in headers})
    return buf.getvalue()


def product_row(listing: StoreListing) -> dict:
    extras = parse_extras(listing)
    sku = listing_sku(listing)
    photos = _photo_urls(listing)
    variant_img = str(getattr(listing, "variation_image_url", "") or "").strip()
    if variant_img:
        photos = [variant_img] + [p for p in photos if p != variant_img]
        photos = photos[:6]
    gtin = extras.get("gtin") or (listing.barcode or "").strip()
    row = {
        "category": (listing.category or "").strip(),
        "product-id": sku,
        "product-id-type": extras.get("product_id_type") or PRODUCT_ID_TYPE_SHOP_SKU,
        VARIANT_GROUP_HEADER: variant_group_code(listing, sku),
        "title": (listing.title or sku)[:500],
        "description": listing.description or listing.title or sku,
        "brand": (listing.brand or "").strip(),
        "ean": gtin,
    }
    for i in range(6):
        row[f"image-{i + 1}"] = photos[i] if i < len(photos) else ""
    for name, value in collect_option_pairs(listing):
        code = _slug_attr(name)
        if code and value:
            row[code] = value
    extras_attrs = extras.get("attributes") if isinstance(extras.get("attributes"), dict) else {}
    for code, value in extras_attrs.items():
        key = str(code or "").strip()
        if key and value not in (None, "") and key not in row:
            row[key] = str(value).strip()
    for field, aliases in _DIM_FALLBACKS.items():
        val = str(extras.get(field) or "").strip()
        if not val:
            continue
        if not any(row.get(alias) for alias in aliases):
            row[aliases[0]] = val
    return row


def offer_row(listing: StoreListing, *, delete: bool = False) -> dict:
    extras = parse_extras(listing)
    sku = listing_sku(listing)
    qty = 0 if listing.infinite_quantity else max(0, int(listing.inventory or 0))
    if listing.infinite_quantity:
        qty = 9999
    return {
        "sku": sku,
        "product-id": sku,
        "product-id-type": extras.get("product_id_type") or PRODUCT_ID_TYPE_SHOP_SKU,
        "description": (listing.title or sku)[:2000],
        "price": f"{listing_price(listing):.2f}",
        "quantity": "0" if delete else str(qty),
        "state": OFFER_STATE_NEW,
        "logistic-class": extras.get("logistic_class") or "",
        "leadtime-to-ship": extras.get("leadtime_to_ship") or "2",
        "update-delete": "delete" if delete else "update",
    }


def products_csv(listings: list[StoreListing]) -> str:
    rows = [product_row(l) for l in listings]
    extra: list[str] = []
    known = set(PRODUCT_CSV_HEADERS)
    for row in rows:
        for key in row:
            if key not in known and key not in extra:
                extra.append(key)
    return _write_csv(PRODUCT_CSV_HEADERS + extra, rows)


def offers_csv(listings: list[StoreListing], *, delete: bool = False) -> str:
    return _write_csv(OFFER_CSV_HEADERS, [offer_row(l, delete=delete) for l in listings])


def flatten_hierarchies(payload, *, q: str = "", limit: int = 2000) -> list[dict]:
    """Turn H11 payload into searchable {code, name, path} rows."""
    items = []
    if isinstance(payload, dict):
        items = payload.get("hierarchies") or payload.get("data") or []
    elif isinstance(payload, list):
        items = payload
    if not isinstance(items, list):
        return []

    by_code = {}
    for raw in items:
        if not isinstance(raw, dict):
            continue
        code = str(raw.get("code") or raw.get("hierarchy_code") or "").strip()
        if not code:
            continue
        label = str(raw.get("label") or raw.get("name") or code).strip()
        parent = str(raw.get("parent_code") or raw.get("parent") or "").strip()
        by_code[code] = {"code": code, "label": label, "parent_code": parent}

    def path_for(code: str) -> str:
        parts = []
        seen = set()
        cur = code
        while cur and cur not in seen:
            seen.add(cur)
            node = by_code.get(cur)
            if not node:
                break
            parts.append(node["label"])
            cur = node["parent_code"]
        return " / ".join(reversed(parts))

    children = {code: False for code in by_code}
    for node in by_code.values():
        parent = node["parent_code"]
        if parent in children:
            children[parent] = True

    query = (q or "").strip().lower()
    out = []
    for code, node in by_code.items():
        path = path_for(code)
        if query and query not in code.lower() and query not in path.lower():
            continue
        out.append({
            "code": code,
            "name": path or node["label"],
            "label": node["label"],
            "leaf": not children.get(code, False),
        })
    out.sort(key=lambda r: (not r["leaf"], r["name"].lower()))
    if limit and len(out) > limit:
        return out[:limit]
    return out


def flatten_logistic_classes(payload) -> list[dict]:
    items = []
    if isinstance(payload, dict):
        items = (
            payload.get("logistic_classes")
            or payload.get("logistics")
            or payload.get("data")
            or []
        )
    elif isinstance(payload, list):
        items = payload
    if not isinstance(items, list):
        return []
    out = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        code = str(raw.get("code") or raw.get("logistic_class") or "").strip()
        if not code:
            continue
        label = str(raw.get("label") or raw.get("name") or code).strip()
        out.append({"code": code, "name": label})
    return out


def _mark_listing(listing: StoreListing, *, status: str, request: dict, response, errors=None):
    listing.status = status
    listing.marketplace_request_json = request
    listing.marketplace_response_json = response if isinstance(response, (dict, list)) else {"raw": response}
    listing.last_uploaded_at = timezone.now() if status.startswith("uploaded") else listing.last_uploaded_at
    listing.validation_errors_json = errors
    listing.save(
        update_fields=[
            "status",
            "marketplace_request_json",
            "marketplace_response_json",
            "last_uploaded_at",
            "validation_errors_json",
            "updated_at",
        ]
    )


def publish_listings(user, store, listings: list[StoreListing]) -> dict:
    """P41 product import (create only) then OF01 offer import."""
    client = BunningsClient(store)
    creates = [l for l in listings if (l.action or ListingAction.CREATE) != ListingAction.MAPPED]
    mapped = [l for l in listings if (l.action or "") == ListingAction.MAPPED]
    target_status = (
        ListingStatus.UPLOADED_PRODUCTION
        if client.environment == "production"
        else ListingStatus.UPLOADED_STAGING
    )

    product_import_id = ""
    product_result = None
    if creates:
        csv_text = products_csv(creates)
        product_result = client.import_products(csv_text)
        product_import_id = extract_import_id(product_result.data)
        if not product_result.ok:
            for listing in creates:
                _mark_listing(
                    listing,
                    status=ListingStatus.FAILED,
                    request={"p41": csv_text[:4000]},
                    response={"error": product_result.message, "data": product_result.data},
                    errors=[product_result.message or "P41 product import failed."],
                )
            if not mapped:
                return {
                    "ok": False,
                    "published": 0,
                    "failed": len(creates),
                    "message": product_result.message or "Bunnings product import failed.",
                    "environment": client.environment,
                }
        elif product_import_id:
            polled = client.poll_import("product", product_import_id)
            product_result = polled
            if _import_still_running(polled):
                return {
                    "ok": False,
                    "published": 0,
                    "failed": 0,
                    "message": (
                        polled.message
                        or "Bunnings product import is still running. "
                        "Wait a minute, then publish again. The listing was not marked live."
                    ),
                    "environment": client.environment,
                }
            if not polled.ok:
                sku_errors = line_errors_by_sku(polled.data)
                for listing in creates:
                    sku = listing_sku(listing)
                    detail = sku_errors.get(sku.lower()) if sku else ""
                    msg = detail or polled.message or "P41 product import failed."
                    _mark_listing(
                        listing,
                        status=ListingStatus.FAILED,
                        request={"p41_import_id": product_import_id},
                        response={"error": msg, "data": polled.data},
                        errors=[msg],
                    )
                if not mapped:
                    return {
                        "ok": False,
                        "published": 0,
                        "failed": len(creates),
                        "message": polled.message or "Bunnings product import failed.",
                        "environment": client.environment,
                    }

    offer_targets = [l for l in listings if l.status != ListingStatus.FAILED]
    if not offer_targets:
        return {
            "ok": False,
            "published": 0,
            "failed": len(listings),
            "message": "Bunnings product import failed; offer was not sent.",
            "environment": client.environment,
        }

    offer_text = offers_csv(offer_targets)
    offer_result = client.import_offers(offer_text)
    offer_import_id = extract_import_id(offer_result.data)
    if offer_result.ok and offer_import_id:
        offer_result = client.poll_import("offer", offer_import_id)

    published = 0
    failed = 0
    for listing in offer_targets:
        if offer_result.ok:
            _mark_listing(
                listing,
                status=target_status,
                request={"p41_import_id": product_import_id, "of01_import_id": offer_import_id},
                response={
                    "product_import": None if product_result is None else product_result.data,
                    "offer_import": offer_result.data,
                },
            )
            published += 1
        else:
            _mark_listing(
                listing,
                status=ListingStatus.FAILED,
                request={"p41_import_id": product_import_id, "of01": offer_text[:4000]},
                response={"error": offer_result.message, "data": offer_result.data},
                errors=[offer_result.message or "OF01 offer import failed."],
            )
            failed += 1

    if published and failed == 0:
        message = (
            f"Published {published} listing(s) to Bunnings. "
            "New products may wait for Bunnings review before they are live."
        )
    elif published:
        message = f"Published {published} listing(s); {failed} failed."
    else:
        message = offer_result.message or "Could not publish listings to Bunnings."
    return {
        "ok": published > 0 and failed == 0,
        "published": published,
        "failed": failed,
        "message": message,
        "environment": client.environment,
    }


def _import_still_running(result) -> bool:
    msg = (result.message or "").lower()
    return "still" in msg or "in progress" in msg or "waiting" in msg or "running" in msg


def push_inventory(listings: list[StoreListing], store) -> dict:
    """OF01 offer update for price + stock."""
    if not listings:
        return {"ok": False, "message": "No listings to push.", "updated": 0}
    client = BunningsClient(store)
    csv_text = offers_csv(listings)
    result = client.import_offers(csv_text, import_mode="NORMAL")
    import_id = extract_import_id(result.data)
    if result.ok and import_id:
        polled = client.poll_import("offer", import_id)
        if polled.ok or _import_still_running(polled):
            result = polled if polled.ok else result
        else:
            result = polled
    return {
        "ok": result.ok,
        "message": result.message or ("Price/stock pushed to Bunnings." if result.ok else "Bunnings offer update failed."),
        "updated": len(listings) if result.ok else 0,
    }


def end_listing(store, listing: StoreListing) -> bool:
    sku = listing_sku(listing)
    if not sku:
        return False
    client = BunningsClient(store)
    csv_text = offers_csv([listing], delete=True)
    result = client.import_offers(csv_text, import_mode="NORMAL")
    if result.ok:
        return True
    msg = (result.message or "").lower()
    if result.status == 404 or "not found" in msg:
        return False
    raise MarketplaceError(result.message or "Could not end the Bunnings offer.")


def lookup_offer(store, sku: str) -> dict | None:
    client = BunningsClient(store)
    result = client.list_offers(sku=sku, max_results=20)
    if not result.ok:
        raise MarketplaceError(result.message or "Bunnings offer lookup failed.")
    if not isinstance(result.data, dict):
        return None
    offers = result.data.get("offers") or result.data.get("data") or []
    if not isinstance(offers, list):
        return None
    needle = sku.strip().lower()
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        shop_sku = str(offer.get("shop_sku") or offer.get("sku") or "").strip()
        product_id = str(offer.get("product_id") or offer.get("product-id") or "").strip()
        if shop_sku.lower() == needle or product_id.lower() == needle:
            return offer
    return offers[0] if offers else None
