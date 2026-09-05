"""Map managed StoreListing rows to MyDeal ProductGroup payloads and publish."""
from __future__ import annotations

import json
import logging
from collections import OrderedDict
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from ..errors import MarketplaceError
from ..models import ListingStatus, StoreListing
from .client import MyDealClient

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
        photos: list[str] = []
        for listing in members:
            for url in _listing_photos(listing):
                if url not in photos:
                    photos.append(url)
        group = _parent_from_listing(members[0], photos=photos[:30])
        group["BuyableProducts"] = bucket["buyables"]
        packed.append((group, members))
    return packed


def publish_listings(user, store, listings: list[StoreListing]) -> dict:
    """POST /products for managed listings; poll pending-responses when async."""
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
    groups = [group for group, _members in packed]
    members_flat = [listing for _group, members in packed for listing in members]

    if not groups:
        raise MarketplaceError("No valid listings to publish to MyDeal. Fix validation errors first.")

    result = client.upsert_products(groups)
    uploaded = 0
    failed = 0

    def _mark(listing: StoreListing, *, status: str, request=None, response=None, errors=None):
        listing.status = status
        if request is not None:
            listing.marketplace_request_json = request
        if response is not None:
            listing.marketplace_response_json = response
        if errors is None:
            listing.validation_errors_json = None
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

    pending_uri = ""
    work_id = ""
    if isinstance(result.data, dict):
        pending_uri = str(result.data.get("PendingUri") or result.data.get("pendingUri") or "")
        work_id = str(result.data.get("WorkItemId") or result.data.get("workItemId") or "")
    if result.response_status == "AsyncResponsePending" or (result.ok and pending_uri and not result.data.get("Data")):
        target = (
            ListingStatus.UPLOADED_PRODUCTION
            if client.environment == "production"
            else ListingStatus.UPLOADED_STAGING
        )
        for listing in members_flat:
            _mark(
                listing,
                status=target,
                request={"product_groups": groups},
                response=result.data if isinstance(result.data, dict) else {"raw": result.data},
            )
            uploaded += 1
        return {
            "ok": True,
            "uploaded": uploaded,
            "failed": failed,
            "message": (
                f"Submitted {uploaded} product(s) to MyDeal (async). "
                f"Pending work item: {work_id or pending_uri or 'see response'}."
            ),
        }

    if not result.ok:
        for listing in members_flat:
            listing.status = ListingStatus.FAILED
            listing.marketplace_response_json = {
                "error": result.message,
                "data": result.data,
            }
            listing.save(update_fields=["status", "marketplace_response_json", "updated_at"])
            failed += 1
        return {
            "ok": False,
            "uploaded": 0,
            "failed": failed,
            "message": result.message or "MyDeal publish failed.",
        }

    target_status = (
        ListingStatus.UPLOADED_PRODUCTION
        if client.environment == "production"
        else ListingStatus.UPLOADED_STAGING
    )
    with transaction.atomic():
        for group, members in packed:
            for listing in members:
                _mark(
                    listing,
                    status=target_status,
                    request={"product_groups": [group]},
                    response=result.data if isinstance(result.data, (dict, list)) else {"raw": result.data},
                )
                uploaded += 1

    return {
        "ok": True,
        "uploaded": uploaded,
        "failed": failed,
        "message": f"Published {uploaded} product(s) to MyDeal.",
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
