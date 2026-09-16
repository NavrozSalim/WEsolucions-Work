"""Map managed StoreListing rows to MyDeal ProductGroup payloads and publish."""
from __future__ import annotations

import json
import logging
import time
from collections import OrderedDict
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlparse

from django.utils import timezone

from ..errors import MarketplaceError
from ..models import ListingStatus, StoreListing
from .client import MyDealClient, MyDealResult

logger = logging.getLogger("listings.mydeal")


def _dec(value, default="0") -> Decimal:
    try:
        return Decimal(str(value if value is not None and value != "" else default))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _photo_urls(listing: StoreListing) -> list[str]:
    raw = (listing.image_urls or "").strip()
    if not raw:
        return []
    parts = []
    for sep in ("|", ";", "\n", ","):
        if sep in raw:
            parts = [
                p.strip()
                for p in raw.replace(";", "|").replace("\n", "|").replace(",", "|").split("|")
                if p.strip()
            ]
            break
    if not parts:
        parts = [raw] if raw.startswith("http") else []
    return parts[:30]


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
    if data.get("marketplace") and str(data.get("marketplace")).lower() != "mydeal":
        return {}
    return data


def build_extras(data: dict) -> str:
    """Persist MyDeal-specific optional fields on StoreListing.external_data_object_json."""
    extras = {
        "marketplace": "mydeal",
        "tags": str(data.get("tags") or "").strip(),
        "specifications": str(data.get("specifications") or "").strip(),
        "condition": str(data.get("condition") or "").strip() or "New",
        "gtin": str(data.get("gtin") or data.get("barcode") or "").strip(),
        "mpn": str(data.get("mpn") or "").strip(),
        "weight": str(data.get("weight") or "").strip(),
        "weight_unit": str(data.get("weight_unit") or "").strip() or "kg",
        "length": str(data.get("length") or "").strip(),
        "height": str(data.get("height") or "").strip(),
        "width": str(data.get("width") or "").strip(),
        "dimension_unit": str(data.get("dimension_unit") or "").strip() or "cm",
        "shipping_cost_category": str(data.get("shipping_cost_category") or "").strip() or "Flat",
        "shipping_cost_standard": str(data.get("shipping_cost_standard") or "").strip() or "0",
        "custom_freight_scheme_id": str(data.get("custom_freight_scheme_id") or "").strip(),
        "is_direct_import": bool(data.get("is_direct_import")),
        "max_days_for_delivery": str(data.get("max_days_for_delivery") or "").strip() or "10",
        "delivery_time": str(data.get("delivery_time") or "").strip() or "5-10 business days",
        "has_48_hours_dispatch": bool(data.get("has_48_hours_dispatch")),
    }
    return json.dumps(extras)


def listing_sku(listing) -> str:
    return (getattr(listing, "sku", None) or getattr(listing, "external_variant_key", None) or "").strip()


def parent_product_id(listing) -> str:
    sku = listing_sku(listing)
    return (getattr(listing, "external_product_key", None) or "").strip() or sku


def buyable_product_id(listing) -> str:
    sku = listing_sku(listing)
    return (getattr(listing, "external_variant_key", None) or sku).strip() or sku


_PARENT_INHERIT_FIELDS = (
    "title",
    "description",
    "brand",
    "category",
    "image_urls",
    "tags",
    "specifications",
    "condition",
    "shipping_cost_category",
    "shipping_cost_standard",
    "custom_freight_scheme_id",
    "is_direct_import",
    "max_days_for_delivery",
    "delivery_time",
    "has_48_hours_dispatch",
    "weight",
    "weight_unit",
    "length",
    "height",
    "width",
    "dimension_unit",
    "gtin",
    "mpn",
)
_BUYABLE_INHERIT_IF_BLANK = ("sale_price", "original_price", "inventory")


def _clean_key(value) -> str:
    from ..lasoo.mapper import clean_key

    return clean_key(value)


def _is_blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    return str(value).strip() == ""


def _row_photos(data: dict) -> list[str]:
    photos = []
    variant_img = str(data.get("variation_image_url") or "").strip()
    if variant_img.startswith("http"):
        photos.append(variant_img)
    raw_images = data.get("image_urls")
    if isinstance(raw_images, str) and raw_images.strip():
        photos.extend(
            p.strip()
            for p in raw_images.replace(";", "|").replace("\n", "|").replace(",", "|").split("|")
            if p.strip().startswith("http")
        )
    return photos


def _parent_content_score(title, description, category, photos) -> int:
    score = 0
    if str(title or "").strip():
        score += 4
    if str(description or "").strip():
        score += 2
    if str(category or "").strip().isdigit():
        score += 2
    if photos:
        score += 2
    return score


def normalize_row_keys(data: dict) -> dict:
    """WMP rules: standalone Parent SKU == SKU; variation Parent SKU != SKU."""
    row = dict(data or {})
    sku = (
        _clean_key(row.get("sku"))
        or _clean_key(row.get("variant_key"))
        or _clean_key(row.get("product_key"))
    )
    parent = _clean_key(row.get("product_key"))
    pairs = collect_option_pairs(row)
    if not pairs and (not parent or parent == sku):
        parent = sku
    row["sku"] = sku
    if sku and not _clean_key(row.get("variant_key")):
        row["variant_key"] = sku
    elif sku:
        row["variant_key"] = _clean_key(row.get("variant_key")) or sku
    if parent or not pairs:
        row["product_key"] = parent or sku
    else:
        row["product_key"] = parent
    return row


def _best_parent_row(rows: list[dict]) -> dict | None:
    if not rows:
        return None
    return max(
        rows,
        key=lambda row: _parent_content_score(
            row.get("title"),
            row.get("description"),
            row.get("category"),
            _row_photos(row),
        ),
    )


def _copy_missing_parent_fields(row: dict, donor: dict) -> None:
    if not donor or donor is row:
        return
    for key in _PARENT_INHERIT_FIELDS:
        if _is_blank(row.get(key)) and not _is_blank(donor.get(key)):
            row[key] = donor.get(key)
    for key in _BUYABLE_INHERIT_IF_BLANK:
        if _is_blank(row.get(key)) and not _is_blank(donor.get(key)):
            row[key] = donor.get(key)


def prepare_import_rows(rows: list[dict]) -> list[dict]:
    """Copy ProductGroup fields onto variant rows that share a Parent SKU."""
    prepared = [normalize_row_keys(row) for row in rows]
    groups: OrderedDict[str, list[dict]] = OrderedDict()
    for index, row in enumerate(prepared):
        parent = str(row.get("product_key") or "").strip()
        key = parent or f"__row__{index}"
        groups.setdefault(key, []).append(row)
    for members in groups.values():
        donor = _best_parent_row(members)
        if donor is None:
            continue
        for row in members:
            _copy_missing_parent_fields(row, donor)
    return prepared


def listing_to_import_row(listing) -> dict:
    extras = parse_extras(listing)
    return {
        "product_key": getattr(listing, "external_product_key", "") or "",
        "variant_key": getattr(listing, "external_variant_key", "") or "",
        "sku": getattr(listing, "sku", "") or getattr(listing, "external_variant_key", "") or "",
        "title": getattr(listing, "title", "") or "",
        "description": getattr(listing, "description", "") or "",
        "brand": getattr(listing, "brand", "") or "",
        "category": getattr(listing, "category", "") or "",
        "image_urls": getattr(listing, "image_urls", "") or "",
        "variation_image_url": getattr(listing, "variation_image_url", "") or "",
        "sale_price": getattr(listing, "sale_price", None),
        "original_price": getattr(listing, "original_price", None),
        "inventory": getattr(listing, "inventory", None),
        "option_1_name": getattr(listing, "option_1_name", "") or "",
        "option_1_value": getattr(listing, "option_1_value", "") or "",
        "option_2_name": getattr(listing, "option_2_name", "") or "",
        "option_2_value": getattr(listing, "option_2_value", "") or "",
        "option_3_name": getattr(listing, "option_3_name", "") or "",
        "option_3_value": getattr(listing, "option_3_value", "") or "",
        **extras,
    }


def prepare_single_row(data: dict, store=None) -> dict:
    """Normalize keys and fill blank parent fields from siblings already in the store."""
    row = normalize_row_keys(dict(data or {}))
    parent = str(row.get("product_key") or "").strip()
    sku = str(row.get("sku") or "").strip()
    if not store or not getattr(store, "pk", None) or not parent:
        return row
    siblings = StoreListing.objects.filter(store=store, external_product_key=parent)
    if sku:
        siblings = siblings.exclude(sku=sku).exclude(external_variant_key=sku)
    donor_rows = [listing_to_import_row(item) for item in siblings[:50]]
    donor_rows.append(row)
    donor = _best_parent_row(donor_rows)
    _copy_missing_parent_fields(row, donor)
    return row


def collect_option_pairs(listing_or_data) -> list[tuple[str, str]]:
    data = listing_or_data if isinstance(listing_or_data, dict) else None
    pairs: list[tuple[str, str]] = []
    for i in (1, 2, 3):
        if data is not None:
            name = str(data.get(f"option_{i}_name") or "").strip()
            value = str(data.get(f"option_{i}_value") or "").strip()
        else:
            name = str(getattr(listing_or_data, f"option_{i}_name", "") or "").strip()
            value = str(getattr(listing_or_data, f"option_{i}_value", "") or "").strip()
        if name or value:
            pairs.append((name, value))
    return pairs


def _listing_photos(listing) -> list[str]:
    photos = []
    variant_img = str(getattr(listing, "variation_image_url", "") or "").strip()
    if variant_img.startswith("http"):
        photos.append(variant_img)
    for url in _photo_urls(listing):
        if url not in photos:
            photos.append(url)
    return photos[:30]


def _category_id(listing, sku: str) -> int:
    category_raw = (getattr(listing, "category", None) or "").strip()
    try:
        category_id = int(category_raw) if category_raw.isdigit() else None
    except (TypeError, ValueError):
        category_id = None
    if category_id is None:
        raise MarketplaceError(
            f'MyDeal Category ID (numeric) is required in the Category field for SKU "{sku}".'
        )
    return category_id


def _listing_price(listing, sku: str) -> Decimal:
    price = _dec(listing.sale_price) if getattr(listing, "sale_price", None) else _dec(
        getattr(listing, "original_price", None)
    )
    if price <= 0:
        cents = getattr(listing, "sale_price_cents", None) or getattr(listing, "original_price_cents", None) or 0
        price = (Decimal(cents) / Decimal(100)) if cents else Decimal("0")
    if price <= 0:
        raise MarketplaceError(f'Price must be greater than 0 for SKU "{sku}".')
    return price


def validate_listing(data: dict) -> list[str]:
    """Return human-readable errors for a MyDeal create/import row."""
    data = normalize_row_keys(dict(data or {}))
    errors: list[str] = []
    sku = str(data.get("sku") or data.get("variant_key") or data.get("product_key") or "").strip()
    label = sku or "unknown"
    if not sku:
        errors.append("SKU is required for MyDeal.")
    if not str(data.get("title") or "").strip():
        errors.append(f"Title is required for SKU {label}.")
    if not str(data.get("description") or "").strip():
        errors.append(f"Description is required for SKU {label}.")
    category_raw = str(data.get("category") or "").strip()
    if not category_raw.isdigit():
        errors.append(f"MyDeal Category ID (numeric) is required for SKU {label}.")
    photos = []
    raw_images = data.get("image_urls")
    if isinstance(raw_images, str) and raw_images.strip():
        photos = [
            p.strip()
            for p in raw_images.replace(";", "|").replace("\n", "|").replace(",", "|").split("|")
            if p.strip().startswith("http")
        ]
    variant_img = str(data.get("variation_image_url") or "").strip()
    if variant_img.startswith("http"):
        photos = [variant_img] + photos
    if not photos:
        errors.append(f'At least one photo URL is required for SKU "{label}".')
    price = _dec(data.get("sale_price"))
    if price <= 0:
        price = _dec(data.get("original_price"))
    if price <= 0:
        errors.append(f"Price must be greater than 0 for SKU {label}.")
    pairs = collect_option_pairs(data)
    for name, value in pairs:
        if not name or not value:
            errors.append(
                f"Option name and value must both be set for SKU {label} (e.g. Size / M)."
            )
            break
    product_key = str(data.get("product_key") or "").strip()
    is_variant = bool(product_key and product_key != sku)
    if pairs:
        if not product_key:
            errors.append(
                f"Parent SKU is required for variation listings (SKU {label}). "
                "Use the same Parent SKU on every size/colour and a unique SKU per row."
            )
        elif product_key == sku:
            errors.append(
                f"Parent SKU must differ from SKU {label} so MyDeal can group "
                "sizes/colours on one product page."
            )
    elif is_variant:
        errors.append(
            f"At least Option 1 Name and Option 1 Value are required for SKU {label} "
            "when Parent SKU differs from SKU."
        )
    return errors


def _buyable_from_listing(listing: StoreListing) -> dict:
    sku = listing_sku(listing)
    if not sku:
        raise MarketplaceError("SKU is required to publish to MyDeal.")
    price = _listing_price(listing, sku)
    qty = int(getattr(listing, "inventory", None) or 0)
    unlimited = bool(getattr(listing, "infinite_quantity", False))
    buyable_id = buyable_product_id(listing)
    options = []
    for i, (name, value) in enumerate(collect_option_pairs(listing), start=1):
        if name and value:
            options.append({"OptionName": name, "OptionValue": value, "Position": i})
    buyable = {
        "ExternalBuyableProductID": buyable_id,
        "SKU": sku,
        "Price": float(price),
        "Quantity": 0 if unlimited else max(0, qty),
        "ProductUnlimited": unlimited,
        "Options": options,
    }
    rrp = _dec(getattr(listing, "original_price", None)) if getattr(listing, "original_price", None) else None
    if rrp and rrp > price:
        buyable["RRP"] = float(rrp)
    return buyable


def _parent_from_listing(listing: StoreListing, *, photos: list[str] | None = None) -> dict:
    sku = listing_sku(listing)
    if not sku:
        raise MarketplaceError("SKU is required to publish to MyDeal.")
    parent_id = parent_product_id(listing)
    photo_list = photos if photos is not None else _listing_photos(listing)
    if not photo_list:
        raise MarketplaceError(f'At least one photo URL is required for SKU "{sku}".')
    extras = parse_extras(listing)
    category_id = _category_id(listing, sku)
    images = [{"Id": i + 1, "Src": url, "Position": i + 1} for i, url in enumerate(photo_list[:30])]
    ship_cat = extras.get("shipping_cost_category") or "Flat"
    group = {
        "ExternalProductID": parent_id,
        "ProductSKU": parent_id,
        "Title": (listing.title or parent_id)[:200],
        "Description": listing.description or listing.title or parent_id,
        "Brand": (listing.brand or "").strip() or None,
        "Condition": extras.get("condition") or "New",
        "Images": images,
        "Categories": [{"CategoryId": category_id}],
        "ShippingCostCategory": ship_cat,
        "RequiresShipping": True,
        "IsDirectImport": bool(extras.get("is_direct_import")),
        "MaxDaysForDelivery": int(extras.get("max_days_for_delivery") or 10),
        "DeliveryTime": extras.get("delivery_time") or "5-10 business days",
        "BuyableProducts": [],
    }
    if extras.get("tags"):
        group["Tags"] = extras["tags"]
    if extras.get("specifications"):
        group["Specifications"] = extras["specifications"]
    if extras.get("gtin"):
        group["GTIN"] = extras["gtin"]
    if extras.get("mpn"):
        group["MPN"] = extras["mpn"]
    if extras.get("weight"):
        group["Weight"] = float(_dec(extras["weight"]))
        group["WeightUnit"] = extras.get("weight_unit") or "kg"
    for dim_key, api_key in (("length", "Length"), ("height", "Height"), ("width", "Width")):
        if extras.get(dim_key):
            group[api_key] = float(_dec(extras[dim_key]))
    if any(extras.get(k) for k in ("length", "height", "width")):
        group["DimensionUnit"] = extras.get("dimension_unit") or "cm"
    if ship_cat in ("Flat", "FlatAnyQty"):
        group["ShippingCostStandard"] = float(_dec(extras.get("shipping_cost_standard") or "0"))
    if ship_cat == "Custom" and extras.get("custom_freight_scheme_id"):
        try:
            group["CustomFreightSchemeID"] = int(extras["custom_freight_scheme_id"])
        except (TypeError, ValueError):
            group["CustomFreightSchemeID"] = extras["custom_freight_scheme_id"]
    if extras.get("has_48_hours_dispatch"):
        group["Has48HoursDispatch"] = True

    return {k: v for k, v in group.items() if v is not None}


def listing_to_product_group(listing: StoreListing) -> dict:
    """Build a ProductGroup for one listing (standalone or a single variant)."""
    group = _parent_from_listing(listing)
    group["BuyableProducts"] = [_buyable_from_listing(listing)]
    return group


def listings_to_product_groups(
    listings: list[StoreListing],
) -> list[tuple[dict, list[StoreListing]]]:
    """Group rows that share Parent SKU into one MyDeal ProductGroup with many buyables."""
    buckets: OrderedDict[str, dict] = OrderedDict()
    for listing in listings:
        parent_id = parent_product_id(listing)
        buyable = _buyable_from_listing(listing)
        bucket = buckets.get(parent_id)
        if bucket is None:
            buckets[parent_id] = {"listings": [listing], "buyables": [buyable]}
            continue
        if buyable["SKU"] in {b["SKU"] for b in bucket["buyables"]}:
            continue
        bucket["listings"].append(listing)
        bucket["buyables"].append(buyable)

    packed = []
    for bucket in buckets.values():
        members = bucket["listings"]
        buyables = bucket["buyables"]
        has_variation = any(
            collect_option_pairs(listing) and parent_product_id(listing) != listing_sku(listing)
            for listing in members
        )
        if has_variation:
            kept = []
            for listing, buyable in zip(members, buyables):
                sku = listing_sku(listing)
                parent = parent_product_id(listing)
                if sku == parent and not collect_option_pairs(listing):
                    continue
                kept.append(buyable)
            if kept:
                buyables = kept
        photos: list[str] = []
        for listing in members:
            for url in _listing_photos(listing):
                if url not in photos:
                    photos.append(url)
        source = max(
            members,
            key=lambda listing: _parent_content_score(
                getattr(listing, "title", ""),
                getattr(listing, "description", ""),
                getattr(listing, "category", ""),
                _listing_photos(listing),
            ),
        )
        group = _parent_from_listing(source, photos=photos[:30])
        group["BuyableProducts"] = buyables
        packed.append((group, members))
    return packed


# Product groups per POST /products. MyDeal accepts at most 250 ProductGroups.
PUBLISH_GROUP_CHUNK = 250
# Celery ingest can wait; 40 × 15s ≈ 10 minutes per batch.
PENDING_POLL_ATTEMPTS = 40
PENDING_POLL_SECONDS = 15
# Auth / connectivity failures will repeat on every chunk — stop early.
_FATAL_PUBLISH_MARKERS = (
    "configured",
    "credential",
    "access_token",
    "rejected client",
    "401",
)


def _is_fatal_publish_error(result) -> bool:
    if result is None or result.ok:
        return False
    if int(getattr(result, "status", 0) or 0) in (401, 403):
        return True
    msg = (getattr(result, "message", None) or "").lower()
    return any(marker in msg for marker in _FATAL_PUBLISH_MARKERS)


def _result_data(result) -> dict:
    data = getattr(result, "data", None)
    return data if isinstance(data, dict) else {}


def _work_item_id(result) -> str:
    data = _result_data(result)
    for key in ("WorkItemId", "workItemId", "WorkItemID"):
        value = str(data.get(key) or "").strip()
        if value:
            return value
    uri = str(data.get("PendingUri") or data.get("pendingUri") or "").strip()
    if not uri:
        return ""
    query = parse_qs(urlparse(uri).query)
    for key in ("workItemId", "WorkItemId", "workitemid"):
        values = query.get(key) or []
        if values and str(values[0]).strip():
            return str(values[0]).strip()
    return ""


def _is_async_pending(result) -> bool:
    """True while MyDeal has not returned a final product payload."""
    status = str(getattr(result, "response_status", "") or "").strip().lower().replace(" ", "")
    data = _result_data(result)
    if not status:
        status = str(data.get("ResponseStatus") or data.get("responseStatus") or "").strip().lower().replace(" ", "")
    if status in ("failed", "fail"):
        return False
    if data.get("Errors") or data.get("errors"):
        return False
    if status == "asyncresponsepending":
        return True
    pending_uri = str(data.get("PendingUri") or data.get("pendingUri") or "").strip()
    work_id = _work_item_id(result)
    empty_data = data.get("Data") in (None, "", [], {})
    # Complete with a work item and no Data is still working.
    if empty_data and (pending_uri or work_id):
        return True
    return False


def _product_on_mydeal(client, product_sku: str) -> bool:
    """True when GET /products/{sku} returns that ProductSKU (parent), not a buyable size code."""
    sku = str(product_sku or "").strip()
    if not sku:
        return False
    result = client.get_product(sku, by="sku")
    if not getattr(result, "ok", False):
        return False
    payload = getattr(result, "data", None)
    rows = []
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        inner = payload.get("Data")
        if inner is None:
            inner = payload.get("data")
        if isinstance(inner, list):
            rows = inner
        elif isinstance(inner, dict):
            rows = [inner]
        elif payload.get("ProductSKU"):
            rows = [payload]
    for row in rows:
        if isinstance(row, dict) and str(row.get("ProductSKU") or "").strip() == sku:
            return True
    return False


def _confirm_groups_on_mydeal(client, packed_chunk) -> dict[str, bool]:
    found: dict[str, bool] = {}
    for group, _members in packed_chunk:
        sku = str((group or {}).get("ProductSKU") or "").strip()
        if not sku or sku in found:
            continue
        found[sku] = _product_on_mydeal(client, sku)
    return found


def is_unconfirmed_upload(listing) -> bool:
    """True when Hub marked uploaded from AsyncResponsePending without a final result."""
    status = getattr(listing, "status", "") or ""
    if status not in (ListingStatus.UPLOADED_PRODUCTION, ListingStatus.UPLOADED_STAGING):
        return False
    request = getattr(listing, "marketplace_request_json", None)
    if isinstance(request, dict) and request.get("confirmed"):
        return False
    payload = getattr(listing, "marketplace_response_json", None)
    if not isinstance(payload, dict):
        return False
    fake = MyDealResult(
        ok=True,
        data=payload,
        response_status=str(payload.get("ResponseStatus") or payload.get("responseStatus") or ""),
    )
    return _is_async_pending(fake)


def requeue_unconfirmed_uploads(store) -> int:
    """Move false-success MyDeal rows back to Ready so they show in Created products."""
    qs = StoreListing.objects.filter(
        store=store,
        status__in=[ListingStatus.UPLOADED_STAGING, ListingStatus.UPLOADED_PRODUCTION],
    )
    ids = [
        row.id
        for row in qs.only("id", "status", "marketplace_response_json", "marketplace_request_json").iterator()
        if is_unconfirmed_upload(row)
    ]
    if not ids:
        return 0
    return StoreListing.objects.filter(id__in=ids).update(
        status=ListingStatus.READY,
        validation_errors_json=None,
    )


def _resolve_pending_upsert(client, result):
    """Poll GET /pending-responses until MyDeal finishes (or we time out)."""
    if not _is_async_pending(result):
        return result
    work_id = _work_item_id(result)
    if not work_id:
        return MyDealResult(
            ok=False,
            data=getattr(result, "data", None),
            error=getattr(result, "error", None),
            message="MyDeal accepted the batch but did not return a work item to confirm.",
            status=int(getattr(result, "status", 0) or 0),
            response_status=str(getattr(result, "response_status", "") or ""),
        )
    last = result
    attempts = max(1, int(PENDING_POLL_ATTEMPTS or 1))
    wait = max(0, float(PENDING_POLL_SECONDS or 0))
    for attempt in range(attempts):
        if wait:
            time.sleep(wait)
        last = client.get_pending_response(work_id)
        if not _is_async_pending(last):
            logger.info(
                "MyDeal pending work item %s finished on poll %s status=%s",
                work_id,
                attempt + 1,
                getattr(last, "response_status", "") or "",
            )
            return last
    logger.warning("MyDeal pending work item %s still processing after %s polls", work_id, attempts)
    return MyDealResult(
        ok=False,
        data=getattr(last, "data", None),
        error=getattr(last, "error", None),
        message="MyDeal is still creating this batch. Wait a few minutes, then click Publish all once.",
        status=int(getattr(last, "status", 0) or 0),
        response_status=str(getattr(last, "response_status", "") or "AsyncResponsePending"),
    )


def _mark_listing(listing: StoreListing, *, status: str, request=None, response=None, errors=None):
    listing.status = status
    if request is not None:
        listing.marketplace_request_json = request
    if response is not None:
        listing.marketplace_response_json = response
    listing.validation_errors_json = errors
    listing.last_uploaded_at = timezone.now()
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


def _apply_upsert_result(client, packed_chunk, result) -> tuple[int, int, str]:
    """Mark listings in this chunk from one MyDeal upsert. Returns uploaded, failed, message."""
    groups = [group for group, _members in packed_chunk]
    members = [listing for _group, members in packed_chunk for listing in members]
    chunk_meta = {"product_group_count": len(groups)}
    response_payload = result.data if isinstance(result.data, (dict, list)) else {"raw": result.data}

    target = (
        ListingStatus.UPLOADED_PRODUCTION
        if client.environment == "production"
        else ListingStatus.UPLOADED_STAGING
    )

    if _is_async_pending(result) or (
        not result.ok and "still creating this batch" in (result.message or "").lower()
    ):
        confirmed = _confirm_groups_on_mydeal(client, packed_chunk)
        uploaded = 0
        failed = 0
        timeout_err = (
            (result.message or "MyDeal is still creating this batch. Wait a few minutes, then click Publish all once.")
        )[:400]
        for group, group_members in packed_chunk:
            sku = str(group.get("ProductSKU") or "").strip()
            if sku and confirmed.get(sku):
                for listing in group_members:
                    _mark_listing(
                        listing,
                        status=target,
                        request={"product_sku": sku, "confirmed": True},
                        response={
                            "ResponseStatus": "Complete",
                            "Data": {"ProductSKU": sku},
                            "confirmed": True,
                        },
                        errors=None,
                    )
                uploaded += len(group_members)
                continue
            payload = {"error": timeout_err, "data": result.data, "status": getattr(result, "status", 0)}
            for listing in group_members:
                _mark_listing(
                    listing,
                    status=ListingStatus.FAILED,
                    request=chunk_meta,
                    response=payload,
                    errors=[timeout_err],
                )
            failed += len(group_members)
        if uploaded and not failed:
            return uploaded, 0, f"Published {uploaded} product(s) to MyDeal."
        if uploaded:
            return uploaded, failed, f"Published {uploaded} listing(s) to MyDeal; {failed} still creating."
        return 0, failed, timeout_err

    if not result.ok:
        err = (result.message or "MyDeal publish failed.")[:400]
        payload = {"error": err, "data": result.data, "status": getattr(result, "status", 0)}
        for listing in members:
            _mark_listing(
                listing,
                status=ListingStatus.FAILED,
                request=chunk_meta,
                response=payload,
                errors=[err],
            )
        return 0, len(members), err

    for group, group_members in packed_chunk:
        for listing in group_members:
            _mark_listing(
                listing,
                status=target,
                request={"product_sku": group.get("ProductSKU")},
                response=response_payload,
                errors=None,
            )
    return len(members), 0, f"Published {len(members)} product(s) to MyDeal."


def publish_listings(user, store, listings: list[StoreListing]) -> dict:
    """POST /products in chunks so a large catalog cannot fail as one request."""
    method = (getattr(store, "mydeal_setup_method", None) or "upload").strip().lower()
    if method != "api":
        raise MarketplaceError("MyDeal publish requires API connection mode.")

    client = MyDealClient(store)
    prepared: list[StoreListing] = []
    for listing in listings:
        try:
            listing_to_product_group(listing)
        except MarketplaceError as exc:
            listing.status = ListingStatus.VALIDATION_FAILED
            listing.validation_errors_json = [str(exc)]
            listing.save(update_fields=["status", "validation_errors_json", "updated_at"])
            continue
        prepared.append(listing)

    packed = listings_to_product_groups(prepared)
    if not packed:
        raise MarketplaceError("No valid listings to publish to MyDeal. Fix validation errors first.")

    uploaded = 0
    failed = 0
    chunk_messages: list[str] = []
    chunk = max(1, int(PUBLISH_GROUP_CHUNK or 250))

    for start in range(0, len(packed), chunk):
        packed_chunk = packed[start : start + chunk]
        result = _resolve_pending_upsert(
            client,
            client.upsert_products([group for group, _members in packed_chunk]),
        )
        up, fail, msg = _apply_upsert_result(client, packed_chunk, result)
        uploaded += up
        failed += fail
        if msg:
            chunk_messages.append(msg)
        if fail and _is_fatal_publish_error(result):
            remaining = packed[start + chunk :]
            leftover = [listing for _g, members in remaining for listing in members]
            if leftover:
                err = (result.message or "MyDeal publish failed.")[:400]
                payload = {"error": err, "data": result.data, "status": getattr(result, "status", 0)}
                for listing in leftover:
                    _mark_listing(
                        listing,
                        status=ListingStatus.FAILED,
                        request={"skipped": True},
                        response=payload,
                        errors=[err],
                    )
                failed += len(leftover)
            break

    ok = uploaded > 0 and failed == 0
    if uploaded and failed:
        message = f"Published {uploaded} listing(s) to MyDeal; {failed} failed."
    elif uploaded:
        message = chunk_messages[-1] if len(chunk_messages) == 1 else f"Published {uploaded} product(s) to MyDeal."
    else:
        message = chunk_messages[-1] if chunk_messages else "MyDeal publish failed."

    return {
        "ok": ok,
        "uploaded": uploaded,
        "published": uploaded,
        "failed": failed,
        "message": message,
    }


def push_inventory(listings: list[StoreListing], store) -> dict:
    """POST /products/priceandquantity for already-uploaded listings."""
    client = MyDealClient(store)
    groups = []
    for listing in listings:
        sku = (listing.sku or listing.external_variant_key or "").strip()
        if not sku:
            continue
        parent_id = parent_product_id(listing)
        buyable_id = buyable_product_id(listing)
        price = _dec(listing.sale_price) if listing.sale_price else _dec(listing.original_price)
        if price <= 0:
            cents = listing.sale_price_cents or listing.original_price_cents or 0
            price = (Decimal(cents) / Decimal(100)) if cents else Decimal("0")
        qty = int(listing.inventory or 0)
        unlimited = bool(listing.infinite_quantity)
        groups.append(
            {
                "ExternalProductID": parent_id,
                "ProductSKU": parent_id,
                "BuyableProducts": [
                    {
                        "ExternalBuyableProductID": buyable_id,
                        "SKU": sku,
                        "Price": float(price) if price > 0 else None,
                        "Quantity": 0 if unlimited else max(0, qty),
                        "ProductUnlimited": unlimited,
                    }
                ],
            }
        )
    if not groups:
        return {"ok": False, "message": "No listings to push.", "updated": 0}
    # Strip None prices
    for g in groups:
        for b in g["BuyableProducts"]:
            if b.get("Price") is None:
                b.pop("Price", None)
    result = client.update_price_quantity(groups)
    return {
        "ok": result.ok,
        "message": result.message or ("Price/stock pushed to MyDeal." if result.ok else "Push failed."),
        "updated": len(groups) if result.ok else 0,
    }
