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
from .reverb import listings as reverb_listings

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


def _store_kind(store) -> str:
    return marketplace_kind(getattr(store, "marketplace", None))


def _listing_env(store) -> str:
    """Reverb has no staging API — always production. Lasoo uses store setting."""
    if _store_kind(store) == "reverb":
        return Environment.PRODUCTION
    return store.lasoo_environment or Environment.STAGING


def _validate_listing(store, data: dict) -> list[str]:
    if _store_kind(store) == "reverb":
        return reverb_listings.validate_listing(data)
    return validator.validate_listing(data)


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
    listing.sku = (data.get("sku") or "").strip()
    listing.barcode = (data.get("barcode") or data.get("upc") or "").strip()
    listing.vendor_url = (data.get("vendor_url") or "").strip()[:1000]
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
    kind = marketplace_kind(store.marketplace)
    if on_marketplace and kind == "lasoo":
        environment = listing.environment or store.lasoo_environment or Environment.STAGING
        client = LasooClient(store, environment)
        payload = mapper.build_bulk_delete_payload([listing.external_variant_key], client.auth_key)
        result = client.send("bulk_delete", payload)
        if not result.ok:
            raise MarketplaceError(
                result.message or "Could not delete the listing on the marketplace."
            )
    elif on_marketplace and kind == "reverb":
        adapter = get_adapter(store)
        listing_id = (listing.external_product_key or "").strip()
        # Prefer Reverb listing id stored after publish; fall back to SKU lookup.
        if not listing_id or listing_id == listing.sku:
            try:
                listing_id = adapter.lookup_listing_by_sku(listing.sku or listing.external_variant_key) or listing_id
            except ReverbAPIError as exc:
                raise MarketplaceError(str(exc) or "Could not look up Reverb listing.") from exc
        if listing_id:
            try:
                adapter.delete_product(listing_id)
            except ReverbAPIError as exc:
                raise MarketplaceError(
                    str(exc) or "Could not end the listing on Reverb."
                ) from exc
    variant_key = listing.external_variant_key
    listing.delete()
    return {"ok": True, "variant_key": variant_key, "marketplace_deleted": on_marketplace}


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
        kind = marketplace_kind(store.marketplace)
        if kind == "lasoo":
            environment = store.lasoo_environment or Environment.STAGING
            client = LasooClient(store, environment)
            payload = mapper.build_bulk_delete_payload(marketplace_keys, client.auth_key)
            result = client.send("bulk_delete", payload)
            if not result.ok:
                raise MarketplaceError(
                    result.message or "Could not delete the listings on the marketplace."
                )
        elif kind == "reverb":
            adapter = get_adapter(store)
            for key in marketplace_keys:
                try:
                    lid = adapter.lookup_listing_by_sku(key) or key
                    if lid:
                        adapter.delete_product(lid)
                except ReverbAPIError as exc:
                    raise MarketplaceError(
                        str(exc) or f"Could not end Reverb listing for SKU {key}."
                    ) from exc
        else:
            raise MarketplaceError(
                "Deleting marketplace listings is currently only supported for Lasoo and Reverb stores."
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

    environment = _listing_env(store)
    preview, imported, error_rows = [], 0, 0
    for row in rows:
        errors = _validate_listing(store, row)
        row_result = {
            "row_number": row.get("row_number"),
            "sku": row.get("sku", ""),
            "variant_key": row.get("variant_key") or row.get("sku", ""),
            "errors": errors,
            "valid": not errors,
            "imported": False,
        }

        _, variant_key = _resolve_keys(store, row)
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
                f'A listing with SKU "{variant_key}" already exists. '
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


def scrape_listings(user, store, listing_ids=None) -> dict:
    """Scrape vendor URLs on managed listings and update local price/stock."""
    from scrapers import close_amazon_session, get_price_and_stock

    qs = StoreListing.objects.filter(user=user, store=store)
    if listing_ids:
        qs = qs.filter(id__in=listing_ids)
    else:
        # Default: inventory (on marketplace) + ready created rows with a vendor link
        qs = qs.filter(
            status__in=[
                ListingStatus.UPLOADED_STAGING,
                ListingStatus.UPLOADED_PRODUCTION,
                ListingStatus.READY,
                ListingStatus.FAILED,
            ]
        )
    listings = [l for l in qs if (l.vendor_url or "").strip()]
    if not listings:
        raise MarketplaceError(
            "No listings with a Vendor URL to scrape. Add a vendor link on each listing first."
        )

    region = (getattr(store, "region", None) or "USA").strip() or "USA"
    session = {}
    scraped = 0
    failed = 0
    rows = []
    try:
        for listing in listings:
            url = (listing.vendor_url or "").strip()
            row = {
                "id": str(listing.id),
                "sku": listing.sku or listing.external_variant_key,
                "vendor_url": url,
                "ok": False,
                "price": None,
                "inventory": None,
                "error": "",
            }
            try:
                result = get_price_and_stock(url, region, session) or {}
            except Exception as exc:  # noqa: BLE001
                row["error"] = str(exc) or "Scrape failed."
                failed += 1
                rows.append(row)
                logger.warning("Listing scrape failed SKU %s: %s", listing.sku, exc)
                continue

            if result.get("ingest_only"):
                row["error"] = "This vendor is ingest-only and cannot be scraped server-side."
                failed += 1
                rows.append(row)
                continue

            price = result.get("price")
            stock = result.get("stock")
            if result.get("inventory") is not None and stock is None:
                stock = result.get("inventory")
            err = result.get("error_message") or result.get("error") or ""
            if price is None and stock is None:
                row["error"] = str(err) or "No price or stock returned."
                failed += 1
                rows.append(row)
                continue

            update_fields = ["updated_at"]
            if price is not None:
                listing.sale_price = _safe_decimal(price)
                listing.original_price = listing.sale_price
                cents = int(listing.sale_price * 100)
                listing.sale_price_cents = cents
                listing.original_price_cents = cents
                update_fields.extend(
                    ["sale_price", "original_price", "sale_price_cents", "original_price_cents"]
                )
                row["price"] = float(listing.sale_price)
            if stock is not None:
                try:
                    listing.inventory = max(0, int(stock))
                except (TypeError, ValueError):
                    listing.inventory = 0
                listing.infinite_quantity = False
                update_fields.extend(["inventory", "infinite_quantity"])
                row["inventory"] = listing.inventory

            # Keep scrape timestamp in Reverb extras JSON (no migration).
            if _store_kind(store) == "reverb":
                extras = reverb_listings.parse_extras(listing)
                extras["last_scrape_at"] = timezone.now().isoformat()
                extras["last_scrape_ok"] = True
                listing.external_data_object_json = reverb_listings.build_extras(
                    {
                        **extras,
                        "make": extras.get("make") or listing.brand,
                        "model": extras.get("model") or "",
                        "condition_uuid": extras.get("condition_uuid") or "",
                        "category_uuid": extras.get("category_uuid") or listing.category,
                        "currency": extras.get("currency") or "USD",
                        "upc_does_not_apply": extras.get("upc_does_not_apply", False),
                        "publish_status": extras.get("publish_status") or "draft",
                        "free_shipping": extras.get("free_shipping", True),
                        "finish": extras.get("finish") or "",
                        "year": extras.get("year") or "",
                    }
                )
                # Preserve last_scrape fields build_extras does not know about
                import json as _json
                payload = _json.loads(listing.external_data_object_json)
                payload["last_scrape_at"] = extras["last_scrape_at"]
                payload["last_scrape_ok"] = True
                listing.external_data_object_json = _json.dumps(payload)
                update_fields.append("external_data_object_json")

            listing.save(update_fields=list(dict.fromkeys(update_fields)))
            row["ok"] = True
            scraped += 1
            rows.append(row)
    finally:
        try:
            close_amazon_session(session)
        except Exception:  # noqa: BLE001
            pass

    return {
        "ok": scraped > 0,
        "message": (
            f"Scraped {scraped} listing(s)"
            + (f"; {failed} failed." if failed else ".")
        ),
        "scraped": scraped,
        "failed": failed,
        "rows": rows,
    }


def push_inventory(user, store, listing_ids=None) -> dict:
    """Push local price/stock to the marketplace for listings already uploaded."""
    kind = marketplace_kind(store.marketplace)
    if kind != "reverb":
        raise MarketplaceError(
            "Inventory push is currently supported for Reverb managed stores."
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
            listing.save(update_fields=["external_product_key", "last_uploaded_at", "updated_at"])
            row["ok"] = True
            pushed += 1
        except ReverbAPIError as exc:
            row["error"] = str(exc) or "Reverb update failed."
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
