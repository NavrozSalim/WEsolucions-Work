"""Listing CRUD, validation, bulk import, and publish to the store's marketplace.

Only Lasoo publishing is implemented for now; the dispatch point is
``publish()`` so other managed marketplaces can be added marketplace-by-marketplace.
"""
import logging
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from stores.credentials import marketplace_kind

from . import csv_import
from .errors import MarketplaceError
from .lasoo import mapper, validator
from .lasoo.client import LasooClient
from .models import (
    Environment,
    ListingAction,
    ListingStatus,
    ListingUpload,
    StoreListing,
)

logger = logging.getLogger("listings")

# Listings in these statuses exist on the marketplace and need an API call to remove.
ON_MARKETPLACE_STATUSES = (
    ListingStatus.UPLOADED_STAGING,
    ListingStatus.UPLOADED_PRODUCTION,
)


def record_activity(
    user,
    store,
    *,
    action: str,
    source: str = ListingUpload.Source.SINGLE,
    filename: str = "",
    total: int = 1,
    success: int = 1,
    errors: int = 0,
    rows=None,
    message: str = "",
) -> ListingUpload:
    """Persist an Upload-history row for a file or single-listing change."""
    if errors and success:
        status = ListingUpload.Status.PARTIAL
    elif errors:
        status = ListingUpload.Status.FAILED
    else:
        status = ListingUpload.Status.COMPLETED
    return ListingUpload.objects.create(
        user=user,
        store=store,
        filename=filename,
        source=source,
        action=action,
        status=status,
        total_rows=total,
        success_rows=success,
        error_rows=errors,
        rows_json=rows,
        message=message,
    )


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
    listing.vendor_url = (data.get("vendor_url") or "").strip()[:1000]
    listing.image_urls = mapper.normalize_image_urls(data.get("image_urls"))
    listing.infinite_quantity = bool(data.get("infinite_quantity"))
    try:
        listing.inventory = 0 if listing.infinite_quantity else int(data.get("inventory") or 0)
    except (TypeError, ValueError):
        listing.inventory = 0
    listing.original_price = _safe_decimal(data.get("original_price"))
    listing.sale_price = _safe_decimal(data.get("sale_price"))


def _uploaded_status(environment: str) -> str:
    return (
        ListingStatus.UPLOADED_PRODUCTION
        if environment == Environment.PRODUCTION
        else ListingStatus.UPLOADED_STAGING
    )


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
        # Mapped listings already exist on the marketplace: valid ones go
        # straight to the uploaded status so they show under Inventory management.
        if listing.action == ListingAction.MAPPED:
            listing.status = _uploaded_status(listing.environment)
        else:
            listing.status = ListingStatus.READY
    return errors


def create(user, store, data: dict, action: str = ListingAction.CREATE) -> StoreListing:
    if action not in (ListingAction.CREATE, ListingAction.MAPPED):
        action = ListingAction.CREATE
    environment = store.lasoo_environment or Environment.STAGING
    listing = StoreListing(user=user, store=store, environment=environment, action=action)
    _apply_fields(listing, data)
    _, variant_key = mapper.resolve_keys(data)
    if variant_key and StoreListing.objects.filter(
        store=store, external_variant_key=variant_key, environment=environment,
    ).exists():
        raise MarketplaceError(
            f'A created product with variant key "{variant_key}" already exists for this store.'
            + (' Use the Mapped action to update it.' if action == ListingAction.CREATE else '')
        )
    _finalize_validation(listing, data)
    listing.save()
    return listing


def update(listing: StoreListing, data: dict) -> StoreListing:
    _apply_fields(listing, data)
    _finalize_validation(listing, data)
    listing.save()
    return listing


def delete(user, store, listing: StoreListing) -> dict:
    """Delete a listing locally; remove it from the marketplace first when it
    exists there (uploaded or mapped)."""
    on_marketplace = (
        listing.status in ON_MARKETPLACE_STATUSES
        or listing.action == ListingAction.MAPPED
    )
    if on_marketplace and marketplace_kind(store.marketplace) == "lasoo":
        environment = listing.environment or store.lasoo_environment or Environment.STAGING
        client = LasooClient(store, environment)
        payload = mapper.build_bulk_delete_payload([listing.external_variant_key], client.auth_key)
        result = client.send("bulk_delete", payload)
        if not result.ok:
            raise MarketplaceError(
                result.message or "Could not delete the listing on the marketplace."
            )
    variant_key = listing.external_variant_key
    listing.delete()
    return {"ok": True, "variant_key": variant_key, "marketplace_deleted": on_marketplace}


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
        "vendor_url": listing.vendor_url,
        "image_urls": listing.image_urls,
        "inventory": listing.inventory,
        "infinite_quantity": listing.infinite_quantity,
        "original_price": listing.original_price,
        "sale_price": listing.sale_price,
    }


def _resolve_file_action(rows: list[dict], requested: str) -> str:
    """A file must use exactly one action: from the Action column or the
    action chosen in the UI. Mixed-action files are rejected."""
    row_actions = {r.get("action") for r in rows if r.get("action")}
    if len(row_actions) > 1:
        raise MarketplaceError(
            "Use one action per file. This file mixes: "
            + ", ".join(sorted(row_actions)) + "."
        )
    action = next(iter(row_actions), "") or (requested or "").strip().lower()
    if action not in (ListingAction.CREATE, ListingAction.MAPPED, ListingAction.DELETE):
        action = ListingAction.CREATE
    if requested and row_actions and action != requested.strip().lower():
        raise MarketplaceError(
            f'You selected the "{requested}" action but the file\'s Action column says "{action}". '
            "Use one action per file."
        )
    return action


def _bulk_delete(user, store, filename: str, rows: list[dict]) -> dict:
    """Delete listings by SKU: remove from the marketplace (when there), then locally."""
    preview, deleted = [], 0
    to_delete, marketplace_keys = [], []
    for row in rows:
        sku = (row.get("sku") or "").strip()
        row_result = {
            "row_number": row.get("row_number"),
            "sku": sku,
            "variant_key": sku,
            "errors": [],
            "valid": True,
            "imported": False,
        }
        if not sku:
            row_result["errors"] = ["SKU is required to delete a listing."]
            row_result["valid"] = False
            preview.append(row_result)
            continue
        listing = (
            StoreListing.objects.filter(store=store, user=user)
            .filter(Q(sku=sku) | Q(external_variant_key=sku))
            .first()
        )
        if not listing:
            row_result["errors"] = [f'No listing found with SKU "{sku}".']
            row_result["valid"] = False
            preview.append(row_result)
            continue
        to_delete.append((listing, row_result))
        if (
            listing.status in ON_MARKETPLACE_STATUSES
            or listing.action == ListingAction.MAPPED
        ):
            marketplace_keys.append(listing.external_variant_key)
        preview.append(row_result)

    # One marketplace call for every listing that actually exists there.
    if marketplace_keys:
        if marketplace_kind(store.marketplace) != "lasoo":
            raise MarketplaceError(
                "Deleting marketplace listings is currently only supported for Lasoo stores."
            )
        environment = store.lasoo_environment or Environment.STAGING
        client = LasooClient(store, environment)
        payload = mapper.build_bulk_delete_payload(marketplace_keys, client.auth_key)
        result = client.send("bulk_delete", payload)
        if not result.ok:
            raise MarketplaceError(
                result.message or "Could not delete the listings on the marketplace."
            )

    for listing, row_result in to_delete:
        listing.delete()
        row_result["imported"] = True
        deleted += 1

    error_rows = sum(1 for r in preview if not r["valid"])
    record_activity(
        user, store,
        action=ListingAction.DELETE,
        source=ListingUpload.Source.FILE,
        filename=filename,
        total=len(rows), success=deleted, errors=error_rows,
        rows=preview,
        message=f"Deleted {deleted} listing(s)." if deleted else "",
    )
    return {"total_rows": len(rows), "imported": deleted, "action": ListingAction.DELETE, "rows": preview}


def bulk_import(user, store, filename: str, content: bytes, action: str = "") -> dict:
    """Import listings from a CSV/XLSX template. One action per file:
    Create (new listings), Mapped (already on the store), Delete (SKU only).
    Invalid Create/Mapped rows are saved with validation_failed status so they
    show under the Error filter on Created products."""
    rows = csv_import.parse_upload(filename, content)
    if not rows:
        raise MarketplaceError("No data rows found in the uploaded file.")

    file_action = _resolve_file_action(rows, action)
    if file_action == ListingAction.DELETE:
        return _bulk_delete(user, store, filename, rows)

    environment = store.lasoo_environment or Environment.STAGING
    preview, imported, error_rows = [], 0, 0
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

        _, variant_key = mapper.resolve_keys(row)
        if not variant_key:
            # Nothing to upsert by; keep the row purely in the upload report.
            error_rows += 1
            preview.append(row_result)
            continue

        existing = StoreListing.objects.filter(
            store=store, external_variant_key=variant_key, environment=environment,
        ).first()
        if file_action == ListingAction.CREATE and existing and not errors:
            row_result["valid"] = False
            row_result["errors"] = [
                f'A listing with variant key "{variant_key}" already exists. '
                'Use the Mapped action to update it.'
            ]
            error_rows += 1
            preview.append(row_result)
            continue

        listing = existing or StoreListing(
            user=user, store=store,
            external_variant_key=variant_key,
            external_product_key=variant_key,
            environment=environment,
        )
        listing.action = file_action
        _apply_fields(listing, row)
        _finalize_validation(listing, row)
        listing.save()
        if errors:
            # Persisted with validation_failed status -> Error filter on Created products.
            error_rows += 1
        else:
            imported += 1
            row_result["imported"] = True
        preview.append(row_result)

    record_activity(
        user, store,
        action=file_action,
        source=ListingUpload.Source.FILE,
        filename=filename,
        total=len(rows), success=imported, errors=error_rows,
        rows=preview,
        message=f"Imported {imported} of {len(rows)} row(s).",
    )
    return {"total_rows": len(rows), "imported": imported, "action": file_action, "rows": preview}


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
