"""
Catalog Celery tasks: sync, scrape, update.
"""
import logging
from celery import shared_task
from django.utils import timezone
from django.db import transaction

logger = logging.getLogger(__name__)

from .models import CatalogUpload, CatalogUploadRow, CatalogSyncLog, ProductMapping
from .services import _normalize
from products.models import Product
from vendor.models import Vendor


def _resolve_vendor(vendor_name_raw: str) -> Vendor | None:
    """Resolve vendor by name or code."""
    vn = _normalize(vendor_name_raw)
    if not vn:
        return None
    vn_lower = vn.lower()
    for v in Vendor.objects.all():
        if v.name and v.name.lower() == vn_lower:
            return v
        if v.code and v.code.lower() == vn_lower:
            return v
    return None


def _normalize_action(action_raw: str) -> str:
    """Return add, update, or delete."""
    a = (action_raw or '').strip().lower()
    if a in ('add', 'update', 'delete'):
        return a
    return 'add'


def _find_product_mapping(row: CatalogUploadRow, store, *, active_only: bool = True) -> ProductMapping | None:
    """Find ProductMapping by marketplace_id, marketplace_child_sku, or vendor+sku."""
    mid = _normalize(row.marketplace_id_raw)
    sku = _normalize(row.marketplace_child_sku_raw)
    qs = ProductMapping.objects.filter(store=store)
    if active_only:
        qs = qs.filter(is_active=True)
    if mid:
        pm = qs.filter(marketplace_id=mid).first()
        if pm:
            return pm
    if sku:
        pm = qs.filter(marketplace_child_sku=sku).first()
        if pm:
            return pm
    # Fallback: by product if we have vendor + at least one identifier
    vendor = row.vendor or _resolve_vendor(row.vendor_name_raw)
    if not vendor:
        return None
    vid = _normalize(row.variation_id_raw) or ''
    vsku = (
        _normalize(row.vendor_sku_raw)
        or _normalize(row.marketplace_child_sku_raw)
        or _normalize(row.marketplace_parent_sku_raw)
    )
    if not vsku:
        return None
    product = Product.objects.filter(
        vendor=vendor, vendor_sku=vsku, variation_id=vid
    ).first()
    if product:
        pm_qs = ProductMapping.objects.filter(store=store, product=product)
        if active_only:
            pm_qs = pm_qs.filter(is_active=True)
        return pm_qs.first()
    # Last resort: match by marketplace_parent_sku on ProductMapping
    mp_sku = _normalize(row.marketplace_parent_sku_raw)
    if mp_sku:
        return qs.filter(marketplace_parent_sku=mp_sku).first()
    return None


def _get_or_create_product(vendor: Vendor, row: CatalogUploadRow) -> Product:
    """Get or create Product from row."""
    vsku = (
        _normalize(row.vendor_sku_raw)
        or _normalize(row.marketplace_child_sku_raw)
        or _normalize(row.vendor_id_raw)
        or _normalize(row.marketplace_parent_sku_raw)
    )
    vid = _normalize(row.variation_id_raw) or ''
    url = _normalize(row.vendor_url_raw)
    product, created = Product.objects.get_or_create(
        vendor=vendor,
        vendor_sku=vsku,
        variation_id=vid,
        defaults={'vendor_url': url or None},
    )
    if url and not product.vendor_url:
        product.vendor_url = url
        product.save(update_fields=['vendor_url'])
    return product


def _update_product_mapping(pm: ProductMapping, row: CatalogUploadRow) -> None:
    """Update ProductMapping fields from row."""
    updates = {}
    mp_sku = _normalize(row.marketplace_parent_sku_raw)
    mc_sku = _normalize(row.marketplace_child_sku_raw)
    mid = _normalize(row.marketplace_id_raw)
    if mc_sku is not None:
        updates['marketplace_child_sku'] = mc_sku
    if mp_sku is not None:
        updates['marketplace_parent_sku'] = mp_sku
    if mid is not None:
        updates['marketplace_id'] = mid
    url = _normalize(row.vendor_url_raw)
    if url and pm.product:
        pm.product.vendor_url = url
        pm.product.save(update_fields=['vendor_url'])
    if updates:
        for k, v in updates.items():
            setattr(pm, k, v)
        pm.save(update_fields=list(updates.keys()))


def run_catalog_sync(upload_id: str):
    """
    Sync CatalogUpload rows: Add/Update/Delete Product and ProductMapping.
    Creates CatalogSyncLog per row. Call directly or via catalog_sync_task.
    """
    try:
        upload = CatalogUpload.objects.select_related('store').get(id=upload_id)
    except CatalogUpload.DoesNotExist:
        return {'error': 'Upload not found', 'upload_id': upload_id}

    store = upload.store
    upload.status = CatalogUpload.Status.PROCESSING
    upload.save(update_fields=['status'])
    added, updated, deleted, errors = 0, 0, 0, 0

    for row in upload.rows.all().order_by('row_number'):
        action = _normalize_action(row.action_raw)
        log_status = CatalogSyncLog.Status.SUCCESS
        log_message = None

        try:
            with transaction.atomic():
                if action == 'delete':
                    pm = _find_product_mapping(row, store, active_only=False)
                    if pm:
                        pm.is_active = False
                        pm.save(update_fields=['is_active'])
                        row.sync_status = CatalogUploadRow.SyncStatus.DELETED
                        row.product_mapping = pm
                        deleted += 1
                    else:
                        row.sync_status = CatalogUploadRow.SyncStatus.ERROR
                        row.sync_error = 'Mapping not found for delete'
                        log_status = CatalogSyncLog.Status.ERROR
                        log_message = row.sync_error
                        errors += 1
                else:
                    vendor = row.vendor or _resolve_vendor(row.vendor_name_raw)
                    if not vendor:
                        row.sync_status = CatalogUploadRow.SyncStatus.ERROR
                        row.sync_error = f"Vendor not found: {row.vendor_name_raw}"
                        log_status = CatalogSyncLog.Status.ERROR
                        log_message = row.sync_error
                        errors += 1
                    elif action == 'add':
                        product = _get_or_create_product(vendor, row)
                        mp_sku = _normalize(row.marketplace_parent_sku_raw)
                        mc_sku = _normalize(row.marketplace_child_sku_raw)
                        mid = _normalize(row.marketplace_id_raw)
                        pm, created = ProductMapping.objects.get_or_create(
                            store=store,
                            product=product,
                            defaults={
                                'marketplace_child_sku': mc_sku,
                                'marketplace_parent_sku': mp_sku,
                                'marketplace_id': mid,
                                'is_active': True,
                            },
                        )
                        if not created and not pm.is_active:
                            pm.is_active = True
                            pm.save(update_fields=['is_active'])
                        row.product = product
                        row.product_mapping = pm
                        row.sync_status = (
                            CatalogUploadRow.SyncStatus.ADDED
                            if created
                            else CatalogUploadRow.SyncStatus.UPDATED
                        )
                        if created:
                            added += 1
                        else:
                            _update_product_mapping(pm, row)
                            updated += 1
                    else:  # update
                        pm = _find_product_mapping(row, store)
                        if pm:
                            _update_product_mapping(pm, row)
                            row.product_mapping = pm
                            row.sync_status = CatalogUploadRow.SyncStatus.UPDATED
                            updated += 1
                        else:
                            row.sync_status = CatalogUploadRow.SyncStatus.ERROR
                            row.sync_error = 'Mapping not found for update'
                            log_status = CatalogSyncLog.Status.ERROR
                            log_message = row.sync_error
                            errors += 1

                row.save(update_fields=['sync_status', 'sync_error', 'product', 'product_mapping'])
                CatalogSyncLog.objects.create(
                    catalog_upload=upload,
                    catalog_upload_row=row,
                    action=action,
                    status=log_status,
                    message=log_message,
                )
                upload.processed_rows = upload.processed_rows + 1
                upload.save(update_fields=['processed_rows'])
        except Exception as e:
            row.sync_status = CatalogUploadRow.SyncStatus.ERROR
            row.sync_error = str(e)
            row.save(update_fields=['sync_status', 'sync_error'])
            CatalogSyncLog.objects.create(
                catalog_upload=upload,
                catalog_upload_row=row,
                action=action,
                status=CatalogSyncLog.Status.ERROR,
                message=str(e),
            )
            errors += 1

    # Final upload status
    if errors and upload.processed_rows < upload.total_rows:
        upload.status = CatalogUpload.Status.PARTIAL
    elif errors:
        upload.status = CatalogUpload.Status.FAILED
    else:
        upload.status = CatalogUpload.Status.SYNCED
    upload.error_summary = f"Added: {added}, Updated: {updated}, Deleted: {deleted}, Errors: {errors}" if errors else None
    upload.save(update_fields=['status', 'error_summary'])

    return {
        'upload_id': str(upload_id),
        'status': upload.status,
        'added': added,
        'updated': updated,
        'deleted': deleted,
        'errors': errors,
    }


@shared_task(bind=True, max_retries=3)
def catalog_sync_task(self, upload_id: str):
    """Celery wrapper for run_catalog_sync."""
    return run_catalog_sync(upload_id)


def run_catalog_scrape(upload_id: str):
    """
    Scrape vendor URLs for rows in upload, apply pricing/inventory rules, update ProductMapping.
    Call directly or via catalog_scrape_task.
    """
    from sync.models import ScrapeRun
    from sync.tasks import _get_pricing_for_vendor, _apply_pricing, _apply_inventory
    from sync.tasks import _get_inventory_for_vendor, _resolve_vendor_url
    from vendor.models import VendorPrice
    from scrapers import get_price_and_stock, close_amazon_session

    try:
        upload = CatalogUpload.objects.select_related('store').get(id=upload_id)
    except CatalogUpload.DoesNotExist:
        return {'error': 'Upload not found', 'upload_id': upload_id}

    store = upload.store
    run = ScrapeRun.objects.create(
        catalog_upload=upload,
        store=store,
        status=ScrapeRun.Status.RUNNING,
    )
    session = {}
    succeeded, failed = 0, 0
    fatal_error = None

    try:
        rows = upload.rows.filter(
            product_mapping__isnull=False,
            product_mapping__is_active=True,
        ).select_related('product_mapping', 'product_mapping__product', 'product_mapping__product__vendor')
        now = timezone.now()

        for row in rows:
            pm = row.product_mapping
            product = pm.product
            if not product:
                continue
            url = _normalize(row.vendor_url_raw) or _resolve_vendor_url(product, store)
            if not url:
                continue

            run.rows_processed += 1
            if run.rows_processed % 10 == 0:
                run.rows_succeeded = succeeded
                run.save(update_fields=['rows_processed', 'rows_succeeded'])

            import os
            use_demo_fallback = os.getenv('DEMO_SCRAPE_FALLBACK', 'false').lower() in ('1', 'true', 'yes')
            scrape_title = ''
            logger.info(
                "Scraping row %d: sku=%s vendor=%s region=%s url=%s",
                run.rows_processed,
                product.vendor_sku,
                (product.vendor.code if product.vendor else '?'),
                store.region or 'USA',
                url[:120],
            )

            try:
                result = get_price_and_stock(url, store.region or '', session)
                vendor_price = result.get('price')
                vendor_stock = result.get('stock', 0) or 0
                if isinstance(result, dict):
                    scrape_title = (result.get('title') or '').strip()[:500]
            except Exception as scrape_err:
                logger.exception(
                    "Scrape failed for %s (url=%s): %s", product.vendor_sku, url, scrape_err
                )
                if use_demo_fallback:
                    vendor_price = 29.99
                    vendor_stock = 5
                else:
                    pm.store_price = None
                    pm.store_stock = None
                    pm.failed_sync_count = (pm.failed_sync_count or 0) + 1
                    pm.sync_status = 'needs_attention' if pm.failed_sync_count >= 3 else 'failed'
                    pm.last_sync_time = now
                    pm.save(
                        update_fields=[
                            'store_price',
                            'store_stock',
                            'failed_sync_count',
                            'sync_status',
                            'last_sync_time',
                        ]
                    )
                    failed += 1
                    continue

            # Demo fallback: when scraper returns None (Selenium not set up, Amazon blocks, etc.)
            if vendor_price is None and use_demo_fallback:
                vendor_price = 29.99
                vendor_stock = vendor_stock if vendor_stock and vendor_stock > 0 else 5

            if vendor_price is None and not use_demo_fallback:
                pm.store_price = None
                pm.store_stock = None
                pm.failed_sync_count = (pm.failed_sync_count or 0) + 1
                pm.sync_status = 'needs_attention' if pm.failed_sync_count >= 3 else 'failed'
                pm.last_sync_time = now
                pm.save(
                    update_fields=[
                        'store_price',
                        'store_stock',
                        'failed_sync_count',
                        'sync_status',
                        'last_sync_time',
                    ]
                )
                failed += 1
                continue

            if vendor_stock is None or vendor_stock < 0:
                vendor_stock = 0

            try:
                pricing = _get_pricing_for_vendor(store, product.vendor_id)
                inventory = _get_inventory_for_vendor(store, product.vendor_id)
                new_price = _apply_pricing(vendor_price, pricing) if vendor_price is not None else None
                new_stock = _apply_inventory(vendor_stock, inventory)

                if vendor_price is not None:
                    from decimal import Decimal
                    VendorPrice.objects.create(
                        product=product,
                        price=Decimal(str(vendor_price)),
                        stock=int(vendor_stock),
                    )

                pm.store_price = new_price
                pm.store_stock = new_stock
                pm.sync_status = 'synced'
                pm.failed_sync_count = 0
                pm.last_sync_time = now
                save_fields = ['store_price', 'store_stock', 'sync_status', 'failed_sync_count', 'last_sync_time']
                if scrape_title:
                    pm.title = scrape_title
                    save_fields.append('title')
                pm.save(update_fields=save_fields)
                succeeded += 1
            except Exception as apply_err:
                logger.exception(
                    'Pricing/inventory apply failed for SKU %s (store=%s): %s',
                    product.vendor_sku, store.id, apply_err,
                )
                pm.store_price = None
                pm.store_stock = None
                pm.failed_sync_count = (pm.failed_sync_count or 0) + 1
                pm.sync_status = 'needs_attention' if pm.failed_sync_count >= 3 else 'failed'
                pm.last_sync_time = now
                pm.save(
                    update_fields=[
                        'store_price',
                        'store_stock',
                        'failed_sync_count',
                        'sync_status',
                        'last_sync_time',
                    ]
                )
                failed += 1
                continue
    except Exception as loop_err:
        fatal_error = loop_err
        logger.exception('Catalog scrape aborted: %s', loop_err)
    finally:
        close_amazon_session(session)

    run.finished_at = timezone.now()
    run.rows_succeeded = succeeded
    if fatal_error:
        run.status = ScrapeRun.Status.FAILED
        run.error_summary = str(fatal_error)[:2000]
    else:
        run.status = ScrapeRun.Status.FAILED if succeeded == 0 and run.rows_processed > 0 else (
            ScrapeRun.Status.PARTIAL if failed else ScrapeRun.Status.SUCCESS
        )
    run.save()

    out = {
        'upload_id': str(upload_id),
        'run_id': str(run.id),
        'status': run.status,
        'rows_processed': run.rows_processed,
        'rows_succeeded': succeeded,
        'failed': failed,
    }
    if fatal_error:
        out['error'] = str(fatal_error)
    return out


@shared_task(bind=True, max_retries=3)
def catalog_scrape_task(self, upload_id: str):
    """Celery wrapper for run_catalog_scrape."""
    return run_catalog_scrape(upload_id)


@shared_task(bind=True, max_retries=3)
def catalog_update_task(self, upload_id: str):
    """
    Push to Reverb API: update price/inventory for active mappings, end listings for Delete rows.
    Uses marketplace_id (listing ID) or lookup by marketplace_child_sku.
    """
    from .models import ReverbUpdateLog
    from store_adapters.reverb_adapter import ReverbAdapter, ReverbAPIError

    try:
        upload = CatalogUpload.objects.select_related('store').get(id=upload_id)
    except CatalogUpload.DoesNotExist:
        return {'error': 'Upload not found', 'upload_id': upload_id}

    store = upload.store
    adapter = ReverbAdapter(store)

    # 1. End listings for Delete rows (soft-deleted mappings)
    for row in upload.rows.filter(
        action_raw__icontains='delete',
        product_mapping__isnull=False,
    ):
        pm = row.product_mapping
        if not pm or pm.is_active:
            continue
        listing_id = pm.marketplace_id
        if not listing_id:
            for sku_candidate in filter(None, [
                pm.marketplace_child_sku,
                pm.marketplace_parent_sku,
                pm.product.vendor_sku if pm.product else None,
            ]):
                listing_id = adapter.lookup_listing_by_sku(sku_candidate)
                if listing_id:
                    break
        if not listing_id:
            ReverbUpdateLog.objects.create(
                product_mapping=pm,
                status=ReverbUpdateLog.Status.FAILED,
                error_message='No Reverb listing ID or SKU for end listing',
            )
            continue
        try:
            adapter.delete_product(listing_id)
            ReverbUpdateLog.objects.create(
                product_mapping=pm,
                status=ReverbUpdateLog.Status.SUCCESS,
                pushed_stock=0,
            )
        except ReverbAPIError as e:
            ReverbUpdateLog.objects.create(
                product_mapping=pm,
                status=ReverbUpdateLog.Status.FAILED,
                http_status=e.status_code,
                error_message=str(e),
            )

    # 2. Update active mappings with price/stock
    rows_to_update = upload.rows.filter(
        product_mapping__isnull=False,
        product_mapping__is_active=True,
    ).exclude(
        action_raw__icontains='delete',
    ).select_related('product_mapping')
    succeeded, failed = 0, 0

    for row in rows_to_update:
        pm = row.product_mapping
        if pm.sync_status != 'synced' or pm.store_price is None:
            continue
        listing_id = pm.marketplace_id
        if not listing_id:
            for sku_candidate in filter(None, [
                pm.marketplace_child_sku,
                pm.marketplace_parent_sku,
                pm.product.vendor_sku if pm.product else None,
            ]):
                listing_id = adapter.lookup_listing_by_sku(sku_candidate)
                if listing_id:
                    pm.marketplace_id = listing_id
                    if not pm.marketplace_child_sku:
                        pm.marketplace_child_sku = sku_candidate
                        pm.save(update_fields=['marketplace_id', 'marketplace_child_sku'])
                    else:
                        pm.save(update_fields=['marketplace_id'])
                    break
        if not listing_id:
            ReverbUpdateLog.objects.create(
                product_mapping=pm,
                status=ReverbUpdateLog.Status.FAILED,
                error_message='No Reverb listing ID or SKU',
            )
            failed += 1
            continue
        try:
            adapter.update_product(
                listing_id,
                price=float(pm.store_price),
                stock=pm.store_stock or 0,
            )
            ReverbUpdateLog.objects.create(
                product_mapping=pm,
                status=ReverbUpdateLog.Status.SUCCESS,
                pushed_price=pm.store_price,
                pushed_stock=pm.store_stock,
            )
            succeeded += 1
        except ReverbAPIError as e:
            ReverbUpdateLog.objects.create(
                product_mapping=pm,
                status=ReverbUpdateLog.Status.FAILED,
                http_status=e.status_code,
                error_message=str(e),
                retry_count=0,
            )
            failed += 1

    return {
        'upload_id': str(upload_id),
        'succeeded': succeeded,
        'failed': failed,
    }
