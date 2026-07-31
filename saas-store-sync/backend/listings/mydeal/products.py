"""Map managed StoreListing rows to MyDeal ProductGroup payloads and publish."""
from __future__ import annotations

import json
import logging
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


def listing_to_product_group(listing: StoreListing) -> dict:
    """Build a standalone ProductGroup for POST /products."""
    sku = (listing.sku or listing.external_variant_key or "").strip()
    if not sku:
        raise MarketplaceError("SKU is required to publish to MyDeal.")
    ext = (listing.external_product_key or sku).strip() or sku
    photos = _photo_urls(listing)
    if not photos:
        raise MarketplaceError(f'At least one photo URL is required for SKU "{sku}".')

    price = _dec(listing.sale_price) if listing.sale_price else _dec(listing.original_price)
    if price <= 0:
        cents = listing.sale_price_cents or listing.original_price_cents or 0
        price = (Decimal(cents) / Decimal(100)) if cents else Decimal("0")
    if price <= 0:
        raise MarketplaceError(f'Price must be greater than 0 for SKU "{sku}".')

    qty = int(listing.inventory or 0)
    unlimited = bool(listing.infinite_quantity)
    extras = parse_extras(listing)

    category_raw = (listing.category or "").strip()
    try:
        category_id = int(category_raw) if category_raw.isdigit() else None
    except (TypeError, ValueError):
        category_id = None
    if category_id is None:
        raise MarketplaceError(
            f'MyDeal Category ID (numeric) is required in the Category field for SKU "{sku}".'
        )

    images = [{"Id": i + 1, "Src": url, "Position": i + 1} for i, url in enumerate(photos)]

    rrp = _dec(listing.original_price) if listing.original_price else None
    buyable = {
        "ExternalBuyableProductID": ext,
        "SKU": sku,
        "Price": float(price),
        "Quantity": 0 if unlimited else max(0, qty),
        "ProductUnlimited": unlimited,
        "Options": [],
    }
    options = []
    for i in (1, 2, 3):
        name = str(getattr(listing, f"option_{i}_name", "") or "").strip()
        value = str(getattr(listing, f"option_{i}_value", "") or "").strip()
        if name and value:
            options.append({"OptionName": name[:10], "OptionValue": value, "Position": i})
    buyable["Options"] = options

    if rrp and rrp > price:
        buyable["RRP"] = float(rrp)

    ship_cat = extras.get("shipping_cost_category") or "Flat"
    group = {
        "ExternalProductID": ext,
        "ProductSKU": sku,
        "Title": (listing.title or sku)[:200],
        "Description": listing.description or listing.title or sku,
        "Brand": (listing.brand or "").strip() or None,
        "Condition": extras.get("condition") or "New",
        "Images": images,
        "Categories": [{"CategoryId": category_id}],
        "ShippingCostCategory": ship_cat,
        "RequiresShipping": True,
        "IsDirectImport": bool(extras.get("is_direct_import")),
        "MaxDaysForDelivery": int(extras.get("max_days_for_delivery") or 10),
        "DeliveryTime": extras.get("delivery_time") or "5-10 business days",
        "BuyableProducts": [buyable],
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


def publish_listings(user, store, listings: list[StoreListing]) -> dict:
    """POST /products for managed listings; poll pending-responses when async."""
    method = (getattr(store, "mydeal_setup_method", None) or "upload").strip().lower()
    if method != "api":
        raise MarketplaceError("MyDeal publish requires API connection mode.")

    client = MyDealClient(store)
    groups = []
    by_sku = {}
    for listing in listings:
        try:
            group = listing_to_product_group(listing)
        except MarketplaceError as exc:
            listing.status = ListingStatus.VALIDATION_FAILED
            listing.validation_errors_json = [str(exc)]
            listing.save(update_fields=["status", "validation_errors_json", "updated_at"])
            continue
        groups.append(group)
        by_sku[group["ProductSKU"]] = listing

    if not groups:
        raise MarketplaceError("No valid listings to publish to MyDeal. Fix validation errors first.")

    result = client.upsert_products(groups)
    uploaded = 0
    failed = 0

    # Async pending
    pending_uri = ""
    work_id = ""
    if isinstance(result.data, dict):
        pending_uri = str(result.data.get("PendingUri") or result.data.get("pendingUri") or "")
        work_id = str(result.data.get("WorkItemId") or result.data.get("workItemId") or "")
    if result.response_status == "AsyncResponsePending" or (result.ok and pending_uri and not result.data.get("Data")):
        # Best-effort: store request and mark uploaded; seller can re-check later.
        for listing in by_sku.values():
            listing.status = ListingStatus.UPLOADED_PRODUCTION if client.environment == "production" else ListingStatus.UPLOADED_STAGING
            listing.marketplace_request_json = {"product_groups": groups}
            listing.marketplace_response_json = result.data if isinstance(result.data, dict) else {"raw": result.data}
            listing.last_uploaded_at = timezone.now()
            listing.validation_errors_json = None
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
        for listing in by_sku.values():
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

    # Sync complete / complete with errors — mark all submitted listings uploaded.
    target_status = (
        ListingStatus.UPLOADED_PRODUCTION
        if client.environment == "production"
        else ListingStatus.UPLOADED_STAGING
    )
    with transaction.atomic():
        for listing in by_sku.values():
            listing.status = target_status
            listing.marketplace_request_json = {"product_groups": [g for g in groups if g["ProductSKU"] == (listing.sku or listing.external_variant_key)]}
            listing.marketplace_response_json = result.data if isinstance(result.data, (dict, list)) else {"raw": result.data}
            listing.last_uploaded_at = timezone.now()
            listing.validation_errors_json = None
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
        ext = (listing.external_product_key or sku).strip() or sku
        price = _dec(listing.sale_price) if listing.sale_price else _dec(listing.original_price)
        if price <= 0:
            cents = listing.sale_price_cents or listing.original_price_cents or 0
            price = (Decimal(cents) / Decimal(100)) if cents else Decimal("0")
        qty = int(listing.inventory or 0)
        unlimited = bool(listing.infinite_quantity)
        groups.append(
            {
                "ExternalProductID": ext,
                "ProductSKU": sku,
                "BuyableProducts": [
                    {
                        "ExternalBuyableProductID": ext,
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
