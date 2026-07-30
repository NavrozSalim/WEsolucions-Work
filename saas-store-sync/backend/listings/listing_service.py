"""Listing CRUD, validation, bulk import, and publish to the store's marketplace.

Lasoo and Reverb managed stores are supported. Dispatch happens in
``publish()`` / validation helpers by marketplace kind.
"""
import logging
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from store_adapters import get_adapter
from store_adapters.reverb_adapter import ReverbAPIError
from stores.credentials import marketplace_kind

from . import csv_import
from . import template_routing
from .errors import MarketplaceError
from .lasoo import mapper, validator
from .lasoo.client import LasooClient
from .models import (
    Environment,
    InventorySyncStatus,
    ListingAction,
    ListingStatus,
    ListingUpload,
    StoreListing,
)
from .reverb import listings as reverb_listings

logger = logging.getLogger("listings")

# Listings in these statuses exist on the marketplace and need an API call to remove.
ON_MARKETPLACE_STATUSES = (
    ListingStatus.UPLOADED_STAGING,
    ListingStatus.UPLOADED_PRODUCTION,
)

# Single-action rows that belong on Logs, not Upload history.
SYSTEM_LOG_FILENAMES = frozenset({
    'Publish to marketplace',
    'Push inventory to marketplace',
})

HISTORY_ACTIONS = (
    ListingAction.CREATE,
    ListingAction.MAPPED,
    ListingAction.DELETE,
)


def listing_upload_history_q() -> Q:
    """Upload history: bulk Create/Mapped/Delete files + single Create/Delete."""
    return (
        Q(action__in=HISTORY_ACTIONS)
        & Q(source__in=(ListingUpload.Source.FILE, ListingUpload.Source.SINGLE))
        & ~Q(filename__in=SYSTEM_LOG_FILENAMES)
        & ~Q(filename__startswith='Edit ')
    )


def filter_listing_uploads(qs, scope: str = 'history'):
    """Split ListingUpload rows between Upload history and Logs."""
    scope = (scope or 'history').strip().lower()
    history_q = listing_upload_history_q()
    if scope == 'logs':
        return qs.exclude(history_q)
    if scope == 'all':
        return qs
    return qs.filter(history_q)


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
    """Persist an Upload-history row for a file or single-listing change.

    Any row error marks the upload as Failed (shown as Error in the UI).
    All rows OK → Completed (shown as Created).
    """
    if errors:
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


def _vendor_id_from_url(url: str, price_by_vendor_id: dict, inv_by_vendor_id: dict):
    """Best-effort match of a vendor URL to a store-configured vendor.

    Managed listings only store ``vendor_url`` (no vendor FK). Prefer a vendor
    whose code appears in the URL host (e.g. amazon → amazonus); else None so
    callers use the store's first configured settings as fallback.
    """
    if not url:
        return None
    url_l = url.lower()
    vendor_ids = set(price_by_vendor_id) | set(inv_by_vendor_id)
    if not vendor_ids:
        return None
    from vendor.models import Vendor

    vendors = list(Vendor.objects.filter(id__in=vendor_ids).only("id", "code", "name"))
    # Prefer longer codes first so amazonus wins over amazon when both exist.
    vendors.sort(key=lambda v: len((v.code or "")), reverse=True)
    for v in vendors:
        code = (v.code or "").strip().lower()
        name = (v.name or "").strip().lower()
        if code and code in url_l:
            return v.id
        # Host fragments: amazon.com / ebay.com / aliexpress.com
        for token in (code, name):
            if not token:
                continue
            stem = token.replace(" ", "")
            for suffix in ("us", "au", "uk", "ca"):
                if stem.endswith(suffix) and len(stem) > len(suffix):
                    stem = stem[: -len(suffix)]
                    break
            if stem and len(stem) >= 3 and stem in url_l:
                return v.id
    return None


def _store_kind(store) -> str:
    return marketplace_kind(getattr(store, "marketplace", None))


def _listing_env(store) -> str:
    """Reverb has no staging API — always production. Lasoo uses store setting."""
    if _store_kind(store) == "reverb":
        return Environment.PRODUCTION
    return store.lasoo_environment or Environment.STAGING


def _validate_listing(store, data: dict) -> list[str]:
    source_errors = template_routing.validate_source_vendor_for_store(store, data)
    if _store_kind(store) == "reverb":
        return source_errors + reverb_listings.validate_listing(data)
    return source_errors + validator.validate_listing(data)


def _resolve_keys(store, data: dict) -> tuple[str, str]:
    if _store_kind(store) == "reverb":
        return reverb_listings.resolve_keys(data)
    return mapper.resolve_keys(data)


def _apply_fields(listing: StoreListing, data: dict):
    store = listing.store
    product_key, variant_key = _resolve_keys(store, data)
    listing.external_product_key = product_key
    listing.external_variant_key = variant_key
    listing.title = (data.get("title") or "").strip()
    listing.description = (data.get("description") or "").strip()
    # Reverb: brand column stores Make
    make = (data.get("make") or data.get("brand") or "").strip()
    listing.brand = make
    category = (data.get("category_uuid") or data.get("category") or "").strip()
    listing.category = category
    listing.sku = (data.get("sku") or "").strip() or variant_key
    listing.barcode = (data.get("barcode") or data.get("upc") or "").strip()
    if _store_kind(store) != "reverb":
        for i in (1, 2, 3, 4):
            setattr(
                listing,
                f"option_{i}_name",
                str(data.get(f"option_{i}_name") or "").strip()[:100],
            )
            setattr(
                listing,
                f"option_{i}_value",
                str(data.get(f"option_{i}_value") or "").strip()[:255],
            )
        listing.variation_image_url = str(data.get("variation_image_url") or "").strip()[:1000]
        listing.options = mapper.format_options_summary(data)[:500]
    listing.vendor_url = (data.get("vendor_url") or "").strip()[:1000]
    listing.vendor_id = (data.get("vendor_id") or "").strip()[:255]
    source_code = (data.get("source_vendor_code") or data.get("vendor_code") or "").strip()
    if not source_code and (data.get("vendor_name") or "").strip():
        source_code = template_routing.resolve_vendor_code(data.get("vendor_name")) or ""
    listing.source_vendor_code = (source_code or "")[:50]
    if _store_kind(store) == "reverb":
        listing.image_urls = "|".join(
            p for p in str(data.get("image_urls") or "").replace(";", "|").replace("\n", "|").replace(",", "|").split("|")
            if p.strip()
        )
        listing.infinite_quantity = False
        listing.external_data_object_json = reverb_listings.build_extras(data)
    else:
        listing.image_urls = mapper.normalize_image_urls(data.get("image_urls"))
        listing.infinite_quantity = bool(data.get("infinite_quantity"))
    try:
        listing.inventory = 0 if listing.infinite_quantity else int(data.get("inventory") or 0)
    except (TypeError, ValueError):
        listing.inventory = 0
    # Reverb uses a single Price → store as both original + sale
    if _store_kind(store) == "reverb":
        price = data.get("sale_price")
        if price in (None, ""):
            price = data.get("price")
        if price in (None, ""):
            price = data.get("original_price")
        listing.original_price = _safe_decimal(price)
        listing.sale_price = _safe_decimal(price)
    else:
        listing.original_price = _safe_decimal(data.get("original_price"))
        listing.sale_price = _safe_decimal(data.get("sale_price"))


def _uploaded_status(environment: str) -> str:
    return (
        ListingStatus.UPLOADED_PRODUCTION
        if environment == Environment.PRODUCTION
        else ListingStatus.UPLOADED_STAGING
    )


def _finalize_validation(listing: StoreListing, data: dict) -> list[str]:
    store = listing.store
    errors = _validate_listing(store, data)
    if errors:
        listing.validation_errors_json = errors
        listing.status = ListingStatus.VALIDATION_FAILED
        if _store_kind(store) != "reverb":
            listing.external_data_object_json = ""
        listing.original_price_cents = 0
        listing.sale_price_cents = 0
    else:
        listing.validation_errors_json = None
        if _store_kind(store) == "reverb":
            price = data.get("sale_price")
            if price in (None, ""):
                price = data.get("price")
            if price in (None, ""):
                price = data.get("original_price")
            cents = int((_safe_decimal(price) * 100))
            listing.original_price_cents = cents
            listing.sale_price_cents = cents
            listing.external_data_object_json = reverb_listings.build_extras(data)
        else:
            listing.original_price_cents = mapper.dollars_to_cents(data.get("original_price"))
            listing.sale_price_cents = mapper.dollars_to_cents(data.get("sale_price"))
            listing.external_data_object_json = mapper.build_external_data_object(data)
        if listing.action == ListingAction.MAPPED:
            listing.status = _uploaded_status(listing.environment)
        else:
            listing.status = ListingStatus.READY
    return errors


def create(user, store, data: dict, action: str = ListingAction.CREATE) -> StoreListing:
    if action not in (ListingAction.CREATE, ListingAction.MAPPED):
        action = ListingAction.CREATE
    # Same optional routing as bulk: Marketplace Name / Store Name may target
    # another of the user's stores.
    target_store, route_errors = template_routing.resolve_row_store(user, store, data or {})
    if route_errors:
        raise MarketplaceError(" ".join(route_errors))
    store = target_store
    environment = _listing_env(store)
    listing = StoreListing(user=user, store=store, environment=environment, action=action)
    _apply_fields(listing, data)
    _, variant_key = _resolve_keys(store, data)
    if variant_key and StoreListing.objects.filter(
        store=store, external_variant_key=variant_key, environment=environment,
    ).exists():
        raise MarketplaceError(
            f'A created product with SKU/variant "{variant_key}" already exists for this store.'
            + (' Use the Mapped action to update it.' if action == ListingAction.CREATE else '')
        )
    _finalize_validation(listing, data)
    link_errors = _attach_reverb_mapped_listing(listing)
    if link_errors:
        merged = list(listing.validation_errors_json or []) + link_errors
        listing.validation_errors_json = merged
        listing.status = ListingStatus.VALIDATION_FAILED
    listing.save()
    return listing


def update(listing: StoreListing, data: dict) -> StoreListing:
    _apply_fields(listing, data)
    _finalize_validation(listing, data)
    link_errors = _attach_reverb_mapped_listing(listing)
    if link_errors:
        merged = list(listing.validation_errors_json or []) + link_errors
        listing.validation_errors_json = merged
        listing.status = ListingStatus.VALIDATION_FAILED
    listing.save()
    return listing


def _looks_like_reverb_listing_id(value: str) -> bool:
    text = (value or "").strip()
    return len(text) >= 32 and "-" in text


def _resolve_reverb_listing_id(adapter, listing: StoreListing) -> str:
    """Prefer stored Reverb listing UUID; otherwise look up by SKU (live + draft)."""
    lid = (listing.external_product_key or "").strip()
    sku = (listing.sku or listing.external_variant_key or "").strip()
    if lid and _looks_like_reverb_listing_id(lid) and lid != sku:
        return lid
    if sku:
        found = adapter.lookup_listing_by_sku(sku)
        if found:
            return str(found).strip()
    if lid and lid != sku:
        return lid
    return ""


def _attach_reverb_mapped_listing(listing: StoreListing) -> list[str]:
    """For Mapped Reverb rows, resolve and store the live/draft listing id.

    Returns human-readable errors (empty == linked or not applicable).
    """
    store = listing.store
    if listing.action != ListingAction.MAPPED or _store_kind(store) != "reverb":
        return []
    sku = (listing.sku or listing.external_variant_key or "").strip()
    if not sku:
        return ["SKU is required to map a Reverb listing."]
    try:
        adapter = get_adapter(store)
        lid = adapter.lookup_listing_by_sku(sku)
    except ReverbAPIError as exc:
        return [f"Could not look up SKU on Reverb: {exc}"]
    if not lid:
        return [
            f'No listing with SKU "{sku}" was found on Reverb (live or draft). '
            "Mapped is for products already on the marketplace — use Create for new ones."
        ]
    listing.external_product_key = str(lid).strip()
    return []


def _end_listing_on_marketplace(store, listing: StoreListing) -> bool:
    """End/delete the listing on the marketplace if present. Returns True if removed.

    Always attempts a marketplace lookup by id/SKU so leftover drafts from failed
    publishes are cleaned up. Missing marketplace rows are treated as success.
    """
    kind = marketplace_kind(store.marketplace)
    if kind == "lasoo":
        environment = listing.environment or store.lasoo_environment or Environment.STAGING
        client = LasooClient(store, environment)
        key = listing.external_variant_key or listing.sku
        if not key:
            return False
        payload = mapper.build_bulk_delete_payload([key], client.auth_key)
        result = client.send("bulk_delete", payload)
        if not result.ok:
            msg = (result.message or "").lower()
            if "not found" in msg or "no variant" in msg:
                return False
            raise MarketplaceError(
                result.message or "Could not delete the listing on the marketplace."
            )
        return True
    if kind == "reverb":
        adapter = get_adapter(store)
        try:
            lid = _resolve_reverb_listing_id(adapter, listing)
        except ReverbAPIError as exc:
            raise MarketplaceError(str(exc) or "Could not look up Reverb listing.") from exc
        if not lid:
            return False
        try:
            adapter.delete_product(lid)
            return True
        except ReverbAPIError as exc:
            code = getattr(exc, "status_code", None)
            msg = str(exc or "").lower()
            if code == 404 or "not found" in msg or "no longer" in msg:
                return False
            raise MarketplaceError(
                str(exc) or "Could not end the listing on Reverb."
            ) from exc
    if kind == "mydeal":
        return _end_mydeal_listing(store, listing)
    # Unsupported marketplace — local-only delete.
    return False


def _end_mydeal_listing(store, listing: StoreListing) -> bool:
    """Set MyDeal ListingStatus=NotLive via Universal API (API-mode stores only)."""
    method = (getattr(store, "mydeal_setup_method", None) or "upload").strip().lower()
    if method != "api":
        # Upload-template stores have no live product API to call.
        return False
    sku = (listing.sku or listing.external_variant_key or "").strip()
    if not sku:
        return False
    ext = (listing.external_product_key or "").strip()
    # Prefer stored product key; fall back to SKU (standalone ProductSKU key mode).
    if not ext or ext == listing.external_variant_key:
        ext = sku
    try:
        from .mydeal.client import MyDealClient

        client = MyDealClient(store)
        result = client.end_listing(sku=sku, external_product_id=ext)
    except MarketplaceError as exc:
        msg = str(exc or "").lower()
        if "not configured" in msg or "no sandbox" in msg or "no production" in msg:
            return False
        raise
    if result.ok:
        return True
    msg = (result.message or "").lower()
    status = getattr(result, "status", 0) or 0
    if status == 404 or "not found" in msg or "does not exist" in msg or "no product" in msg:
        return False
    raise MarketplaceError(result.message or "Could not end the MyDeal listing.")


def delete(user, store, listing: StoreListing) -> dict:
    """Delete a listing from the marketplace (if present) and then from this app.

    Live behavior: one delete removes both sides. If the SKU is not on the
    marketplace, local delete still proceeds.
    """
    marketplace_deleted = False
    kind = marketplace_kind(store.marketplace)
    if kind in ("lasoo", "reverb", "mydeal"):
        marketplace_deleted = _end_listing_on_marketplace(store, listing)
    variant_key = listing.external_variant_key
    listing.delete()
    return {
        "ok": True,
        "variant_key": variant_key,
        "marketplace_deleted": marketplace_deleted,
    }


def _keys_from_upload_rows(upload: ListingUpload) -> list[str]:
    """Collect SKU / variant keys referenced by an upload history row."""
    keys: list[str] = []
    seen: set[str] = set()
    rows = upload.rows_json if isinstance(upload.rows_json, list) else []
    for r in rows:
        for field in ("sku", "variant_key"):
            val = (r.get(field) or "").strip() if isinstance(r, dict) else ""
            if val and val not in seen:
                seen.add(val)
                keys.append(val)
    return keys


def _delete_listings_marketplace(store, listings: list) -> int:
    """Remove listings from Lasoo/Reverb/MyDeal. Returns marketplace removals count."""
    if not listings:
        return 0
    kind = marketplace_kind(store.marketplace)
    if kind == "lasoo":
        keys = [
            (listing.external_variant_key or listing.sku or "").strip()
            for listing in listings
        ]
        keys = [k for k in keys if k]
        if not keys:
            return 0
        environment = (
            listings[0].environment
            or store.lasoo_environment
            or Environment.STAGING
        )
        client = LasooClient(store, environment)
        payload = mapper.build_bulk_delete_payload(keys, client.auth_key)
        result = client.send("bulk_delete", payload)
        if not result.ok:
            msg = (result.message or "").lower()
            if "not found" in msg or "no variant" in msg:
                return 0
            raise MarketplaceError(
                result.message or "Could not delete the listings on the marketplace."
            )
        return len(keys)
    if kind == "mydeal":
        method = (getattr(store, "mydeal_setup_method", None) or "upload").strip().lower()
        if method != "api":
            return 0
        items = []
        for listing in listings:
            sku = (listing.sku or listing.external_variant_key or "").strip()
            if not sku:
                continue
            ext = (listing.external_product_key or "").strip()
            if not ext or ext == listing.external_variant_key:
                ext = sku
            items.append({"sku": sku, "external_product_id": ext})
        if not items:
            return 0
        try:
            from .mydeal.client import MyDealClient

            client = MyDealClient(store)
            result = client.end_listings(items)
        except MarketplaceError as exc:
            msg = str(exc or "").lower()
            if "not configured" in msg:
                return 0
            raise
        if result.ok:
            return len(items)
        msg = (result.message or "").lower()
        status = getattr(result, "status", 0) or 0
        if status == 404 or "not found" in msg or "does not exist" in msg:
            return 0
        raise MarketplaceError(result.message or "Could not end MyDeal listings.")
    removed = 0
    for listing in listings:
        if _end_listing_on_marketplace(store, listing):
            removed += 1
    return removed


def delete_upload(
    user,
    store,
    upload: ListingUpload,
    *,
    delete_system: bool = False,
    delete_marketplace: bool = False,
) -> dict:
    """Delete an Upload history entry and its listings from app + marketplace.

    Live behavior: deleting an upload always removes matching listings from this
    app and ends them on the marketplace when present. ``delete_system`` /
    ``delete_marketplace`` are accepted for API compatibility but both mean
    full live delete when either is true; history-only when both are false.
    """
    live_delete = bool(delete_system or delete_marketplace)

    listings_deleted = 0
    marketplace_deleted = 0
    if live_delete:
        keys = _keys_from_upload_rows(upload)
        listings = []
        if keys:
            listings = list(
                StoreListing.objects.filter(store=store, user=user).filter(
                    Q(sku__in=keys) | Q(external_variant_key__in=keys)
                )
            )
        if listings:
            marketplace_deleted = _delete_listings_marketplace(store, listings)
        listings_deleted = len(listings)
        for listing in listings:
            listing.delete()

    upload_id = str(upload.id)
    upload.delete()
    return {
        "ok": True,
        "upload_id": upload_id,
        "listings_deleted": listings_deleted,
        "marketplace_deleted": marketplace_deleted,
        "delete_system": live_delete,
        "delete_marketplace": live_delete,
    }


def _listing_to_data(listing: StoreListing) -> dict:
    if _store_kind(listing.store) == "reverb":
        return reverb_listings.listing_to_data(listing)
    return {
        "product_key": listing.external_product_key,
        "variant_key": listing.external_variant_key,
        "title": listing.title,
        "description": listing.description,
        "brand": listing.brand,
        "category": listing.category,
        "sku": listing.sku,
        "barcode": listing.barcode,
        "options": listing.options,
        "option_1_name": listing.option_1_name,
        "option_1_value": listing.option_1_value,
        "option_2_name": listing.option_2_name,
        "option_2_value": listing.option_2_value,
        "option_3_name": listing.option_3_name,
        "option_3_value": listing.option_3_value,
        "option_4_name": listing.option_4_name,
        "option_4_value": listing.option_4_value,
        "variation_image_url": listing.variation_image_url,
        "vendor_url": listing.vendor_url,
        "vendor_id": listing.vendor_id,
        "source_vendor_code": listing.source_vendor_code,
        "vendor_name": listing.source_vendor_code,
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
    """Delete listings by SKU: end on marketplace when present, then locally."""
    preview, deleted = [], 0
    to_delete = []
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
        preview.append(row_result)

    for listing, row_result in to_delete:
        try:
            delete(user, store, listing)
        except MarketplaceError as exc:
            row_result["errors"] = [str(exc)]
            row_result["valid"] = False
            continue
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
    show under the Error filter on Created products.

    Optional template columns Vendor Name / Marketplace Name / Store Name are
    validated and used for routing (Store Name may target another of the user's
    stores).
    """
    rows = csv_import.parse_upload(filename, content)
    if not rows:
        raise MarketplaceError("No data rows found in the uploaded file.")

    file_action = _resolve_file_action(rows, action)
    if file_action == ListingAction.DELETE:
        return _bulk_delete(user, store, filename, rows)

    preview, imported, error_rows = [], 0, 0
    # Activity is recorded against the UI store; rows may land on other stores.
    activity_store = store
    for row in rows:
        target_store, route_errors = template_routing.resolve_row_store(user, store, row)
        field_errors = _validate_listing(target_store, row)
        errors = list(route_errors) + list(field_errors)
        row_result = {
            "row_number": row.get("row_number"),
            "sku": row.get("sku", ""),
            "variant_key": row.get("variant_key") or row.get("sku", ""),
            "store_name": getattr(target_store, "name", "") or "",
            "errors": errors,
            "valid": not errors,
            "imported": False,
            # Full input row for export (template columns + Status).
            "fields": csv_import.snapshot_row_fields({
                **row,
                "store_name": row.get("store_name") or getattr(target_store, "name", "") or "",
                "marketplace_name": row.get("marketplace_name") or (
                    getattr(getattr(target_store, "marketplace", None), "name", None) or ""
                ),
                "action": file_action,
            }),
        }

        environment = _listing_env(target_store)
        _, variant_key = _resolve_keys(target_store, row)
        if not variant_key:
            # Nothing to upsert by; keep the row purely in the upload report.
            error_rows += 1
            preview.append(row_result)
            continue

        existing = StoreListing.objects.filter(
            store=target_store, external_variant_key=variant_key, environment=environment,
        ).first()
        if file_action == ListingAction.CREATE and existing and not errors:
            row_result["valid"] = False
            row_result["errors"] = [
                f'A listing with SKU "{variant_key}" already exists. '
                'Use the Mapped action to update it.'
            ]
            error_rows += 1
            preview.append(row_result)
            continue

        listing = existing or StoreListing(
            user=user, store=target_store,
            external_variant_key=variant_key,
            external_product_key=variant_key,
            environment=environment,
        )
        listing.action = file_action
        _apply_fields(listing, row)
        _finalize_validation(listing, row)
        link_errors = _attach_reverb_mapped_listing(listing)
        if link_errors:
            merged = list(listing.validation_errors_json or []) + link_errors
            listing.validation_errors_json = merged
            listing.status = ListingStatus.VALIDATION_FAILED
            errors = list(errors) + link_errors
            row_result["errors"] = errors
            row_result["valid"] = False
        if route_errors:
            merged = list(route_errors) + list(listing.validation_errors_json or [])
            listing.validation_errors_json = merged
            listing.status = ListingStatus.VALIDATION_FAILED
        listing.save()
        if errors:
            # Persisted with validation_failed status -> Error filter on Created products.
            error_rows += 1
        else:
            imported += 1
            row_result["imported"] = True
        preview.append(row_result)

    record_activity(
        user, activity_store,
        action=file_action,
        source=ListingUpload.Source.FILE,
        filename=filename,
        total=len(rows), success=imported, errors=error_rows,
        rows=preview,
        message=f"Imported {imported} of {len(rows)} row(s).",
    )
    return {"total_rows": len(rows), "imported": imported, "action": file_action, "rows": preview}


def _collect_publishable(store, listings: list) -> list:
    """Re-validate rows so stale payload data can't be pushed."""
    publishable = []
    for listing in listings:
        data = _listing_to_data(listing)
        errors = _validate_listing(store, data)
        if errors:
            listing.validation_errors_json = errors
            listing.status = ListingStatus.VALIDATION_FAILED
            listing.save(update_fields=["validation_errors_json", "status", "updated_at"])
        else:
            publishable.append(listing)
    return publishable


def _publish_lasoo(user, store, publishable: list) -> dict:
    environment = store.lasoo_environment or Environment.STAGING
    client = LasooClient(store, environment)
    variants = [_listing_to_data(l) for l in publishable]
    payload = mapper.build_bulk_upsert_payload(variants, client.auth_key)
    result = client.send("bulk_upsert", payload)

    now = timezone.now()
    request_for_storage = {**payload, "auth": "***"}  # never persist the raw key
    new_status = (
        _uploaded_status(environment) if result.ok else ListingStatus.FAILED
    )
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


def _publish_reverb(user, store, publishable: list) -> dict:
    """Create each listing on Reverb via POST /api/listings (one call per row)."""
    adapter = get_adapter(store)
    environment = Environment.PRODUCTION
    now = timezone.now()
    published = 0
    failures = 0

    for listing in publishable:
        data = _listing_to_data(listing)
        condition_raw = data.get("condition_uuid") or data.get("condition") or ""
        condition_uuid = reverb_listings.resolve_condition_uuid(adapter, condition_raw)
        if not condition_uuid:
            listing.validation_errors_json = [
                f'Could not resolve Reverb condition "{condition_raw}". '
                "Use a condition name like Brand New / Excellent, or pick from the dropdown."
            ]
            listing.status = ListingStatus.VALIDATION_FAILED
            listing.save(update_fields=["validation_errors_json", "status", "updated_at"])
            failures += 1
            continue

        category_raw = data.get("category_uuid") or data.get("category") or ""
        category_uuid = reverb_listings.resolve_category_uuid(adapter, category_raw)
        if not category_uuid:
            listing.validation_errors_json = [
                f'Could not resolve Reverb category "{category_raw}". '
                "Use the exact Reverb category name (e.g. Accessories / Cables) or pick from the dropdown."
            ]
            listing.status = ListingStatus.VALIDATION_FAILED
            listing.save(update_fields=["validation_errors_json", "status", "updated_at"])
            failures += 1
            continue

        payload = reverb_listings.build_create_payload(
            data,
            condition_uuid=condition_uuid,
            category_uuid=category_uuid,
        )
        try:
            response = adapter.create_listing(payload)
            listing_id = ""
            if isinstance(response, dict):
                listing_id = str(
                    response.get("id")
                    or (response.get("listing") or {}).get("id")
                    or ""
                ).strip()
            listing.status = ListingStatus.UPLOADED_PRODUCTION
            listing.marketplace_request_json = payload
            listing.marketplace_response_json = response
            listing.last_uploaded_at = now
            if listing_id:
                # Keep SKU as variant key for local upserts; store Reverb id on product key.
                listing.external_product_key = listing_id
            listing.save(
                update_fields=[
                    "status",
                    "external_product_key",
                    "marketplace_request_json",
                    "marketplace_response_json",
                    "last_uploaded_at",
                    "updated_at",
                ]
            )
            published += 1
        except ReverbAPIError as exc:
            listing.status = ListingStatus.FAILED
            listing.marketplace_request_json = payload
            listing.marketplace_response_json = {"error": str(exc)}
            listing.save(
                update_fields=[
                    "status",
                    "marketplace_request_json",
                    "marketplace_response_json",
                    "updated_at",
                ]
            )
            failures += 1
            logger.warning("Reverb publish failed for SKU %s: %s", listing.sku, exc)

    ok = published > 0 and failures == 0
    if published and failures:
        message = f"Published {published} listing(s); {failures} failed."
    elif published:
        message = f"Published {published} listing(s) to Reverb."
    else:
        message = "Could not publish any listings to Reverb. Check validation errors."
    return {
        "ok": ok,
        "message": message,
        "published": published,
        "environment": environment,
    }


def publish(user, store, listing_ids=None) -> dict:
    """Push READY (or previously uploaded) listings to the store's marketplace."""
    kind = marketplace_kind(store.marketplace)
    if kind not in ("lasoo", "reverb"):
        raise MarketplaceError(
            f'Publishing created products is not supported yet for "{kind or "this marketplace"}". '
            "Currently only Lasoo and Reverb stores can publish."
        )

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

    publishable = _collect_publishable(store, listings)
    if not publishable:
        raise MarketplaceError("All selected listings failed validation. Fix the errors and retry.")

    # Reverb: one API call per listing — do not wrap in a single atomic block
    # (partial remote success must still be persisted locally).
    if kind == "reverb":
        return _publish_reverb(user, store, publishable)
    with transaction.atomic():
        return _publish_lasoo(user, store, publishable)


def _scrapeable_listings_qs(user, store, listing_ids=None):
    """Base queryset for managed inventory scrapes."""
    qs = StoreListing.objects.filter(user=user, store=store)
    if listing_ids:
        qs = qs.filter(id__in=listing_ids)
    else:
        qs = qs.filter(
            status__in=[
                ListingStatus.UPLOADED_STAGING,
                ListingStatus.UPLOADED_PRODUCTION,
                ListingStatus.READY,
                ListingStatus.FAILED,
            ]
        )
    return qs


def _estimate_scrape_total(user, store, listing_ids=None) -> int:
    """Count rows that will be scraped (Vendor URL and/or Nora Vendor ID).

    Used so the progress bar shows X/Y as soon as scrape is queued.
    """
    from stores.nora import load_store_nora_stock_map

    qs = _scrapeable_listings_qs(user, store, listing_ids)
    nora_map = None
    try:
        nora_map = load_store_nora_stock_map(store)
    except Exception:  # noqa: BLE001
        nora_map = None

    total = 0
    for listing in qs.only("id", "vendor_url", "vendor_id"):
        has_url = bool((listing.vendor_url or "").strip())
        has_nora_id = bool((listing.vendor_id or "").strip()) and nora_map is not None
        if has_url or has_nora_id:
            total += 1
    return total


def start_scrape_async(user, store, listing_ids=None) -> dict:
    """Start a managed-listing scrape in a background thread.

    Progress is stored in cache and live counts are derived from StoreListing
    statuses (pending → scraped/failed), matching catalog Inventory management.
    """
    import threading

    from . import scrape_progress as scrape_prog

    existing = scrape_prog.get_scrape_progress(store.id)
    if existing.get("active"):
        live = scrape_prog.enrich_progress_from_listings(store.id)
        return {
            "started": False,
            "already_running": True,
            "ok": True,
            "message": "Scrape already running. Progress continues on the server.",
            **{k: live.get(k) for k in ("total", "processed", "scraped", "failed", "pct", "phase", "message")},
        }

    ids = list(listing_ids) if listing_ids else None

    from stores.nora import load_store_nora_stock_map

    qs = _scrapeable_listings_qs(user, store, ids)
    nora_map = None
    try:
        nora_map = load_store_nora_stock_map(store)
    except Exception:  # noqa: BLE001
        nora_map = None

    batch = []
    for listing in qs.only("id", "vendor_url", "vendor_id"):
        has_url = bool((listing.vendor_url or "").strip())
        has_nora_id = bool((listing.vendor_id or "").strip()) and nora_map is not None
        if has_url or has_nora_id:
            batch.append(listing)

    if not batch:
        raise MarketplaceError(
            "No listings with a Vendor URL (or Nora Vendor ID) to scrape. "
            "Add a vendor link / Vendor ID on each listing first."
        )

    batch_ids = [l.id for l in batch]
    total = len(batch_ids)
    StoreListing.objects.filter(id__in=batch_ids).update(
        inventory_sync_status=InventorySyncStatus.PENDING,
        last_scrape_error="",
    )
    scrape_prog.begin_scrape_progress(
        store.id,
        total=total,
        listing_ids=batch_ids,
        phase="running",
        message=f"Scraping 0 of {total}…",
    )

    user_id = user.id
    store_id = str(store.id)
    id_strs = [str(x) for x in batch_ids]

    def _run():
        from django.contrib.auth import get_user_model
        from stores.models import Store

        User = get_user_model()
        try:
            u = User.objects.get(pk=user_id)
            s = Store.objects.select_related("marketplace", "user").get(pk=store_id)
            scrape_listings(u, s, id_strs)
        except MarketplaceError as err:
            scrape_prog.finish_scrape_progress(store_id, message=str(err)[:200])
        except Exception as err:  # noqa: BLE001
            logger.exception("Managed scrape failed store=%s", store_id)
            scrape_prog.finish_scrape_progress(
                store_id,
                message=(str(err) or "Scrape failed.")[:200],
            )
        finally:
            scrape_prog.enrich_progress_from_listings(store_id)

    threading.Thread(target=_run, daemon=True, name=f"listing-scrape-{store_id}").start()

    return {
        "started": True,
        "async": True,
        "via": "thread",
        "ok": True,
        "total": total,
        "processed": 0,
        "message": f"Scrape started for {total} listing(s). Progress updates as each listing finishes.",
    }


def scrape_listings(user, store, listing_ids=None) -> dict:
    """Scrape vendor URLs on managed listings; Nora stock overrides when configured.

    Price comes from Vendor URL scrape (e.g. eBay AU). Inventory comes from the
    Nora Excel map when the store has Nora Inventory uploaded and the listing
    has a Vendor ID — unmatched Vendor IDs get stock 0.
    """
    from scrapers import close_amazon_session, get_price_and_stock
    from stores.nora import (
        get_nora_inventory_settings,
        load_store_nora_stock_map,
        lookup_nora_stock,
    )
    from sync.tasks import (
        _apply_inventory,
        _apply_pricing,
        _build_store_vendor_pricing_inventory_caches,
        _get_inventory_for_vendor_from_cache,
        _get_pricing_for_vendor_from_cache,
    )

    qs = _scrapeable_listings_qs(user, store, listing_ids)

    nora_map = None
    nora_vendor_pk = None
    try:
        nora_map = load_store_nora_stock_map(store)
        nora_inv = get_nora_inventory_settings(store)
        if nora_inv is not None:
            nora_vendor_pk = nora_inv.vendor_id
    except Exception as nora_err:
        logger.warning("Nora map unavailable for managed scrape store=%s: %s", store.id, nora_err)
        nora_map = None

    listings = []
    for listing in qs:
        has_url = bool((listing.vendor_url or "").strip())
        has_nora_id = bool((listing.vendor_id or "").strip()) and nora_map is not None
        if has_url or has_nora_id:
            listings.append(listing)

    if not listings:
        raise MarketplaceError(
            "No listings with a Vendor URL (or Nora Vendor ID) to scrape. "
            "Add a vendor link / Vendor ID on each listing first."
        )

    from . import scrape_progress as scrape_prog

    total = len(listings)
    listing_ids_batch = [l.id for l in listings]
    # Mark batch pending so the inventory table / progress bar move with each row.
    StoreListing.objects.filter(id__in=listing_ids_batch).update(
        inventory_sync_status=InventorySyncStatus.PENDING,
        last_scrape_error="",
    )
    scrape_prog.begin_scrape_progress(
        store.id,
        total=total,
        listing_ids=listing_ids_batch,
        phase="running",
        message=f"Scraping 0 of {total}…",
    )

    region = (getattr(store, "region", None) or "USA").strip() or "USA"
    price_by_vid, price_fb, inv_by_vid, inv_fb = _build_store_vendor_pricing_inventory_caches(store)
    session = {}
    scraped = 0
    failed = 0
    rows = []
    now = timezone.now()
    try:
        try:
            for idx, listing in enumerate(listings):
                scrape_prog.set_scrape_progress(
                    store.id,
                    total=total,
                    processed=idx,
                    scraped=scraped,
                    failed=failed,
                    phase="running",
                    current_sku=(listing.sku or listing.external_variant_key or "")[:80],
                    message=f"Scraping {idx + 1} of {total}…",
                )
                url = (listing.vendor_url or "").strip()
                nora_key = (listing.vendor_id or "").strip()
                src_code = (listing.source_vendor_code or "").strip()
                from scrapers.nora_au_ingest import is_nora_vendor_code
                from .template_routing import is_nora_like

                explicit_nora = is_nora_like(src_code) or is_nora_vendor_code(src_code)
                # vendor_id alone still means Nora when no source vendor was set (legacy rows)
                uses_nora = nora_map is not None and bool(nora_key) and (
                    explicit_nora or not src_code
                )
                row = {
                    "id": str(listing.id),
                    "sku": listing.sku or listing.external_variant_key,
                    "vendor_url": url,
                    "vendor_id": nora_key,
                    "ok": False,
                    "vendor_price": None,
                    "price": None,
                    "inventory": None,
                    "error": "",
                }
                result = {}
                price = None
                stock = None
                if url:
                    try:
                        result = get_price_and_stock(
                            url, region, session, vendor_code=src_code or None,
                        ) or {}
                    except Exception as exc:  # noqa: BLE001
                        if not uses_nora:
                            listing.inventory_sync_status = InventorySyncStatus.FAILED
                            listing.last_scrape_at = now
                            listing.last_scrape_error = (str(exc) or "Scrape failed.")[:500]
                            listing.save(
                                update_fields=[
                                    "inventory_sync_status",
                                    "last_scrape_at",
                                    "last_scrape_error",
                                    "updated_at",
                                ]
                            )
                            row["error"] = listing.last_scrape_error
                            failed += 1
                            rows.append(row)
                            scrape_prog.set_scrape_progress(
                                store.id,
                                processed=idx + 1,
                                scraped=scraped,
                                failed=failed,
                                current_sku=row["sku"] or "",
                            )
                            logger.warning("Listing scrape failed SKU %s: %s", listing.sku, exc)
                            continue
                        logger.warning(
                            "Listing scrape failed SKU %s (Nora stock still applied): %s",
                            listing.sku,
                            exc,
                        )
                        result = {}

                    if result.get("ingest_only") and not uses_nora:
                        listing.inventory_sync_status = InventorySyncStatus.FAILED
                        listing.last_scrape_at = now
                        listing.last_scrape_error = (
                            "This vendor is ingest-only and cannot be scraped server-side."
                        )
                        listing.save(
                            update_fields=[
                                "inventory_sync_status",
                                "last_scrape_at",
                                "last_scrape_error",
                                "updated_at",
                            ]
                        )
                        row["error"] = listing.last_scrape_error
                        failed += 1
                        rows.append(row)
                        scrape_prog.set_scrape_progress(
                            store.id,
                            processed=idx + 1,
                            scraped=scraped,
                            failed=failed,
                            current_sku=row["sku"] or "",
                        )
                        continue

                    price = result.get("price")
                    stock = result.get("stock")
                    if result.get("inventory") is not None and stock is None:
                        stock = result.get("inventory")

                if uses_nora:
                    stock = lookup_nora_stock(nora_map, nora_key) or 0

                err = result.get("error_message") or result.get("error") or ""
                if price is None and stock is None and not uses_nora:
                    listing.inventory_sync_status = InventorySyncStatus.FAILED
                    listing.last_scrape_at = now
                    listing.last_scrape_error = (str(err) or "No price or stock returned.")[:500]
                    listing.save(
                        update_fields=[
                            "inventory_sync_status",
                            "last_scrape_at",
                            "last_scrape_error",
                            "updated_at",
                        ]
                    )
                    row["error"] = listing.last_scrape_error
                    failed += 1
                    rows.append(row)
                    scrape_prog.set_scrape_progress(
                        store.id,
                        processed=idx + 1,
                        scraped=scraped,
                        failed=failed,
                        current_sku=row["sku"] or "",
                    )
                    continue

                vendor_id = _vendor_id_from_url(url, price_by_vid, inv_by_vid)
                pricing = _get_pricing_for_vendor_from_cache(vendor_id, price_by_vid, price_fb)
                inventory_settings = (
                    _get_inventory_for_vendor_from_cache(nora_vendor_pk, inv_by_vid, inv_fb)
                    if (uses_nora and nora_vendor_pk)
                    else _get_inventory_for_vendor_from_cache(vendor_id, inv_by_vid, inv_fb)
                )

                update_fields = [
                    "updated_at",
                    "inventory_sync_status",
                    "last_scrape_at",
                    "last_scrape_error",
                ]
                listing.last_scrape_at = now
                listing.last_scrape_error = ""
                listing.inventory_sync_status = InventorySyncStatus.SCRAPED

                if price is not None:
                    vp = _safe_decimal(price)
                    listing.vendor_price = vp
                    priced = _apply_pricing(vp, pricing)
                    if priced is None:
                        priced = vp
                    listing.sale_price = priced
                    listing.original_price = priced
                    cents = int(priced * 100)
                    listing.sale_price_cents = cents
                    listing.original_price_cents = cents
                    update_fields.extend(
                        [
                            "vendor_price",
                            "sale_price",
                            "original_price",
                            "sale_price_cents",
                            "original_price_cents",
                        ]
                    )
                    row["vendor_price"] = float(vp)
                    row["price"] = float(priced)
                elif uses_nora and price is None:
                    listing.last_scrape_error = (
                        "Nora stock applied; eBay price unavailable"
                        + (f" ({url[:80]})" if url else " (no Vendor URL)")
                    )[:500]

                if stock is not None:
                    try:
                        raw_stock = max(0, int(stock))
                    except (TypeError, ValueError):
                        raw_stock = 0
                    listing.inventory = int(_apply_inventory(raw_stock, inventory_settings))
                    listing.infinite_quantity = False
                    update_fields.extend(["inventory", "infinite_quantity"])
                    row["inventory"] = listing.inventory

                listing.save(update_fields=list(dict.fromkeys(update_fields)))
                row["ok"] = True
                scraped += 1
                rows.append(row)
                scrape_prog.set_scrape_progress(
                    store.id,
                    processed=idx + 1,
                    scraped=scraped,
                    failed=failed,
                    current_sku=row["sku"] or "",
                    message=f"Scraped {scraped} of {total}…",
                )
        except Exception as scrape_exc:  # noqa: BLE001
            scrape_prog.finish_scrape_progress(
                store.id,
                scraped=scraped,
                failed=failed + max(0, total - scraped - failed),
                message=(str(scrape_exc) or "Scrape failed.")[:200],
            )
            raise
    finally:
        try:
            close_amazon_session(session)
        except Exception:  # noqa: BLE001
            pass

    # Scrape only — leave status Scraped. Manual sync / schedule pushes to marketplace
    # (same flow as Reverb managed inventory).
    msg = (
        f"Scraped {scraped} listing(s)"
        + (f"; {failed} failed" if failed else "")
        + ". Use Manual sync or your schedule to push price/stock to the marketplace."
    )
    scrape_prog.finish_scrape_progress(
        store.id,
        scraped=scraped,
        failed=failed,
        message=msg,
    )
    return {
        "ok": scraped > 0,
        "message": msg,
        "scraped": scraped,
        "failed": failed,
        "pushed": 0,
        "rows": rows,
    }



def push_inventory(user, store, listing_ids=None) -> dict:
    """Push local price/stock to the marketplace (Manual sync for managed stores)."""
    kind = marketplace_kind(store.marketplace)
    if kind == "lasoo":
        qs = StoreListing.objects.filter(
            user=user,
            store=store,
            status__in=[
                ListingStatus.UPLOADED_STAGING,
                ListingStatus.UPLOADED_PRODUCTION,
            ],
        )
        if listing_ids:
            qs = qs.filter(id__in=listing_ids)
        listings = list(qs)
        if not listings:
            raise MarketplaceError(
                "No marketplace listings to push. Publish from Created products first."
            )
        pub = _publish_lasoo(user, store, listings)
        if pub.get("ok"):
            StoreListing.objects.filter(id__in=[l.id for l in listings]).update(
                inventory_sync_status=InventorySyncStatus.SYNCED,
            )
        return {
            "ok": bool(pub.get("ok")),
            "message": pub.get("message") or f"Pushed {len(listings)} listing(s) to Lasoo.",
            "pushed": pub.get("published") or (len(listings) if pub.get("ok") else 0),
            "failed": 0 if pub.get("ok") else len(listings),
            "rows": [],
        }
    if kind != "reverb":
        raise MarketplaceError(
            "Inventory push is currently supported for Reverb and Lasoo managed stores."
        )

    qs = StoreListing.objects.filter(
        user=user,
        store=store,
        status__in=[
            ListingStatus.UPLOADED_STAGING,
            ListingStatus.UPLOADED_PRODUCTION,
        ],
    )
    if listing_ids:
        qs = qs.filter(id__in=listing_ids)
    listings = list(qs)
    if not listings:
        raise MarketplaceError(
            "No marketplace listings to push. Publish from Created products first."
        )

    adapter = get_adapter(store)
    pushed = 0
    failed = 0
    rows = []
    now = timezone.now()
    for listing in listings:
        row = {
            "id": str(listing.id),
            "sku": listing.sku or listing.external_variant_key,
            "ok": False,
            "error": "",
        }
        listing_id = (listing.external_product_key or "").strip()
        if not listing_id or listing_id == listing.sku:
            try:
                listing_id = (
                    adapter.lookup_listing_by_sku(listing.sku or listing.external_variant_key)
                    or listing_id
                )
            except ReverbAPIError as exc:
                row["error"] = str(exc) or "Could not look up Reverb listing."
                failed += 1
                rows.append(row)
                continue
        if not listing_id:
            row["error"] = "No Reverb listing id. Publish the listing first."
            failed += 1
            rows.append(row)
            continue

        extras = reverb_listings.parse_extras(listing)
        currency = extras.get("currency") or "USD"
        price = listing.sale_price if listing.sale_price not in (None, Decimal("0")) else listing.original_price
        try:
            adapter.update_product(
                listing_id,
                price=price,
                stock=listing.inventory,
                currency=currency,
            )
            listing.external_product_key = listing_id
            listing.last_uploaded_at = now
            listing.inventory_sync_status = InventorySyncStatus.SYNCED
            listing.save(
                update_fields=[
                    "external_product_key",
                    "last_uploaded_at",
                    "inventory_sync_status",
                    "updated_at",
                ]
            )
            row["ok"] = True
            pushed += 1
        except ReverbAPIError as exc:
            row["error"] = str(exc) or "Reverb update failed."
            listing.inventory_sync_status = InventorySyncStatus.FAILED
            listing.last_scrape_error = row["error"][:500]
            listing.save(update_fields=["inventory_sync_status", "last_scrape_error", "updated_at"])
            failed += 1
            logger.warning("Reverb inventory push failed SKU %s: %s", listing.sku, exc)
        rows.append(row)

    return {
        "ok": pushed > 0 and failed == 0,
        "message": (
            f"Pushed price/stock for {pushed} listing(s)"
            + (f"; {failed} failed." if failed else " to Reverb.")
        ),
        "pushed": pushed,
        "failed": failed,
        "rows": rows,
    }


def reset_inventory_status(user, store, scope: str = "failed") -> dict:
    """Reset inventory_sync_status to pending so Start Scraping can run again."""
    scope = (scope or "failed").strip().lower()
    qs = StoreListing.objects.filter(
        user=user,
        store=store,
        status__in=[
            ListingStatus.UPLOADED_STAGING,
            ListingStatus.UPLOADED_PRODUCTION,
            ListingStatus.READY,
            ListingStatus.FAILED,
        ],
    )
    if scope == "failed":
        qs = qs.filter(inventory_sync_status=InventorySyncStatus.FAILED)
    elif scope == "scraped":
        qs = qs.filter(inventory_sync_status=InventorySyncStatus.SCRAPED)
    elif scope == "all":
        pass
    else:
        raise MarketplaceError('Scope must be "failed", "scraped", or "all".')

    updated = qs.update(
        inventory_sync_status=InventorySyncStatus.PENDING,
        last_scrape_error="",
    )
    return {
        "ok": True,
        "message": f"Reset {updated} listing(s) to Pending.",
        "updated": updated,
        "scope": scope,
    }


def critical_zero_inventory(user, store) -> dict:
    """Set stock to 0 on all marketplace listings and push (Reverb / Lasoo)."""
    qs = StoreListing.objects.filter(
        user=user,
        store=store,
        status__in=[
            ListingStatus.UPLOADED_STAGING,
            ListingStatus.UPLOADED_PRODUCTION,
        ],
    )
    listings = list(qs)
    if not listings:
        raise MarketplaceError("No marketplace listings to zero.")

    for listing in listings:
        listing.inventory = 0
        listing.infinite_quantity = False
        listing.save(update_fields=["inventory", "infinite_quantity", "updated_at"])

    push_result = push_inventory(user, store, listing_ids=[str(l.id) for l in listings])
    return {
        "ok": push_result.get("ok"),
        "message": (
            f"Set stock to 0 on {len(listings)} listing(s). "
            + (push_result.get("message") or "")
        ),
        "zeroed": len(listings),
        "pushed": push_result.get("pushed", 0),
        "failed": push_result.get("failed", 0),
        "rows": push_result.get("rows") or [],
    }


def export_inventory_xlsx(user, store, sync_status: str = "") -> bytes:
    """Excel export of managed inventory listings."""
    from openpyxl import Workbook

    qs = StoreListing.objects.filter(
        user=user,
        store=store,
        status__in=[
            ListingStatus.UPLOADED_STAGING,
            ListingStatus.UPLOADED_PRODUCTION,
        ],
    ).order_by("sku")
    sync_status = (sync_status or "").strip().lower()
    if sync_status in ("pending", "scraped", "synced", "failed"):
        qs = qs.filter(inventory_sync_status=sync_status)

    wb = Workbook()
    ws = wb.active
    ws.title = "Inventory"
    headers = [
        "SKU",
        "Title",
        "Vendor URL",
        "Vendor Price",
        "Price",
        "Stock",
        "Inventory Sync",
        "Marketplace Status",
        "Last Scrape",
        "Last Push",
        "Scrape Error",
    ]
    ws.append(headers)
    for listing in qs:
        ws.append([
            listing.sku or listing.external_variant_key,
            listing.title,
            listing.vendor_url,
            float(listing.vendor_price) if listing.vendor_price is not None else "",
            float(listing.sale_price) if listing.sale_price is not None else "",
            listing.inventory,
            listing.inventory_sync_status,
            listing.status,
            listing.last_scrape_at.isoformat() if listing.last_scrape_at else "",
            listing.last_uploaded_at.isoformat() if listing.last_uploaded_at else "",
            listing.last_scrape_error or "",
        ])
    from .export_xlsx import _workbook_response_bytes
    return _workbook_response_bytes(wb)
