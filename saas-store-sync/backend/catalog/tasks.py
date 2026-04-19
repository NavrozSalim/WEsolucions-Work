"""
Catalog Celery tasks: sync, scrape, update.
"""
import logging
from celery import shared_task
from django.utils import timezone
from django.db import transaction
from decimal import Decimal

logger = logging.getLogger(__name__)

from .models import CatalogUpload, CatalogUploadRow, CatalogSyncLog, ProductMapping
from .reverb_catalog import listing_sku_lookup_order, store_is_reverb, vendor_is_ebay
from .services import _normalize
from .vendor_price_fallback import get_last_known_vendor_price_stock
from products.models import Product
from vendor.models import Vendor


def _is_ingest_only_product(product) -> bool:
    """True when the vendor has no live server-side scraper (prices come from
    the desktop runner or the Vevor S3 XLSX feed). Those flows write to
    ``ProductMapping`` directly via the ingest endpoints, so the catalog
    scrape task must never clobber them with a 'failed' flag just because
    the HTTP dispatcher returned an ``*_ingest_only_result`` sentinel.
    """
    vendor = getattr(product, 'vendor', None)
    code = (getattr(vendor, 'code', '') or '').lower()
    if code in ('heb', 'hebus', 'costcoau', 'costco_au', 'costco-au', 'vevor', 'vevorau'):
        return True
    if code.startswith('heb_') or code.startswith('costco_') or code.startswith('vevor_'):
        return True
    return False


def _fail_mapping(pm, code: str, message: str = '') -> None:
    """Mark a ProductMapping as a strict scrape failure.

    Clears ``store_price`` + ``store_stock`` (so nothing gets pushed to the
    marketplace), stores a short reason in ``scrape_error``, escalates
    ``sync_status`` to ``needs_attention`` after 3 consecutive failures.
    """
    pm.store_price = None
    pm.store_stock = None
    pm.failed_sync_count = (pm.failed_sync_count or 0) + 1
    pm.sync_status = 'needs_attention' if pm.failed_sync_count >= 3 else 'failed'
    reason = (code or 'scrape_failed').strip() or 'scrape_failed'
    if message:
        reason = f'{reason}: {str(message)[:240]}'
    pm.scrape_error = reason[:512]
    pm.save(update_fields=[
        'store_price',
        'store_stock',
        'failed_sync_count',
        'sync_status',
        'scrape_error',
    ])


def _resolve_vendor(vendor_name_raw: str) -> Vendor | None:
    """Resolve vendor by name, code, or canonical alias."""
    from .services import resolve_canonical_vendor_code

    vn = _normalize(vendor_name_raw)
    if not vn:
        return None
    vn_lower = vn.lower()
    for v in Vendor.objects.all():
        if v.name and v.name.lower() == vn_lower:
            return v
        if v.code and v.code.lower() == vn_lower:
            return v
    canon = resolve_canonical_vendor_code(vn)
    if canon:
        return Vendor.objects.filter(code__iexact=canon).first()
    return None


def _is_heb_product(product) -> bool:
    """Return True when ``product`` belongs to the HEB vendor.

    Re-exports ``sync.tasks._is_heb_product`` style check so ``catalog.tasks``
    does not have to import from ``sync.tasks`` at module load time (circular
    import risk). HEB is ingest-only: prices come from the desktop runner via
    ``/api/v1/ingest/heb/``.
    """
    vendor = getattr(product, 'vendor', None)
    code = (getattr(vendor, 'code', '') or '').lower()
    return code in ('heb', 'hebus') or code.startswith('heb_')


def _normalize_action(action_raw: str) -> str:
    """Return add, update, or delete."""
    a = (action_raw or '').strip().lower()
    if a in ('add', 'update', 'delete'):
        return a
    return 'add'


def _to_decimal_or_none(raw_val):
    val = _normalize(raw_val)
    if val is None:
        return None
    try:
        return Decimal(str(val))
    except Exception:
        return None


def _find_product_mapping(row: CatalogUploadRow, store, *, active_only: bool = True) -> ProductMapping | None:
    """Find ProductMapping by marketplace_id, marketplace SKUs, or vendor+product key."""
    vendor_early = row.vendor or _resolve_vendor(row.vendor_name_raw)
    reverb = store_is_reverb(store)
    ebay_v = vendor_is_ebay(vendor_early, row.vendor_name_raw)
    mid = _normalize(row.marketplace_id_raw)
    sku = _normalize(row.marketplace_child_sku_raw)
    mp_row = _normalize(row.marketplace_parent_sku_raw)
    qs = ProductMapping.objects.filter(store=store)
    if active_only:
        qs = qs.filter(is_active=True)
    if mid:
        pm = qs.filter(marketplace_id=mid).first()
        if pm:
            return pm
    if (reverb or ebay_v) and mp_row:
        pm = qs.filter(marketplace_parent_sku=mp_row).first()
        if pm:
            return pm
    if sku:
        pm = qs.filter(marketplace_child_sku=sku).first()
        if pm:
            return pm
    vendor = vendor_early
    if not vendor:
        return None
    vendor_code = (vendor.code or "").strip().lower()
    vid = _normalize(row.variation_id_raw) or ''
    if vendor_code in ("costcoau", "costco_au", "costco-au"):
        vsku = (
            _normalize(row.vendor_id_raw)
            or _normalize(row.vendor_sku_raw)
            or _normalize(row.marketplace_child_sku_raw)
            or _normalize(row.marketplace_parent_sku_raw)
        )
    elif ebay_v:
        vsku = (
            _normalize(row.vendor_sku_raw)
            or _normalize(row.vendor_id_raw)
            or _normalize(row.marketplace_child_sku_raw)
            or _normalize(row.marketplace_parent_sku_raw)
        )
    elif reverb:
        vsku = (
            _normalize(row.marketplace_parent_sku_raw)
            or _normalize(row.vendor_sku_raw)
            or _normalize(row.marketplace_child_sku_raw)
        )
    else:
        vsku = (
            _normalize(row.vendor_sku_raw)
            or _normalize(row.marketplace_child_sku_raw)
            or _normalize(row.vendor_id_raw)
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


def _get_or_create_product(vendor: Vendor, row: CatalogUploadRow, *, store) -> Product:
    """Get or create Product from row."""
    vendor_code = (vendor.code or "").strip().lower()
    if vendor_code in ("costcoau", "costco_au", "costco-au"):
        vsku = (
            _normalize(row.vendor_id_raw)
            or _normalize(row.vendor_sku_raw)
            or _normalize(row.marketplace_child_sku_raw)
            or _normalize(row.marketplace_parent_sku_raw)
        )
    elif vendor_is_ebay(vendor, row.vendor_name_raw):
        vsku = (
            _normalize(row.vendor_sku_raw)
            or _normalize(row.vendor_id_raw)
            or _normalize(row.marketplace_child_sku_raw)
            or _normalize(row.marketplace_parent_sku_raw)
        )
    elif store_is_reverb(store):
        vsku = (
            _normalize(row.marketplace_parent_sku_raw)
            or _normalize(row.vendor_sku_raw)
            or _normalize(row.marketplace_child_sku_raw)
            or _normalize(row.vendor_id_raw)
        )
    else:
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
    updates['pack_qty'] = _to_decimal_or_none(row.pack_qty_raw)
    updates['prep_fees'] = _to_decimal_or_none(row.prep_fees_raw)
    updates['shipping_fees'] = _to_decimal_or_none(row.shipping_fees_raw)
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
        upload = CatalogUpload.objects.select_related('store', 'store__marketplace').get(id=upload_id)
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
                        product = _get_or_create_product(vendor, row, store=store)
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
                                'pack_qty': _to_decimal_or_none(row.pack_qty_raw),
                                'prep_fees': _to_decimal_or_none(row.prep_fees_raw),
                                'shipping_fees': _to_decimal_or_none(row.shipping_fees_raw),
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
    from sync.tasks import (
        _get_pricing_for_vendor,
        _apply_pricing,
        _apply_inventory,
        _has_fixed_tier,
        _missing_fixed_inputs,
        _fail_mapping,
    )
    from sync.tasks import _get_inventory_for_vendor, resolve_vendor_scrape_url, _inventory_from_scrape_result
    from stores.pricing_tiers import resolve_margin_tier_for_raw_cost
    from vendor.models import VendorPrice
    from scrapers import get_price_and_stock, close_amazon_session

    try:
        upload = CatalogUpload.objects.select_related('store', 'store__marketplace').get(id=upload_id)
    except CatalogUpload.DoesNotExist:
        return {'error': 'Upload not found', 'upload_id': upload_id}

    from catalog.activity_log import append_catalog_log

    store = upload.store
    append_catalog_log(
        store.id,
        f'Vendor scrape started for upload “{upload.original_filename}” at '
        f'{timezone.now().strftime("%Y-%m-%d %H:%M:%S %Z")}.',
        action_type='scrape_start',
        metadata={'upload_id': str(upload_id), 'scope': 'upload'},
    )
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

            run.rows_processed += 1
            if run.rows_processed % 10 == 0:
                run.rows_succeeded = succeeded
                run.save(update_fields=['rows_processed', 'rows_succeeded'])

            # Ingest-only vendors (HEB, Costco AU, Vevor AU) have no live
            # server-side scraper. The ingest endpoint — or the Vevor feed
            # task — has already written store_price/store_stock straight
            # to the ProductMapping. We just re-apply the *latest* VendorPrice
            # through the store's current pricing rules so margin edits take
            # effect without a fresh ingest. If there's no VendorPrice yet,
            # skip silently (not a failure — the runner/feed will populate).
            if _is_ingest_only_product(product):
                from sync.tasks import _apply_latest_heb_ingest
                if _apply_latest_heb_ingest(pm, product, store, now):
                    succeeded += 1
                    logger.info(
                        "Ingest-only row refreshed (sku=%s vendor=%s)",
                        getattr(product, 'vendor_sku', '?'),
                        (product.vendor.code if product.vendor else '?'),
                    )
                else:
                    logger.info(
                        "Ingest-only row skipped — no ingest data yet (sku=%s vendor=%s)",
                        getattr(product, 'vendor_sku', '?'),
                        (product.vendor.code if product.vendor else '?'),
                    )
                continue

            url = resolve_vendor_scrape_url(product, store, row)
            if not url:
                logger.warning(
                    'Catalog scrape row %s: no Vendor URL / Vendor ID resolvable for product %s '
                    '(listing marketplace does not affect vendor scraper).',
                    row.row_number,
                    product.vendor_sku,
                )
                _fail_mapping(pm, 'no_vendor_url', 'Product has no vendor URL or resolvable SKU.')
                failed += 1
                continue

            scrape_title = ''
            logger.info(
                "Scraping row %d: sku=%s vendor=%s region=%s url=%s",
                run.rows_processed,
                product.vendor_sku,
                (product.vendor.code if product.vendor else '?'),
                store.region or 'USA',
                url[:120],
            )

            vendor_price = None
            vendor_stock = 0
            result = {}
            try:
                result = get_price_and_stock(url, store.region or '', session)
                vendor_price = result.get('price')
                inv = _inventory_from_scrape_result(result)
                vendor_stock = 0 if inv is None or inv < 0 else inv
                if isinstance(result, dict):
                    scrape_title = (result.get('title') or '').strip()[:500]
            except Exception as scrape_err:
                logger.exception(
                    "Scrape failed for %s (url=%s): %s",
                    product.vendor_sku, url, scrape_err,
                )
                _fail_mapping(pm, 'scrape_exception', str(scrape_err))
                failed += 1
                continue

            if vendor_price is None:
                err_code = (
                    result.get('error_code') if isinstance(result, dict) else None
                ) or 'no_price'
                err_msg = (
                    result.get('error_message') if isinstance(result, dict) else ''
                ) or ''
                logger.warning(
                    "Catalog scrape no price sku=%s url=%s code=%s msg=%s",
                    product.vendor_sku,
                    url[:160],
                    err_code,
                    err_msg[:300],
                )
                _fail_mapping(pm, err_code, err_msg)
                failed += 1
                continue

            if vendor_stock is None or vendor_stock < 0:
                vendor_stock = 0

            try:
                from decimal import Decimal

                pricing = _get_pricing_for_vendor(store, product.vendor_id)
                inventory = _get_inventory_for_vendor(store, product.vendor_id)

                if _has_fixed_tier(pricing):
                    tier_now = resolve_margin_tier_for_raw_cost(pricing, vendor_price)
                    if tier_now is not None and getattr(tier_now, 'margin_type', '') == 'fixed':
                        missing_inputs = _missing_fixed_inputs(pm)
                        if missing_inputs:
                            _fail_mapping(
                                pm,
                                'missing_fixed_inputs',
                                f"Fixed pricing requires {', '.join(missing_inputs)} on the catalog row.",
                            )
                            failed += 1
                            continue

                new_price = (
                    _apply_pricing(
                        vendor_price,
                        pricing,
                        pack_qty=getattr(pm, 'pack_qty', None),
                        prep_fees=getattr(pm, 'prep_fees', None),
                        shipping_fees=getattr(pm, 'shipping_fees', None),
                    )
                    if vendor_price is not None else None
                )
                if new_price is None and vendor_price is not None:
                    new_price = Decimal(str(vendor_price))
                new_stock = _apply_inventory(vendor_stock, inventory)

                VendorPrice.objects.create(
                    product=product,
                    price=Decimal(str(vendor_price)),
                    stock=int(vendor_stock),
                )

                pm.store_price = new_price
                pm.store_stock = new_stock
                pm.sync_status = 'scraped'
                pm.failed_sync_count = 0
                pm.last_scrape_time = now
                pm.scrape_error = None
                save_fields = [
                    'store_price', 'store_stock', 'sync_status',
                    'failed_sync_count', 'last_scrape_time', 'scrape_error',
                ]
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
                _fail_mapping(pm, 'pricing_apply_error', str(apply_err))
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
    append_catalog_log(
        store.id,
        f'Vendor scrape finished at {timezone.now().strftime("%Y-%m-%d %H:%M:%S %Z")}. '
        f'{succeeded} row(s) updated, {failed} failed, {run.rows_processed} processed.',
        action_type='scrape_end',
        metadata={'rows_succeeded': succeeded, 'failed': failed, 'upload_id': str(upload_id)},
    )
    # Intentionally do not auto-push after scrape.
    # "synced" should only come from explicit Manual sync or scheduled sync runs.
    return out


def run_store_wide_catalog_scrape(store_id: str) -> dict:
    """
    Scrape all active ProductMappings for a store (same flow as scheduled run_store_update
    vendor pass, without marketplace push). Matches Amazon/eBay behavior for posted price,
    inventory, and listing title on every listing — not only rows on the latest catalog upload.
    """
    from decimal import Decimal

    from scrapers import close_amazon_session, get_price_and_stock
    from stores.models import Store
    from sync.tasks import (
        _apply_inventory,
        _apply_pricing,
        _fail_mapping,
        _get_inventory_for_vendor,
        _get_pricing_for_vendor,
        _has_fixed_tier,
        _missing_fixed_inputs,
        resolve_vendor_scrape_url,
        _inventory_from_scrape_result,
    )
    from stores.pricing_tiers import resolve_margin_tier_for_raw_cost
    from vendor.models import VendorPrice

    from catalog.activity_log import append_catalog_log

    try:
        store = Store.objects.select_related('marketplace').get(id=store_id)
    except Store.DoesNotExist:
        return {'error': 'store_not_found', 'store_id': str(store_id)}

    append_catalog_log(
        store.id,
        f'Store-wide vendor scrape started at {timezone.now().strftime("%Y-%m-%d %H:%M:%S %Z")} '
        f'for all active listings.',
        action_type='scrape_start',
        metadata={'scope': 'store'},
    )

    mappings = ProductMapping.objects.filter(
        store=store, is_active=True
    ).select_related('product', 'product__vendor')
    session: dict = {}
    processed = succeeded = failed = 0
    now = timezone.now()
    error_summary = None

    try:
        for pm in mappings:
            processed += 1
            product = pm.product
            if not product:
                continue
            # Ingest-only vendors (HEB, Costco AU, Vevor AU) — see
            # ``run_catalog_scrape`` for rationale.
            if _is_ingest_only_product(product):
                from sync.tasks import _apply_latest_heb_ingest
                if _apply_latest_heb_ingest(pm, product, store, now):
                    succeeded += 1
                    logger.info(
                        "Ingest-only row (store-wide) refreshed (sku=%s vendor=%s)",
                        getattr(product, 'vendor_sku', '?'),
                        (product.vendor.code if product.vendor else '?'),
                    )
                else:
                    logger.info(
                        "Ingest-only row (store-wide) skipped — no ingest data yet (sku=%s vendor=%s)",
                        getattr(product, 'vendor_sku', '?'),
                        (product.vendor.code if product.vendor else '?'),
                    )
                continue
            pricing = _get_pricing_for_vendor(store, product.vendor_id)
            inventory = _get_inventory_for_vendor(store, product.vendor_id)

            url = resolve_vendor_scrape_url(product, store, None)
            vendor_price = None
            vendor_stock = 0
            scrape_title = ''
            result = {}
            try:
                if not url:
                    raise ValueError('Product has no vendor_url or resolvable SKU')
                result = get_price_and_stock(url, store.region or '', session)
                vendor_price = result.get('price')
                vendor_stock = _inventory_from_scrape_result(result)
                scrape_title = (result.get('title') or '').strip()[:500]
            except Exception as e:
                logger.exception(
                    'Store scrape failed for %s (url=%s): %s',
                    product.vendor_sku,
                    url[:120] if url else '',
                    e,
                )
                _fail_mapping(pm, 'scrape_exception', str(e))
                failed += 1
                error_summary = str(e) if not error_summary else error_summary
                continue

            if vendor_price is None:
                err_code = (
                    result.get('error_code') if isinstance(result, dict) else None
                ) or 'no_price'
                err_msg = (
                    result.get('error_message') if isinstance(result, dict) else ''
                ) or ''
                logger.warning(
                    "Store-wide scrape no price sku=%s url=%s code=%s msg=%s",
                    product.vendor_sku,
                    (url or "")[:160],
                    err_code,
                    err_msg[:300],
                )
                _fail_mapping(pm, err_code, err_msg)
                failed += 1
                error_summary = err_code if not error_summary else error_summary
                continue

            if vendor_stock is None or vendor_stock <= 0:
                vendor_stock = 0

            try:
                if _has_fixed_tier(pricing):
                    tier_now = resolve_margin_tier_for_raw_cost(pricing, vendor_price)
                    if tier_now is not None and getattr(tier_now, 'margin_type', '') == 'fixed':
                        missing_inputs = _missing_fixed_inputs(pm)
                        if missing_inputs:
                            _fail_mapping(
                                pm,
                                'missing_fixed_inputs',
                                f"Fixed pricing requires {', '.join(missing_inputs)} on the catalog row.",
                            )
                            failed += 1
                            error_summary = 'missing_fixed_inputs' if not error_summary else error_summary
                            continue

                new_price = (
                    _apply_pricing(
                        vendor_price,
                        pricing,
                        pack_qty=getattr(pm, 'pack_qty', None),
                        prep_fees=getattr(pm, 'prep_fees', None),
                        shipping_fees=getattr(pm, 'shipping_fees', None),
                    )
                    if vendor_price is not None else None
                )
                if new_price is None and vendor_price is not None:
                    new_price = Decimal(str(vendor_price))
                new_stock = _apply_inventory(vendor_stock, inventory)

                VendorPrice.objects.create(
                    product=product,
                    price=Decimal(str(vendor_price)),
                    stock=vendor_stock or 0,
                )

                pm.store_price = new_price
                pm.store_stock = new_stock
                pm.sync_status = 'scraped'
                pm.failed_sync_count = 0
                pm.last_scrape_time = now
                pm.scrape_error = None
                save_fields = [
                    'store_price',
                    'store_stock',
                    'sync_status',
                    'failed_sync_count',
                    'last_scrape_time',
                    'scrape_error',
                ]
                if scrape_title:
                    pm.title = scrape_title
                    save_fields.append('title')
                pm.save(update_fields=save_fields)
                succeeded += 1
            except Exception as apply_err:
                logger.exception(
                    'Pricing/inventory apply failed for SKU %s (store=%s): %s',
                    product.vendor_sku,
                    store.id,
                    apply_err,
                )
                _fail_mapping(pm, 'pricing_apply_error', str(apply_err))
                failed += 1
                continue
    finally:
        close_amazon_session(session)

    append_catalog_log(
        store.id,
        f'Store-wide vendor scrape finished at {timezone.now().strftime("%Y-%m-%d %H:%M:%S %Z")}. '
        f'{succeeded} listing(s) updated, {failed} failed, {processed} processed.',
        action_type='scrape_end',
        metadata={'rows_succeeded': succeeded, 'failed': failed, 'rows_processed': processed},
    )
    return {
        'store_id': str(store_id),
        'scope': 'store',
        'rows_processed': processed,
        'rows_succeeded': succeeded,
        'failed': failed,
        'error_summary': error_summary,
    }


@shared_task(bind=True, max_retries=3)
def catalog_scrape_task(self, upload_id: str):
    """Celery wrapper for run_catalog_scrape."""
    return run_catalog_scrape(upload_id)


@shared_task(bind=True, max_retries=3)
def catalog_scrape_store_task(self, store_id: str):
    """Celery: scrape all active listings for a store (no marketplace push)."""
    return run_store_wide_catalog_scrape(store_id)


def run_vevor_au_ingest(store_id: str | None = None, *, job_id: str | None = None) -> dict:
    """Refresh VendorPrice rows for Vevor AU products from the public S3 XLSX feed.

    Called by ``CatalogScrapeTriggerView`` whenever a store with Vevor AU
    products is scraped. Downloads the feed once, builds a SKU -> price/stock
    lookup, then writes the latest values to ``VendorPrice`` for every
    matching ``Product`` and refreshes the store's ``ProductMapping``
    (posted price + stock, with margin rules applied).

    Passing ``store_id=None`` updates every store that has Vevor AU listings.
    """
    from decimal import Decimal

    from scrapers.vevor_au import (
        VEVOR_AU_FEED_URL,
        fetch_vevor_feed,
        load_veror_via_excel_positions,
        lookup_sku,
    )
    from sync.tasks import (
        _apply_inventory,
        _apply_pricing,
        _get_inventory_for_vendor,
        _get_pricing_for_vendor,
    )
    from vendor.models import Vendor, VendorPrice
    from stores.models import Store

    vevor_codes = ('vevorau', 'vevor_au', 'vevor-au', 'vevor')
    vendor_ids = list(
        Vendor.objects.filter(code__iregex=r'^vevor(au|_au|-au)?$')
        .values_list('id', flat=True)
    )
    if not vendor_ids:
        return {'status': 'no_vendor', 'message': 'Vevor vendor not seeded.', 'updated': 0}

    try:
        xlsx_path = fetch_vevor_feed(VEVOR_AU_FEED_URL)
    except Exception as e:
        logger.exception('Vevor AU feed download failed: %s', e)
        return {'status': 'failed', 'error': str(e), 'updated': 0}

    try:
        lookup, lookup_compact, pos_rows = load_veror_via_excel_positions(xlsx_path)
    except Exception as e:
        logger.exception('Vevor AU feed parse failed: %s', e)
        return {'status': 'failed', 'error': str(e), 'updated': 0}
    finally:
        try:
            import os as _os
            _os.unlink(xlsx_path)
        except Exception:
            pass

    if not lookup:
        return {'status': 'empty_feed', 'feed_rows': pos_rows, 'updated': 0}

    pm_qs = ProductMapping.objects.filter(
        is_active=True,
        product__vendor_id__in=vendor_ids,
    ).select_related('product', 'product__vendor', 'store')
    if store_id:
        pm_qs = pm_qs.filter(store_id=store_id)

    now = timezone.now()
    matched = missing = updated_rows = 0

    for pm in pm_qs.iterator():
        product = pm.product
        if not product:
            continue
        raw_sku = (product.vendor_sku or '').strip()
        entry = lookup_sku(lookup, lookup_compact, raw_sku)
        if not entry:
            missing += 1
            continue
        matched += 1
        try:
            price = Decimal(str(entry['Posted Price'] or 0))
            stock_val = int(entry.get('Posted Inventory') or 0)
        except Exception:
            missing += 1
            continue

        VendorPrice.objects.create(product=product, price=price, stock=stock_val)

        try:
            store = pm.store
            pricing = _get_pricing_for_vendor(store, product.vendor_id)
            inventory = _get_inventory_for_vendor(store, product.vendor_id)
            new_price = _apply_pricing(
                price,
                pricing,
                pack_qty=getattr(pm, 'pack_qty', None),
                prep_fees=getattr(pm, 'prep_fees', None),
                shipping_fees=getattr(pm, 'shipping_fees', None),
            )
            if new_price is None:
                new_price = price
            new_stock = _apply_inventory(stock_val, inventory)
            pm.store_price = new_price
            pm.store_stock = new_stock
            pm.sync_status = 'scraped'
            pm.failed_sync_count = 0
            pm.last_scrape_time = now
            pm.save(update_fields=[
                'store_price', 'store_stock', 'sync_status',
                'failed_sync_count', 'last_scrape_time',
            ])
            updated_rows += 1
        except Exception as apply_err:
            logger.exception(
                'Vevor AU apply failed for SKU %s (store=%s): %s',
                product.vendor_sku, pm.store_id, apply_err,
            )

    result = {
        'status': 'ok',
        'feed_rows': pos_rows,
        'feed_unique_skus': len(lookup),
        'matched': matched,
        'missing': missing,
        'updated': updated_rows,
        'store_id': str(store_id) if store_id else None,
        'job_id': str(job_id) if job_id else None,
    }

    if job_id:
        try:
            from catalog.models import HebScrapeJob
            job = HebScrapeJob.objects.filter(id=job_id).first()
            if job and job.status != HebScrapeJob.Status.DONE:
                job.status = HebScrapeJob.Status.DONE
                job.completed_at = timezone.now()
                job.stats = {
                    'received': pos_rows,
                    'matched': matched,
                    'applied': updated_rows,
                }
                job.save(update_fields=['status', 'completed_at', 'stats'])
        except Exception:
            logger.exception('Failed to mark VevorAU job %s done', job_id)

    logger.info('Vevor AU ingest summary: %s', result)
    return result


@shared_task(bind=True, max_retries=3, name='catalog.run_vevor_au_ingest')
def vevor_au_ingest_task(self, store_id: str | None = None, job_id: str | None = None):
    """Celery entrypoint for the Vevor AU XLSX feed refresh."""
    return run_vevor_au_ingest(store_id=store_id, job_id=job_id)


@shared_task(bind=True, max_retries=3)
def catalog_update_task(self, upload_id: str):
    """
    Push to Reverb API: update price/inventory for active mappings, end listings for Delete rows.
    Uses marketplace_id (listing ID) or SKU lookup; Reverb stores try Marketplace Parent SKU first.
    """
    from .models import ReverbUpdateLog
    from store_adapters.reverb_adapter import ReverbAdapter, ReverbAPIError

    try:
        upload = CatalogUpload.objects.select_related('store', 'store__marketplace').get(id=upload_id)
    except CatalogUpload.DoesNotExist:
        return {'error': 'Upload not found', 'upload_id': upload_id}

    store = upload.store
    adapter = ReverbAdapter(store)

    # 1. End listings for Delete rows (soft-deleted mappings)
    for row in upload.rows.filter(
        action_raw__icontains='delete',
        product_mapping__isnull=False,
    ).select_related('product_mapping', 'product_mapping__product'):
        pm = row.product_mapping
        if not pm or pm.is_active:
            continue
        listing_id = pm.marketplace_id
        if not listing_id:
            for sku_candidate in listing_sku_lookup_order(pm, store):
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
    ).select_related('product_mapping', 'product_mapping__product')
    succeeded, failed = 0, 0

    for row in rows_to_update:
        pm = row.product_mapping
        if pm.store_price is None or pm.sync_status not in ('scraped', 'synced'):
            continue
        listing_id = pm.marketplace_id
        if not listing_id:
            for sku_candidate in listing_sku_lookup_order(pm, store):
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
            now_ok = timezone.now()
            pm.sync_status = 'synced'
            pm.last_sync_time = now_ok
            pm.save(update_fields=['sync_status', 'last_sync_time'])
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
