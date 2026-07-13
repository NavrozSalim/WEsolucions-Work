"""Listing CRUD, validation, bulk import, and publish to the store's marketplace.

Only Lasoo publishing is implemented for now; the dispatch point is
``publish()`` so other managed marketplaces can be added marketplace-by-marketplace.
"""
import logging
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from stores.credentials import marketplace_kind

from . import csv_import
from .errors import MarketplaceError
from .lasoo import mapper, validator
from .lasoo.client import LasooClient
from .models import Environment, ListingStatus, StoreListing

logger = logging.getLogger("listings")


def _safe_decimal(value) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return Decimal("0")


def _apply_fields(listing: StoreListing, data: dict):
    product_key, variant_key = mapper.resolve_keys(data)
    listing.external_product_key = product_key
    listing.external_variant_key = variant_key
    listing.title = (data.get("title") or "").strip()
    listing.description = (data.get("description") or "").strip()
    listing.brand = (data.get("brand") or "").strip()
    listing.category = (data.get("category") or "").strip()
    listing.sku = (data.get("sku") or "").strip()
    listing.barcode = (data.get("barcode") or "").strip()
    listing.image_urls = mapper.normalize_image_urls(data.get("image_urls"))
    listing.infinite_quantity = bool(data.get("infinite_quantity"))
    try:
        listing.inventory = 0 if listing.infinite_quantity else int(data.get("inventory") or 0)
    except (TypeError, ValueError):
        listing.inventory = 0
    listing.original_price = _safe_decimal(data.get("original_price"))
    listing.sale_price = _safe_decimal(data.get("sale_price"))


def _finalize_validation(listing: StoreListing, data: dict) -> list[str]:
    errors = validator.validate_listing(data)
    if errors:
        listing.validation_errors_json = errors
        listing.status = ListingStatus.VALIDATION_FAILED
        listing.external_data_object_json = ""
        listing.original_price_cents = 0
        listing.sale_price_cents = 0
    else:
        listing.validation_errors_json = None
        listing.original_price_cents = mapper.dollars_to_cents(data.get("original_price"))
        listing.sale_price_cents = mapper.dollars_to_cents(data.get("sale_price"))
        listing.external_data_object_json = mapper.build_external_data_object(data)
        listing.status = ListingStatus.READY
    return errors


def create(user, store, data: dict) -> StoreListing:
    environment = store.lasoo_environment or Environment.STAGING
    listing = StoreListing(user=user, store=store, environment=environment)
    _apply_fields(listing, data)
    _, variant_key = mapper.resolve_keys(data)
    if variant_key and StoreListing.objects.filter(
        store=store, external_variant_key=variant_key, environment=environment,
    ).exists():
        raise MarketplaceError(
            f'A created product with variant key "{variant_key}" already exists for this store.'
        )
    _finalize_validation(listing, data)
    listing.save()
    return listing


def update(listing: StoreListing, data: dict) -> StoreListing:
    _apply_fields(listing, data)
    _finalize_validation(listing, data)
    listing.save()
    return listing


def _listing_to_data(listing: StoreListing) -> dict:
    return {
        "product_key": listing.external_product_key,
        "variant_key": listing.external_variant_key,
        "title": listing.title,
        "description": listing.description,
        "brand": listing.brand,
        "category": listing.category,
        "sku": listing.sku,
        "barcode": listing.barcode,
        "image_urls": listing.image_urls,
        "inventory": listing.inventory,
        "infinite_quantity": listing.infinite_quantity,
        "original_price": listing.original_price,
        "sale_price": listing.sale_price,
    }


def bulk_import(user, store, filename: str, content: bytes) -> dict:
    """Import listings from a CSV/XLSX template; upserts by variant key."""
    rows = csv_import.parse_upload(filename, content)
    if not rows:
        raise MarketplaceError("No data rows found in the uploaded file.")

    environment = store.lasoo_environment or Environment.STAGING
    preview, imported = [], 0
    for row in rows:
        errors = validator.validate_listing(row)
        row_result = {
            "row_number": row.get("row_number"),
            "sku": row.get("sku", ""),
            "variant_key": row.get("variant_key") or row.get("sku", ""),
            "errors": errors,
            "valid": not errors,
            "imported": False,
        }
        if errors:
            preview.append(row_result)
            continue

        _, variant_key = mapper.resolve_keys(row)
        listing, _ = StoreListing.objects.get_or_create(
            user=user,
            store=store,
            external_variant_key=variant_key,
            environment=environment,
            defaults={"external_product_key": variant_key},
        )
        _apply_fields(listing, row)
        _finalize_validation(listing, row)
        listing.save()
        imported += 1
        row_result["imported"] = True
        preview.append(row_result)

    return {"total_rows": len(rows), "imported": imported, "rows": preview}


@transaction.atomic
def publish(user, store, listing_ids=None) -> dict:
    """Push READY (or previously uploaded) listings to the store's marketplace."""
    kind = marketplace_kind(store.marketplace)
    if kind != "lasoo":
        raise MarketplaceError(
            f'Publishing created products is not supported yet for "{kind or "this marketplace"}". '
            'Currently only Lasoo stores can publish.'
        )

    environment = store.lasoo_environment or Environment.STAGING
    qs = StoreListing.objects.filter(
        user=user,
        store=store,
        status__in=[
            ListingStatus.READY,
            ListingStatus.UPLOADED_STAGING,
            ListingStatus.UPLOADED_PRODUCTION,
            ListingStatus.FAILED,
        ],
    )
    if listing_ids:
        qs = qs.filter(id__in=listing_ids)
    listings = list(qs)
    if not listings:
        raise MarketplaceError("No valid listings to publish. Fix validation errors first.")

    # Re-validate FAILED rows so stale payload data can't be pushed.
    publishable = []
    for listing in listings:
        data = _listing_to_data(listing)
        errors = validator.validate_listing(data)
        if errors:
            listing.validation_errors_json = errors
            listing.status = ListingStatus.VALIDATION_FAILED
            listing.save(update_fields=["validation_errors_json", "status", "updated_at"])
        else:
            publishable.append(listing)
    if not publishable:
        raise MarketplaceError("All selected listings failed validation. Fix the errors and retry.")

    client = LasooClient(store, environment)
    variants = [_listing_to_data(l) for l in publishable]
    payload = mapper.build_bulk_upsert_payload(variants, client.auth_key)
    result = client.send("bulk_upsert", payload)

    now = timezone.now()
    request_for_storage = {**payload, "auth": "***"}  # never persist the raw key

    if result.ok:
        new_status = (
            ListingStatus.UPLOADED_PRODUCTION
            if environment == Environment.PRODUCTION
            else ListingStatus.UPLOADED_STAGING
        )
    else:
        new_status = ListingStatus.FAILED
    for listing in publishable:
        listing.status = new_status
        listing.marketplace_request_json = request_for_storage
        listing.marketplace_response_json = result.data if result.ok else result.error
        if result.ok:
            listing.last_uploaded_at = now
        listing.save(
            update_fields=[
                "status",
                "marketplace_request_json",
                "marketplace_response_json",
                "last_uploaded_at",
                "updated_at",
            ]
        )

    return {
        "ok": result.ok,
        "message": result.message
        or (f"Published {len(publishable)} listing(s) to Lasoo {environment}." if result.ok else ""),
        "published": len(publishable) if result.ok else 0,
        "environment": environment,
    }
