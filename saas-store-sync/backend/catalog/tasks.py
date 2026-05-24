"""
Catalog Celery tasks: sync, scrape, update.
"""
import logging
import time
import uuid
from datetime import timedelta

from celery import chord, group, shared_task
from django.conf import settings
from django.utils import timezone
from django.db import transaction
from django.db.models import Q
from decimal import Decimal

logger = logging.getLogger(__name__)

# If no server-scrapable listing leaves ``pending`` (scraped or failed) within this
# window, assume the scraper is stuck and stop early. Ingest-only rows do not
# count — the timer starts on the first non-ingest pending row.
def _stall_no_pending_timedelta() -> timedelta:
    """Wall time without moving a server-scrapable row off ``pending`` before we stop early."""
    try:
        m = int(getattr(settings, 'CATALOG_SCRAPE_STALL_MINUTES', 20) or 20)
    except (TypeError, ValueError):
        m = 20
    m = max(5, min(120, m))
    return timedelta(minutes=m)

from .celery_scrape_state import mark_celery_scrape_worker_started, should_abort_celery_scrape
from .models import (
    CatalogUpload,
    CatalogUploadRow,
    CatalogSyncLog,
    ProductMapping,
    StoreCatalogCeleryScrapeState,
)
from .reverb_catalog import listing_sku_lookup_order, store_is_reverb, vendor_is_ebay
from .services import _normalize
from products.models import Product
from vendor.models import Vendor


def _heb_us_runs_on_server() -> bool:
    """Return True when the US worker is configured to scrape HEB directly."""
    try:
        from scrapers import _heb_us_server_scrape_enabled

        return _heb_us_server_scrape_enabled()
    except Exception:  # pragma: no cover - defensive
        logger.exception("Failed to determine HEB US server-scrape mode; defaulting to ingest-only")
        return False


def _costco_au_runs_on_server() -> bool:
    """Return True when the AU worker is configured to scrape Costco AU directly.

    The deciding factor is whether ``COSTCO_AU_PROXY_URLS`` (or one of the
    accepted fallbacks) is set: residential proxies are mandatory to bypass
    Cloudflare, so without them Costco stays ingest-only.

    Read every call (not cached) so toggling the env on a running worker after
    a restart immediately changes routing without code redeploys.
    """
    try:
        from scrapers.costco_au_proxies import load_proxy_urls
        return bool(load_proxy_urls())
    except Exception:  # pragma: no cover - defensive
        logger.exception("Failed to determine Costco AU server-scrape mode; defaulting to ingest-only")
        return False


def _is_ingest_only_product(product) -> bool:
    """True when the vendor has no live server-side scraper for this deployment.

    HEB is ingest-only when neither proxies nor cookies-only mode is configured —
    set ``HEB_US_PROXY_URLS`` or ``HEB_COOKIES_ONLY=1`` with ``HEB_COOKIES_FILE``
    on the US worker for live server scrape. Vevor AU is always ingest-only (feed).
    Costco AU is
    ingest-only **only when** residential proxies are not configured — set
    ``COSTCO_AU_PROXY_URLS`` on the AU worker and Costco moves into the live
    server-scrape path.
    """
    vendor = getattr(product, 'vendor', None)
    code = (getattr(vendor, 'code', '') or '').lower()
    if code in ('vevor', 'vevorau'):
        return True
    if code.startswith('vevor_'):
        return True
    is_heb = code in ('heb', 'hebus') or code.startswith('heb_')
    if is_heb:
        return not _heb_us_runs_on_server()
    is_costco = (
        code in ('costcoau', 'costco_au', 'costco-au')
        or code.startswith('costco_')
    )
    if is_costco:
        return not _costco_au_runs_on_server()
    return False


def _ingest_only_vendor_ids() -> list:
    """Primary keys for vendors handled by feed/desktop ingest (not browser scrape).

    Costco AU joins this set only when proxies aren't configured (see
    ``_costco_au_runs_on_server``). HEB joins only when ``HEB_US_PROXY_URLS``
    is unset (see ``_heb_us_runs_on_server``).
    """
    codes = ['vevor', 'vevorau']
    prefix_q = Q(code__istartswith='vevor_')
    if not _heb_us_runs_on_server():
        codes.extend(['heb', 'hebus'])
        prefix_q = prefix_q | Q(code__istartswith='heb_')
    if not _costco_au_runs_on_server():
        codes.extend(['costcoau', 'costco_au', 'costco-au'])
        prefix_q = prefix_q | Q(code__istartswith='costco_')
    q = Q(code__in=codes) | prefix_q
    return list(Vendor.objects.filter(q).values_list('id', flat=True))


def store_has_scrapeable_pending_mappings(store) -> bool:
    """True when the store has pending listings that need live browser scraping."""
    ingest_ids = _ingest_only_vendor_ids()
    qs = ProductMapping.objects.filter(store=store, is_active=True, sync_status='pending')
    if ingest_ids:
        qs = qs.exclude(product__vendor_id__in=ingest_ids)
    return qs.exists()


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


class VendorResolveIndex:
    """O(1) vendor lookup for catalog sync — avoids ``Vendor.objects.all()`` per row."""

    __slots__ = ('by_name_lower', 'by_code_lower')

    def __init__(self, by_name_lower: dict, by_code_lower: dict):
        self.by_name_lower = by_name_lower
        self.by_code_lower = by_code_lower

    @classmethod
    def build(cls) -> 'VendorResolveIndex':
        by_name: dict[str, Vendor] = {}
        by_code: dict[str, Vendor] = {}
        for v in Vendor.objects.all().only('id', 'name', 'code'):
            if v.name:
                key = v.name.strip().lower()
                if key:
                    by_name[key] = v
            if v.code:
                ckey = v.code.strip().lower()
                if ckey:
                    by_code[ckey] = v
        return cls(by_name, by_code)


def _resolve_vendor(vendor_name_raw: str, *, index: VendorResolveIndex | None = None) -> Vendor | None:
    """Resolve vendor by name, code, or canonical alias."""
    from .services import resolve_canonical_vendor_code

    vn = _normalize(vendor_name_raw)
    if not vn:
        return None
    vn_lower = vn.lower()
    if index is not None:
        v = index.by_name_lower.get(vn_lower) or index.by_code_lower.get(vn_lower)
        if v:
            return v
        canon = resolve_canonical_vendor_code(vn)
        if canon:
            return index.by_code_lower.get(canon.strip().lower())
        return None
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


def _find_product_mapping(
    row: CatalogUploadRow,
    store,
    *,
    active_only: bool = True,
    vendor_index: VendorResolveIndex | None = None,
) -> ProductMapping | None:
    """Find ProductMapping by marketplace_id, marketplace SKUs, or vendor+product key."""
    vendor_early = row.vendor or _resolve_vendor(row.vendor_name_raw, index=vendor_index)
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
        vendor=vendor,
        vendor_sku=vsku,
        variation_id=vid,
        owner_id=store.user_id,
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
        owner_id=store.user_id,
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


def _chunked_reset_store_active_listings_pending_scrape(store) -> dict:
    """Mark all active listings pending rescrape after catalog sync — chunked UPDATEs only.

    A single store-wide UPDATE would lock/update every row at once and stall the API.
    Same semantics as before: ``sync_status='pending'``, clear scrape errors and counters.
    """
    batch = int(getattr(settings, 'CATALOG_POST_SYNC_PENDING_RESET_BATCH', 2500) or 2500)
    batch = max(200, min(batch, 20000))
    sleep_ms = int(getattr(settings, 'CATALOG_POST_SYNC_PENDING_RESET_SLEEP_MS', 0) or 0)
    sleep_ms = max(0, min(sleep_ms, 5000))

    t0 = time.perf_counter()
    total_updated = 0
    batches = 0
    last_id = None

    while True:
        q = (
            ProductMapping.objects.filter(store=store, is_active=True)
            .order_by('id')
            .values_list('id', flat=True)
        )
        if last_id is not None:
            q = q.filter(id__gt=last_id)
        ids = list(q[:batch])
        if not ids:
            break
        updated = ProductMapping.objects.filter(id__in=ids).update(
            sync_status='pending',
            failed_sync_count=0,
            scrape_error=None,
        )
        total_updated += updated
        batches += 1
        last_id = ids[-1]
        if sleep_ms:
            time.sleep(sleep_ms / 1000.0)

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    logger.info(
        'catalog post-sync pending reset: store_id=%s batches=%s rows_updated=%s '
        'elapsed_ms=%s batch_size=%s sleep_ms=%s',
        store.id,
        batches,
        total_updated,
        elapsed_ms,
        batch,
        sleep_ms,
    )
    return {
        'batches': batches,
        'rows_updated': total_updated,
        'elapsed_ms': elapsed_ms,
        'batch_size': batch,
    }


def run_catalog_sync(upload_id: str, *, replace_store_catalog: bool = False):
    """
    Sync CatalogUpload rows: Add/Update/Delete Product and ProductMapping.
    Creates CatalogSyncLog per row. Call directly or via catalog_sync_task.

    When replace_store_catalog is True, deactivate all active listings on the
    store before applying the file so the store reflects only this upload.
    """
    sync_log_batch = int(getattr(settings, 'CATALOG_SYNC_LOG_BATCH', 32) or 32)
    progress_every = int(getattr(settings, 'CATALOG_SYNC_PROGRESS_EVERY', 32) or 32)

    try:
        upload = CatalogUpload.objects.select_related('store', 'store__marketplace').get(id=upload_id)
    except CatalogUpload.DoesNotExist:
        return {'error': 'Upload not found', 'upload_id': upload_id}

    if upload.status == CatalogUpload.Status.INGESTING:
        return {
            'error': 'upload_still_ingesting',
            'message': 'Wait for file ingest to finish before syncing.',
            'upload_id': str(upload_id),
        }

    if (
        upload.user_id
        and upload.store.user_id
        and upload.user_id != upload.store.user_id
    ):
        return {
            'error': 'upload_store_user_mismatch',
            'message': 'Catalog upload must belong to the same user as the store.',
            'upload_id': str(upload_id),
        }

    store = upload.store
    replace_deactivated = 0
    if replace_store_catalog:
        replace_deactivated = ProductMapping.objects.filter(
            store=store,
            is_active=True,
        ).update(is_active=False)
        logger.info(
            'replace_store_catalog store_id=%s deactivated=%s upload_id=%s',
            store.id,
            replace_deactivated,
            upload_id,
        )

    upload.status = CatalogUpload.Status.PROCESSING
    upload.save(update_fields=['status'])
    added, updated, deleted, errors = 0, 0, 0, 0

    vendor_index = VendorResolveIndex.build()
    rows_iter = (
        upload.rows.select_related('vendor')
        .order_by('row_number')
        .iterator(chunk_size=200)
    )

    def _action_for_log(a: str) -> str:
        return a if a in {x.value for x in CatalogSyncLog.Action} else 'add'

    log_buffer: list[CatalogSyncLog] = []

    def _flush_logs() -> None:
        if not log_buffer:
            return
        CatalogSyncLog.objects.bulk_create(log_buffer, batch_size=100)
        log_buffer.clear()

    processed = 0
    for row in rows_iter:
        action = _normalize_action(row.action_raw)
        log_status = CatalogSyncLog.Status.SUCCESS
        log_message = None

        try:
            with transaction.atomic():
                if action == 'delete':
                    pm = _find_product_mapping(row, store, active_only=False, vendor_index=vendor_index)
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
                    vendor = row.vendor or _resolve_vendor(row.vendor_name_raw, index=vendor_index)
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
                        pm = _find_product_mapping(row, store, vendor_index=vendor_index)
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
            log_buffer.append(
                CatalogSyncLog(
                    id=uuid.uuid4(),
                    catalog_upload=upload,
                    catalog_upload_row=row,
                    action=_action_for_log(action),
                    status=log_status,
                    message=log_message,
                )
            )
            processed += 1
            if len(log_buffer) >= sync_log_batch:
                _flush_logs()
            if progress_every > 0 and processed % progress_every == 0:
                upload.processed_rows = processed
                upload.save(update_fields=['processed_rows'])
        except Exception as e:
            row.sync_status = CatalogUploadRow.SyncStatus.ERROR
            row.sync_error = str(e)
            row.save(update_fields=['sync_status', 'sync_error'])
            log_buffer.append(
                CatalogSyncLog(
                    id=uuid.uuid4(),
                    catalog_upload=upload,
                    catalog_upload_row=row,
                    action=_action_for_log(action),
                    status=CatalogSyncLog.Status.ERROR,
                    message=str(e),
                )
            )
            errors += 1
            processed += 1
            if len(log_buffer) >= sync_log_batch:
                _flush_logs()
            if progress_every > 0 and processed % progress_every == 0:
                upload.processed_rows = processed
                upload.save(update_fields=['processed_rows'])

    _flush_logs()
    upload.processed_rows = processed

    # Final upload status
    if errors and upload.processed_rows < upload.total_rows:
        upload.status = CatalogUpload.Status.PARTIAL
    elif errors:
        upload.status = CatalogUpload.Status.FAILED
    else:
        upload.status = CatalogUpload.Status.SYNCED
    upload.error_summary = f"Added: {added}, Updated: {updated}, Deleted: {deleted}, Errors: {errors}" if errors else None
    upload.save(update_fields=['status', 'error_summary', 'processed_rows'])

    reset_stats = None
    # After a successful sync, all active listings need a fresh vendor scrape.
    # Failed rows on the file do not block this — users fix those separately.
    if upload.status in (CatalogUpload.Status.SYNCED, CatalogUpload.Status.PARTIAL):
        reset_stats = _chunked_reset_store_active_listings_pending_scrape(store)
        logger.info(
            'catalog sync complete upload_id=%s store_id=%s pending_reset_batches=%s',
            upload_id,
            store.id,
            reset_stats.get('batches'),
        )

    return {
        'upload_id': str(upload_id),
        'status': upload.status,
        'added': added,
        'updated': updated,
        'deleted': deleted,
        'errors': errors,
        'replace_deactivated': replace_deactivated,
        'pending_reset_batches': reset_stats.get('batches') if reset_stats else None,
        'pending_reset_rows': reset_stats.get('rows_updated') if reset_stats else None,
        'pending_reset_elapsed_ms': reset_stats.get('elapsed_ms') if reset_stats else None,
    }


@shared_task(bind=True, name='catalog.ingest_upload_file', max_retries=2, default_retry_delay=90)
def catalog_ingest_upload_file_task(self, upload_id: str):
    """
    Parse stored CSV/XLSX in chunks (bulk_create rows). Replaces request-time ingest.
    """
    from catalog.models import CatalogUpload

    from .services import ingest_stored_catalog_file

    try:
        return ingest_stored_catalog_file(upload_id)
    except CatalogUpload.DoesNotExist:
        logger.warning('catalog ingest: upload id=%s not found', upload_id)
        return {'error': 'upload_not_found', 'upload_id': str(upload_id)}
    except Exception as exc:
        # Worker crash, bug, or exception outside ingest_stored_catalog_file inner handlers —
        # avoid leaving the upload stuck in INGESTING forever.
        logger.exception('catalog ingest fatal upload_id=%s', upload_id)
        try:
            u = CatalogUpload.objects.get(id=upload_id)
            if u.status == CatalogUpload.Status.INGESTING:
                u.status = CatalogUpload.Status.FAILED
                u.error_summary = (str(exc) or 'Ingest failed unexpectedly.')[:2000]
                u.save(update_fields=['status', 'error_summary'])
        except CatalogUpload.DoesNotExist:
            pass
        return {'error': str(exc), 'upload_id': str(upload_id)}


@shared_task(bind=True, max_retries=3)
def catalog_sync_task(self, upload_id: str, replace_store_catalog: bool = False):
    """Celery wrapper for run_catalog_sync."""
    return run_catalog_sync(upload_id, replace_store_catalog=replace_store_catalog)


def _process_catalog_upload_scrape_rows(rows, *, upload, store, upload_id, session, run, emit_stall_log: bool):
    """Shared loop for upload-scrape. *run* None = parallel chunk (skip ScrapeRun writes)."""
    from catalog.activity_log import append_catalog_log
    from stores.pricing_tiers import resolve_margin_tier_for_raw_cost
    from sync.tasks import (
        _apply_inventory,
        _apply_pricing,
        _build_store_vendor_pricing_inventory_caches,
        _fail_mapping,
        _get_inventory_for_vendor_from_cache,
        _get_pricing_for_vendor_from_cache,
        _has_fixed_tier,
        _inventory_from_scrape_result,
        _missing_fixed_inputs,
        resolve_vendor_scrape_url,
    )
    from vendor.models import VendorPrice
    from scrapers import get_price_and_stock

    succeeded = 0
    failed = 0
    stalled_out = False
    user_cancelled = False
    fatal_error = None
    last_progress_at = None
    now = timezone.now()
    rows_visited = 0
    row_counter = 0

    mark_celery_scrape_worker_started(str(store.id))

    try:
        price_by_vid, price_fb, inv_by_vid, inv_fb = _build_store_vendor_pricing_inventory_caches(store)
        for row in rows:
            if should_abort_celery_scrape(str(store.id)):
                user_cancelled = True
                break
            pm = row.product_mapping
            product = pm.product
            if not product:
                continue
            if pm.sync_status != 'pending':
                continue

            rows_visited += 1
            row_counter += 1
            if run is not None:
                run.rows_processed += 1
                if run.rows_processed % 10 == 0:
                    run.rows_succeeded = succeeded
                    run.save(update_fields=['rows_processed', 'rows_succeeded'])

            if _is_ingest_only_product(product):
                logger.info(
                    "Ingest-only row left untouched — awaiting fresh scrape (sku=%s vendor=%s)",
                    getattr(product, 'vendor_sku', '?'),
                    (product.vendor.code if product.vendor else '?'),
                )
                continue

            now_ts = timezone.now()
            if last_progress_at is None:
                last_progress_at = now_ts
            elif now_ts - last_progress_at > _stall_no_pending_timedelta():
                stalled_out = True
                logger.warning(
                    'Catalog scrape stalled for upload %s store %s: no listing left Pending '
                    'within %s.',
                    upload_id,
                    store.id,
                    _stall_no_pending_timedelta(),
                )
                if emit_stall_log:
                    append_catalog_log(
                        store.id,
                        f'Vendor scrape stopped early: nothing moved off Pending for '
                        f'{int(_stall_no_pending_timedelta().total_seconds() // 60)} minutes '
                        f'(scraper may be hung or blocked). Remaining rows stay Pending.',
                        action_type='scrape_stalled',
                        metadata={'upload_id': str(upload_id), 'scope': 'upload'},
                    )
                break

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
                last_progress_at = timezone.now()
                continue

            scrape_title = ''
            logger.info(
                "Scraping row %d: sku=%s vendor=%s region=%s url=%s",
                row_counter,
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
                last_progress_at = timezone.now()
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
                last_progress_at = timezone.now()
                continue

            if vendor_stock is None or vendor_stock < 0:
                vendor_stock = 0

            try:
                pricing = _get_pricing_for_vendor_from_cache(product.vendor_id, price_by_vid, price_fb)
                inventory = _get_inventory_for_vendor_from_cache(product.vendor_id, inv_by_vid, inv_fb)

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
                            last_progress_at = timezone.now()
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
                last_progress_at = timezone.now()
            except Exception as apply_err:
                logger.exception(
                    'Pricing/inventory apply failed for SKU %s (store=%s): %s',
                    product.vendor_sku, store.id, apply_err,
                )
                _fail_mapping(pm, 'pricing_apply_error', str(apply_err))
                failed += 1
                last_progress_at = timezone.now()
                continue
    except Exception as loop_err:
        fatal_error = str(loop_err)
        logger.exception('Catalog scrape aborted: %s', loop_err)

    return {
        'succeeded': succeeded,
        'failed': failed,
        'stalled_out': stalled_out,
        'fatal_error': fatal_error,
        'rows_visited': rows_visited,
        'user_cancelled': user_cancelled,
    }


def run_catalog_scrape(upload_id: str, *, parallel: bool = False) -> dict:
    """
    Scrape vendor URLs for rows in upload, apply pricing/inventory rules, update ProductMapping.

    Only ``ProductMapping`` rows with ``sync_status='pending'`` are processed; successfully
    scraped rows become ``scraped``, failures become ``failed`` / ``needs_attention``.
    When nothing is left pending, this run simply finishes (no further passes).

    If no server-scraped listing leaves ``pending`` within
    ``CATALOG_SCRAPE_STALL_MINUTES`` (default 20), the run stops early;
    ingest-only rows do not start that timer until the first live-scrape row.

    When *parallel* is True (Celery entrypoint) and ``CATALOG_SCRAPE_CHUNK_SIZE`` > 0 and
    there are more pending rows than the chunk size, work is split across a chord of tasks
    (each with its own Amazon/eBay session).
    """
    from scrapers import close_amazon_session
    from sync.models import ScrapeRun

    from catalog.activity_log import append_catalog_log

    try:
        upload = CatalogUpload.objects.select_related('store', 'store__marketplace').get(id=upload_id)
    except CatalogUpload.DoesNotExist:
        return {'error': 'Upload not found', 'upload_id': upload_id}

    store = upload.store
    append_catalog_log(
        store.id,
        f'Vendor scrape started for upload “{upload.original_filename}” at '
        f'{timezone.now().strftime("%Y-%m-%d %H:%M:%S %Z")}.',
        action_type='scrape_start',
        metadata={'upload_id': str(upload_id), 'scope': 'upload'},
    )

    rows_qs = upload.rows.filter(
        product_mapping__isnull=False,
        product_mapping__is_active=True,
        product_mapping__sync_status='pending',
    ).order_by('row_number')
    ingest_ids = _ingest_only_vendor_ids()
    if ingest_ids:
        rows_qs = rows_qs.exclude(product_mapping__product__vendor_id__in=ingest_ids)

    row_ids = list(rows_qs.values_list('id', flat=True))
    chunk_sz = int(getattr(settings, 'CATALOG_SCRAPE_CHUNK_SIZE', 0) or 0)
    use_parallel = bool(parallel and chunk_sz > 0 and len(row_ids) > chunk_sz)

    if use_parallel:
        run = ScrapeRun.objects.create(
            catalog_upload=upload,
            store=store,
            status=ScrapeRun.Status.RUNNING,
        )
        chunks = [row_ids[i : i + chunk_sz] for i in range(0, len(row_ids), chunk_sz)]
        chunk_sigs = [
            catalog_scrape_upload_chunk_task.si(str(upload_id), str(run.id), [str(x) for x in ch])
            for ch in chunks
        ]
        chord(group(chunk_sigs))(catalog_scrape_upload_finalize.s(str(upload_id), str(run.id)))
        return {
            'upload_id': str(upload_id),
            'run_id': str(run.id),
            'parallel': True,
            'chunks': len(chunks),
            'status': 'running',
            'message': 'Scrape running in parallel chunks; see activity log when complete.',
        }

    run = ScrapeRun.objects.create(
        catalog_upload=upload,
        store=store,
        status=ScrapeRun.Status.RUNNING,
    )
    session = {}
    succeeded = failed = 0
    fatal_error = None
    stalled_out = False
    user_cancelled = False

    try:
        stats = _process_catalog_upload_scrape_rows(
            rows_qs.select_related(
                'product_mapping', 'product_mapping__product', 'product_mapping__product__vendor',
            ),
            upload=upload,
            store=store,
            upload_id=upload_id,
            session=session,
            run=run,
            emit_stall_log=True,
        )
        succeeded = stats['succeeded']
        failed = stats['failed']
        stalled_out = stats['stalled_out']
        fatal_error = stats['fatal_error']
        user_cancelled = stats.get('user_cancelled')
    finally:
        close_amazon_session(session)

    run.finished_at = timezone.now()
    run.rows_succeeded = succeeded
    if fatal_error:
        run.status = ScrapeRun.Status.FAILED
        run.error_summary = fatal_error[:2000]
    elif user_cancelled:
        run.status = (
            ScrapeRun.Status.PARTIAL
            if (succeeded > 0 or failed > 0)
            else ScrapeRun.Status.SUCCESS
        )
        run.error_summary = 'Stopped by user.'
    elif stalled_out:
        run.status = (
            ScrapeRun.Status.PARTIAL
            if (succeeded > 0 or failed > 0)
            else ScrapeRun.Status.FAILED
        )
        run.error_summary = (
            f'Stalled: no listing left Pending within {int(_stall_no_pending_timedelta().total_seconds() // 60)} minutes.'
        )
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
        'stalled': stalled_out,
    }
    if fatal_error:
        out['error'] = fatal_error
    finish_msg = (
        f'Vendor scrape finished at {timezone.now().strftime("%Y-%m-%d %H:%M:%S %Z")}. '
        f'{succeeded} row(s) updated, {failed} failed, {run.rows_processed} processed.'
    )
    if stalled_out:
        finish_msg += (
            f' Stopped early: no progress moving listings off Pending for '
            f'{int(_stall_no_pending_timedelta().total_seconds() // 60)} minutes.'
        )
    if user_cancelled:
        append_catalog_log(
            store.id,
            'Price checks stopped because you clicked Stop.',
            action_type='scrape_cancelled',
            metadata={'upload_id': str(upload_id), 'scope': 'upload'},
        )
        finish_msg += ' Stopped because you clicked Stop.'
    append_catalog_log(
        store.id,
        finish_msg,
        action_type='scrape_end',
        metadata={
            'rows_succeeded': succeeded,
            'failed': failed,
            'upload_id': str(upload_id),
            'stalled': stalled_out,
        },
    )
    return out


@shared_task(bind=True, max_retries=3)
def catalog_scrape_upload_chunk_task(self, upload_id: str, scrape_run_id: str, row_ids: list):
    """Process one chunk of catalog upload rows (parallel scrape)."""
    from scrapers import close_amazon_session

    try:
        upload = CatalogUpload.objects.select_related('store', 'store__marketplace').get(id=upload_id)
    except CatalogUpload.DoesNotExist:
        return {
            'error': 'Upload not found',
            'succeeded': 0,
            'failed': 0,
            'stalled_out': False,
            'rows_visited': 0,
            'user_cancelled': False,
        }

    store = upload.store
    session = {}
    try:
        rows = (
            CatalogUploadRow.objects.filter(id__in=row_ids)
            .select_related('product_mapping', 'product_mapping__product', 'product_mapping__product__vendor')
            .order_by('row_number')
        )
        stats = _process_catalog_upload_scrape_rows(
            rows,
            upload=upload,
            store=store,
            upload_id=upload_id,
            session=session,
            run=None,
            emit_stall_log=False,
        )
    finally:
        close_amazon_session(session)
    return stats


@shared_task
def catalog_scrape_upload_finalize(results, upload_id: str, scrape_run_id: str):
    """Chord callback: finalize ScrapeRun + activity log after all upload chunks finish."""
    from sync.models import ScrapeRun

    from catalog.activity_log import append_catalog_log
    from catalog.celery_scrape_state import clear_celery_scrape_state

    try:
        upload = CatalogUpload.objects.select_related('store', 'store__marketplace').get(id=upload_id)
    except CatalogUpload.DoesNotExist:
        return {'error': 'Upload not found', 'upload_id': upload_id}

    store = upload.store
    try:
        run = ScrapeRun.objects.get(id=scrape_run_id, catalog_upload=upload)
    except ScrapeRun.DoesNotExist:
        clear_celery_scrape_state(str(store.id))
        return {'error': 'ScrapeRun not found', 'upload_id': upload_id}

    try:
        succeeded = failed = rows_processed = 0
        stalled_out = False
        user_cancelled = False
        fatal_parts: list[str] = []

        if not isinstance(results, list):
            results = []

        for r in results:
            if isinstance(r, Exception):
                fatal_parts.append(str(r))
                continue
            if not isinstance(r, dict):
                continue
            if r.get('fatal_error'):
                fatal_parts.append(str(r['fatal_error']))
            if r.get('error'):
                fatal_parts.append(str(r['error']))
            succeeded += int(r.get('succeeded', 0))
            failed += int(r.get('failed', 0))
            stalled_out = stalled_out or bool(r.get('stalled_out'))
            rows_processed += int(r.get('rows_visited', 0))
            user_cancelled = user_cancelled or bool(r.get('user_cancelled'))

        fatal_error = '; '.join(fatal_parts) if fatal_parts else None

        run.finished_at = timezone.now()
        run.rows_processed = rows_processed
        run.rows_succeeded = succeeded
        if fatal_error:
            run.status = ScrapeRun.Status.FAILED
            run.error_summary = fatal_error[:2000]
        elif user_cancelled:
            run.status = (
                ScrapeRun.Status.PARTIAL
                if (succeeded > 0 or failed > 0)
                else ScrapeRun.Status.SUCCESS
            )
            run.error_summary = 'Stopped by user.'
        elif stalled_out:
            run.status = (
                ScrapeRun.Status.PARTIAL
                if (succeeded > 0 or failed > 0)
                else ScrapeRun.Status.FAILED
            )
            run.error_summary = (
                f'Stalled: no listing left Pending within {int(_stall_no_pending_timedelta().total_seconds() // 60)} minutes.'
            )
        else:
            run.status = ScrapeRun.Status.FAILED if succeeded == 0 and rows_processed > 0 else (
                ScrapeRun.Status.PARTIAL if failed else ScrapeRun.Status.SUCCESS
            )
        run.save()

        finish_msg = (
            f'Vendor scrape finished at {timezone.now().strftime("%Y-%m-%d %H:%M:%S %Z")}. '
            f'{succeeded} row(s) updated, {failed} failed, {rows_processed} processed (parallel chunks).'
        )
        if stalled_out:
            finish_msg += (
                f' Stopped early: no progress moving listings off Pending for '
                f'{int(_stall_no_pending_timedelta().total_seconds() // 60)} minutes.'
            )
        if user_cancelled:
            finish_msg += ' Stopped because you clicked Stop.'
            append_catalog_log(
                store.id,
                'Price checks stopped because you clicked Stop.',
                action_type='scrape_cancelled',
                metadata={'upload_id': str(upload_id), 'scope': 'upload', 'parallel': True},
            )
        append_catalog_log(
            store.id,
            finish_msg,
            action_type='scrape_end',
            metadata={
                'rows_succeeded': succeeded,
                'failed': failed,
                'upload_id': str(upload_id),
                'stalled': stalled_out,
                'parallel': True,
            },
        )
        return {
            'upload_id': str(upload_id),
            'run_id': str(run.id),
            'status': run.status,
            'rows_processed': rows_processed,
            'rows_succeeded': succeeded,
            'failed': failed,
            'stalled': stalled_out,
        }
    finally:
        clear_celery_scrape_state(str(store.id))


def _process_store_wide_scrape_mappings(mappings, *, store, store_id, session, emit_stall_log: bool):
    """Inner loop for store-wide scrape (single process or one parallel chunk)."""
    from decimal import Decimal

    from catalog.activity_log import append_catalog_log
    from stores.pricing_tiers import resolve_margin_tier_for_raw_cost
    from sync.tasks import (
        _apply_inventory,
        _apply_pricing,
        _build_store_vendor_pricing_inventory_caches,
        _fail_mapping,
        _get_inventory_for_vendor_from_cache,
        _get_pricing_for_vendor_from_cache,
        _has_fixed_tier,
        _inventory_from_scrape_result,
        _missing_fixed_inputs,
        resolve_vendor_scrape_url,
    )
    from vendor.models import VendorPrice
    from scrapers import get_price_and_stock

    processed = succeeded = failed = 0
    now = timezone.now()
    error_summary = None
    stalled_out = False
    user_cancelled = False
    last_progress_at = None
    fatal_error = None

    mark_celery_scrape_worker_started(str(store.id))

    try:
        price_by_vid, price_fb, inv_by_vid, inv_fb = _build_store_vendor_pricing_inventory_caches(store)
        for pm in mappings:
            if should_abort_celery_scrape(str(store.id)):
                user_cancelled = True
                break
            processed += 1
            product = pm.product
            if not product:
                continue
            if _is_ingest_only_product(product):
                logger.info(
                    "Ingest-only row (store-wide) left untouched — awaiting fresh scrape (sku=%s vendor=%s)",
                    getattr(product, 'vendor_sku', '?'),
                    (product.vendor.code if product.vendor else '?'),
                )
                continue

            now_ts = timezone.now()
            if last_progress_at is None:
                last_progress_at = now_ts
            elif now_ts - last_progress_at > _stall_no_pending_timedelta():
                stalled_out = True
                stall_msg = (
                    f'no listing left Pending within '
                    f'{int(_stall_no_pending_timedelta().total_seconds() // 60)} minutes'
                )
                logger.warning(
                    'Store-wide scrape stalled for store %s: %s.',
                    store.id,
                    stall_msg,
                )
                if emit_stall_log:
                    append_catalog_log(
                        store.id,
                        f'Store-wide vendor scrape stopped early: {stall_msg} '
                        f'(scraper may be hung or blocked). Remaining server-scrapable rows stay Pending.',
                        action_type='scrape_stalled',
                        metadata={'scope': 'store'},
                    )
                error_summary = stall_msg if not error_summary else error_summary
                break

            pricing = _get_pricing_for_vendor_from_cache(product.vendor_id, price_by_vid, price_fb)
            inventory = _get_inventory_for_vendor_from_cache(product.vendor_id, inv_by_vid, inv_fb)

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
                last_progress_at = timezone.now()
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
                last_progress_at = timezone.now()
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
                            last_progress_at = timezone.now()
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
                last_progress_at = timezone.now()
            except Exception as apply_err:
                logger.exception(
                    'Pricing/inventory apply failed for SKU %s (store=%s): %s',
                    product.vendor_sku,
                    store.id,
                    apply_err,
                )
                _fail_mapping(pm, 'pricing_apply_error', str(apply_err))
                failed += 1
                last_progress_at = timezone.now()
                continue
    except Exception as loop_err:
        fatal_error = str(loop_err)
        logger.exception('Store-wide scrape aborted: %s', loop_err)

    return {
        'rows_processed': processed,
        'rows_succeeded': succeeded,
        'failed': failed,
        'error_summary': error_summary,
        'stalled': stalled_out,
        'fatal_error': fatal_error,
        'user_cancelled': user_cancelled,
    }


def run_store_wide_catalog_scrape(store_id: str, *, parallel: bool = False) -> dict:
    """
    Scrape vendor URLs for active listings whose ``sync_status`` is ``pending`` only.

    Parallel mode (Celery + ``CATALOG_SCRAPE_CHUNK_SIZE``) splits pending mappings across
    tasks, each with its own Amazon/eBay session.
    """
    from scrapers import close_amazon_session
    from stores.models import Store

    from catalog.activity_log import append_catalog_log

    try:
        store = Store.objects.select_related('marketplace').get(id=store_id)
    except Store.DoesNotExist:
        return {'error': 'store_not_found', 'store_id': str(store_id)}

    append_catalog_log(
        store.id,
        f'Store-wide vendor scrape started at {timezone.now().strftime("%Y-%m-%d %H:%M:%S %Z")} '
        f'for active listings with sync_status=pending.',
        action_type='scrape_start',
        metadata={'scope': 'store'},
    )

    base_qs = ProductMapping.objects.filter(
        store=store, is_active=True, sync_status='pending',
    ).order_by('id')
    ingest_ids = _ingest_only_vendor_ids()
    if ingest_ids:
        base_qs = base_qs.exclude(product__vendor_id__in=ingest_ids)

    mapping_ids = list(base_qs.values_list('id', flat=True))
    if not mapping_ids:
        append_catalog_log(
            store.id,
            'Store-wide browser scrape skipped: no pending live-scrape listings '
            '(feed/desktop vendors are handled separately).',
            action_type='scrape_end',
            metadata={'scope': 'store', 'skipped': 'ingest_only_only'},
        )
        return {
            'store_id': str(store_id),
            'scope': 'store',
            'rows_processed': 0,
            'rows_succeeded': 0,
            'failed': 0,
            'skipped': True,
            'reason': 'no_scrapeable_pending',
        }
    chunk_sz = int(getattr(settings, 'CATALOG_SCRAPE_CHUNK_SIZE', 0) or 0)
    use_parallel = bool(parallel and chunk_sz > 0 and len(mapping_ids) > chunk_sz)

    if use_parallel:
        chunks = [mapping_ids[i : i + chunk_sz] for i in range(0, len(mapping_ids), chunk_sz)]
        sigs = [
            catalog_scrape_store_chunk_task.si(str(store_id), [str(x) for x in ch])
            for ch in chunks
        ]
        chord(group(sigs))(catalog_scrape_store_finalize.s(str(store_id)))
        return {
            'store_id': str(store_id),
            'scope': 'store',
            'parallel': True,
            'chunks': len(chunks),
            'status': 'running',
            'message': 'Store-wide scrape running in parallel chunks; see activity log when complete.',
        }

    session: dict = {}
    try:
        stats = _process_store_wide_scrape_mappings(
            base_qs.select_related('product', 'product__vendor'),
            store=store,
            store_id=store_id,
            session=session,
            emit_stall_log=True,
        )
    finally:
        close_amazon_session(session)

    succeeded = stats['rows_succeeded']
    failed = stats['failed']
    processed = stats['rows_processed']
    error_summary = stats['error_summary']
    stalled_out = stats['stalled']
    user_cancelled = stats.get('user_cancelled')

    end_meta = {
        'rows_succeeded': succeeded,
        'failed': failed,
        'rows_processed': processed,
        'stalled': stalled_out,
        'user_cancelled': bool(user_cancelled),
    }
    end_msg = (
        f'Store-wide vendor scrape finished at {timezone.now().strftime("%Y-%m-%d %H:%M:%S %Z")}. '
        f'{succeeded} listing(s) updated, {failed} failed, {processed} processed.'
    )
    if stalled_out:
        end_msg += (
            f' Stopped early: no progress moving listings off Pending for '
            f'{int(_stall_no_pending_timedelta().total_seconds() // 60)} minutes.'
        )
    if user_cancelled:
        append_catalog_log(
            store.id,
            'Price checks stopped because you clicked Stop.',
            action_type='scrape_cancelled',
            metadata={'scope': 'store'},
        )
        end_msg += ' Stopped because you clicked Stop.'
    append_catalog_log(
        store.id,
        end_msg,
        action_type='scrape_end',
        metadata=end_meta,
    )
    return {
        'store_id': str(store_id),
        'scope': 'store',
        'rows_processed': processed,
        'rows_succeeded': succeeded,
        'failed': failed,
        'error_summary': error_summary,
        'stalled': stalled_out,
    }


@shared_task(bind=True, max_retries=3)
def catalog_scrape_store_chunk_task(self, store_id: str, mapping_ids: list):
    from scrapers import close_amazon_session
    from stores.models import Store

    try:
        store = Store.objects.select_related('marketplace').get(id=store_id)
    except Store.DoesNotExist:
        return {
            'error': 'store_not_found',
            'rows_processed': 0,
            'rows_succeeded': 0,
            'failed': 0,
            'error_summary': None,
            'stalled': False,
            'user_cancelled': False,
        }

    session: dict = {}
    try:
        mappings = (
            ProductMapping.objects.filter(id__in=mapping_ids, store=store)
            .select_related('product', 'product__vendor')
            .order_by('id')
        )
        stats = _process_store_wide_scrape_mappings(
            mappings,
            store=store,
            store_id=store_id,
            session=session,
            emit_stall_log=False,
        )
    finally:
        close_amazon_session(session)
    return stats


@shared_task
def catalog_scrape_store_finalize(results, store_id: str):
    from stores.models import Store

    from catalog.activity_log import append_catalog_log
    from catalog.celery_scrape_state import clear_celery_scrape_state

    try:
        store = Store.objects.select_related('marketplace').get(id=store_id)
    except Store.DoesNotExist:
        return {'error': 'store_not_found', 'store_id': str(store_id)}

    try:
        processed = succeeded = failed = 0
        stalled_out = False
        user_cancelled = False
        error_summary = None
        fatal_parts: list[str] = []

        if not isinstance(results, list):
            results = []

        for r in results:
            if isinstance(r, Exception):
                fatal_parts.append(str(r))
                continue
            if not isinstance(r, dict):
                continue
            if r.get('fatal_error'):
                fatal_parts.append(str(r['fatal_error']))
            if r.get('error'):
                fatal_parts.append(str(r['error']))
            succeeded += int(r.get('rows_succeeded', 0))
            failed += int(r.get('failed', 0))
            processed += int(r.get('rows_processed', 0))
            stalled_out = stalled_out or bool(r.get('stalled'))
            user_cancelled = user_cancelled or bool(r.get('user_cancelled'))
            es = r.get('error_summary')
            if es and not error_summary:
                error_summary = str(es)

        end_meta = {
            'rows_succeeded': succeeded,
            'failed': failed,
            'rows_processed': processed,
            'stalled': stalled_out,
            'parallel': True,
            'user_cancelled': user_cancelled,
        }
        end_msg = (
            f'Store-wide vendor scrape finished at {timezone.now().strftime("%Y-%m-%d %H:%M:%S %Z")}. '
            f'{succeeded} listing(s) updated, {failed} failed, {processed} processed (parallel chunks).'
        )
        if fatal_parts:
            end_msg += ' Errors: ' + '; '.join(fatal_parts[:5])
        if stalled_out:
            end_msg += (
                f' Stopped early: no progress moving listings off Pending for '
                f'{int(_stall_no_pending_timedelta().total_seconds() // 60)} minutes.'
            )
        if user_cancelled:
            end_msg += ' Stopped because you clicked Stop.'
            append_catalog_log(
                store.id,
                'Price checks stopped because you clicked Stop.',
                action_type='scrape_cancelled',
                metadata={'scope': 'store', 'parallel': True},
            )
        append_catalog_log(
            store.id,
            end_msg,
            action_type='scrape_end',
            metadata=end_meta,
        )
        return {
            'store_id': str(store_id),
            'scope': 'store',
            'rows_processed': processed,
            'rows_succeeded': succeeded,
            'failed': failed,
            'error_summary': error_summary or ('; '.join(fatal_parts) if fatal_parts else None),
            'stalled': stalled_out,
        }
    finally:
        clear_celery_scrape_state(str(store_id))


@shared_task(bind=True, max_retries=3)
def catalog_scrape_task(self, upload_id: str):
    """Celery wrapper for run_catalog_scrape."""
    from catalog.celery_scrape_state import clear_celery_scrape_state
    from catalog.models import CatalogUpload

    store_id = None
    try:
        store_id = str(CatalogUpload.objects.values_list('store_id', flat=True).get(id=upload_id))
    except Exception:
        pass
    # Progress UI: flip to "running" as soon as a worker executes this task (before chord/chunks).
    mark_celery_scrape_worker_started(store_id)
    try:
        out = run_catalog_scrape(upload_id, parallel=True)
    except Exception:
        clear_celery_scrape_state(store_id)
        raise
    if isinstance(out, dict) and out.get('parallel') and out.get('status') == 'running':
        return out
    clear_celery_scrape_state(store_id)
    return out


@shared_task(bind=True, max_retries=3)
def catalog_scrape_store_task(self, store_id: str):
    """Celery: scrape all active listings for a store (no marketplace push)."""
    from catalog.celery_scrape_state import clear_celery_scrape_state

    mark_celery_scrape_worker_started(str(store_id))
    try:
        out = run_store_wide_catalog_scrape(store_id, parallel=True)
    except Exception:
        clear_celery_scrape_state(store_id)
        raise
    if isinstance(out, dict) and out.get('parallel') and out.get('status') == 'running':
        return out
    clear_celery_scrape_state(store_id)
    return out


def _finalize_vevor_scrape_job(
    job_id: str | None,
    store_id: str | None,
    *,
    status: str = 'done',
    stats: dict | None = None,
) -> None:
    """Update the tracking ``HebScrapeJob`` row and bust progress cache."""
    if not job_id:
        return
    try:
        from catalog.models import HebScrapeJob
        from catalog.scrape_progress import invalidate_scrape_progress_cache

        job = HebScrapeJob.objects.filter(id=job_id).first()
        if not job:
            return
        terminal = {
            'done': HebScrapeJob.Status.DONE,
            'failed': HebScrapeJob.Status.FAILED,
        }.get(status, HebScrapeJob.Status.DONE)
        if job.status == terminal:
            return
        job.status = terminal
        job.completed_at = timezone.now()
        if stats is not None:
            job.stats = stats
        job.save(update_fields=['status', 'completed_at', 'stats'])
        if store_id:
            invalidate_scrape_progress_cache(str(store_id))
    except Exception:
        logger.exception('Failed to finalize VevorAU job %s', job_id)


def run_vevor_au_ingest(store_id: str | None = None, *, job_id: str | None = None) -> dict:
    """Refresh VendorPrice rows for Vevor AU products from the public S3 XLSX feed.

    When ``job_id`` is set (user clicked Start Scraping), **all active** Vevor
    listings for the store are refreshed in one bulk pass from the feed.

    Without ``job_id`` (background/cron), only ``sync_status='pending'`` rows
    are processed; when none are pending the feed is not downloaded.

    ``store_id`` is **required**: mappings are updated only for that store (multi-tenant).
    """
    from decimal import Decimal

    from scrapers.vevor_au_ingest import (
        VEVOR_AU_FEED_URL,
        fetch_vevor_feed,
        load_veror_via_excel_positions,
        lookup_sku,
    )
    from sync.tasks import (
        _apply_inventory,
        _apply_pricing,
        _build_store_vendor_pricing_inventory_caches,
        _get_inventory_for_vendor_from_cache,
        _get_pricing_for_vendor_from_cache,
    )
    from vendor.models import Vendor, VendorPrice

    vevor_codes = ('vevorau', 'vevor_au', 'vevor-au', 'vevor')
    vendor_ids = list(
        Vendor.objects.filter(code__iregex=r'^vevor(au|_au|-au)?$')
        .values_list('id', flat=True)
    )
    if not vendor_ids:
        return {'status': 'no_vendor', 'message': 'Vevor vendor not seeded.', 'updated': 0}

    if not store_id:
        logger.warning('run_vevor_au_ingest: store_id missing; refusing global apply (multi-tenant).')
        return {'status': 'skipped', 'message': 'store_id is required', 'updated': 0}

    pm_qs = ProductMapping.objects.filter(
        store_id=store_id,
        is_active=True,
        product__vendor_id__in=vendor_ids,
    ).select_related('product', 'product__vendor', 'store')

    user_triggered = bool(job_id)
    if not user_triggered:
        pm_qs = pm_qs.filter(sync_status='pending')

    if not pm_qs.exists():
        reason = 'no_vevor_listings' if user_triggered else 'no_pending_vevor'
        result = {
            'status': 'skipped',
            'reason': reason,
            'updated': 0,
            'store_id': str(store_id),
            'job_id': str(job_id) if job_id else None,
        }
        if job_id:
            _finalize_vevor_scrape_job(
                job_id, store_id,
                stats={'received': 0, 'matched': 0, 'applied': 0},
            )
        logger.info('Vevor AU ingest skipped: %s', result)
        return result

    try:
        xlsx_path = fetch_vevor_feed(VEVOR_AU_FEED_URL)
    except Exception as e:
        logger.exception('Vevor AU feed download failed: %s', e)
        _finalize_vevor_scrape_job(job_id, store_id, status='failed', stats={'error': str(e)[:240]})
        return {'status': 'failed', 'error': str(e), 'updated': 0}

    try:
        lookup, lookup_compact, pos_rows = load_veror_via_excel_positions(xlsx_path)
    except Exception as e:
        logger.exception('Vevor AU feed parse failed: %s', e)
        _finalize_vevor_scrape_job(job_id, store_id, status='failed', stats={'error': str(e)[:240]})
        return {'status': 'failed', 'error': str(e), 'updated': 0}
    finally:
        try:
            import os as _os
            _os.unlink(xlsx_path)
        except Exception:
            pass

    if not lookup:
        _finalize_vevor_scrape_job(job_id, store_id, stats={'received': pos_rows, 'matched': 0, 'applied': 0})
        return {'status': 'empty_feed', 'feed_rows': pos_rows, 'updated': 0}

    pm_list = list(pm_qs)
    store = pm_list[0].store if pm_list else None
    if store is None:
        try:
            from stores.models import Store
            store = Store.objects.get(id=store_id)
        except Exception:
            store = None

    price_by_vid, price_fb, inv_by_vid, inv_fb = (
        _build_store_vendor_pricing_inventory_caches(store) if store else ({}, {}, {}, {})
    )

    now = timezone.now()
    matched = missing = updated_rows = 0
    pm_batch: list[ProductMapping] = []
    vp_batch: list[VendorPrice] = []
    bulk_pm_size = int(getattr(settings, 'VEVOR_INGEST_BULK_BATCH', 500) or 500)
    bulk_pm_size = max(50, min(bulk_pm_size, 2000))
    pm_fields = (
        'store_price', 'store_stock', 'sync_status',
        'failed_sync_count', 'last_scrape_time', 'scrape_error',
    )

    def _flush_pm_batch() -> None:
        nonlocal updated_rows
        if not pm_batch:
            return
        ProductMapping.objects.bulk_update(pm_batch, pm_fields, batch_size=bulk_pm_size)
        updated_rows += len(pm_batch)
        pm_batch.clear()

    def _flush_vp_batch() -> None:
        if not vp_batch:
            return
        VendorPrice.objects.bulk_create(vp_batch, batch_size=bulk_pm_size)
        vp_batch.clear()

    for pm in pm_list:
        product = pm.product
        if not product:
            continue
        raw_sku = (product.vendor_sku or '').strip()
        if not raw_sku:
            missing += 1
            _fail_mapping(pm, 'vevor_feed_sku_missing', 'Missing vendor SKU')
            continue
        entry = lookup_sku(lookup, lookup_compact, raw_sku)
        if not entry:
            missing += 1
            _fail_mapping(pm, 'vevor_feed_sku_missing', 'SKU not in Vevor AU XLSX feed')
            continue
        matched += 1
        try:
            price = Decimal(str(entry['Posted Price'] or 0))
            stock_val = int(entry.get('Posted Inventory') or 0)
        except Exception as parse_err:
            missing += 1
            _fail_mapping(pm, 'vevor_feed_row_invalid', str(parse_err)[:240])
            continue

        vp_batch.append(VendorPrice(product=product, price=price, stock=stock_val))
        if len(vp_batch) >= bulk_pm_size:
            _flush_vp_batch()

        try:
            pricing = _get_pricing_for_vendor_from_cache(product.vendor_id, price_by_vid, price_fb)
            inventory = _get_inventory_for_vendor_from_cache(product.vendor_id, inv_by_vid, inv_fb)
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
            pm.scrape_error = None
            pm_batch.append(pm)
            if len(pm_batch) >= bulk_pm_size:
                _flush_pm_batch()
        except Exception as apply_err:
            logger.exception(
                'Vevor AU apply failed for SKU %s (store=%s): %s',
                product.vendor_sku, pm.store_id, apply_err,
            )

    _flush_vp_batch()
    _flush_pm_batch()

    result = {
        'status': 'ok',
        'feed_rows': pos_rows,
        'feed_unique_skus': len(lookup),
        'matched': matched,
        'missing': missing,
        'updated': updated_rows,
        'store_id': str(store_id) if store_id else None,
        'job_id': str(job_id) if job_id else None,
        'user_triggered': user_triggered,
        'listing_count': len(pm_list),
    }

    if job_id:
        _finalize_vevor_scrape_job(
            job_id, store_id,
            stats={
                'received': pos_rows,
                'matched': matched,
                'applied': updated_rows,
            },
        )

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


@shared_task(ignore_result=True)
def resume_catalog_scrape_after_stop(
    store_id: str,
    scope: str | None = None,
    upload_id: str | None = None,
):
    """Re-queue server-side catalog scrape (Amazon/eBay) after Stop if Pending rows remain.

    Scheduled by ``CatalogScrapeCancelView`` with ``countdown=CATALOG_SCRAPE_RESUME_AFTER_STOP_SECONDS``.
    Does not enqueue desktop-runner jobs (HEB/Costco). Disabled when setting is 0.
    """
    try:
        sec = int(getattr(settings, 'CATALOG_SCRAPE_RESUME_AFTER_STOP_SECONDS', 0) or 0)
    except ValueError:
        sec = 0
    if sec <= 0:
        return {'skipped': True, 'reason': 'disabled'}

    from catalog.activity_log import append_catalog_log
    from catalog.celery_scrape_state import clear_celery_scrape_state, set_celery_scrape_state
    from stores.models import Store

    if StoreCatalogCeleryScrapeState.objects.filter(store_id=store_id).exists():
        return {'skipped': True, 'reason': 'scrape_already_active'}

    try:
        store = Store.objects.get(id=store_id)
    except Store.DoesNotExist:
        return {'skipped': True, 'reason': 'no_store'}

    scope_norm = (scope or StoreCatalogCeleryScrapeState.Scope.STORE).strip().lower()

    if scope_norm == StoreCatalogCeleryScrapeState.Scope.UPLOAD and upload_id:
        upload = CatalogUpload.objects.filter(id=upload_id, store_id=store_id).first()
        if not upload:
            return {'skipped': True, 'reason': 'upload_missing'}
        has_pending = upload.rows.filter(
            product_mapping__isnull=False,
            product_mapping__is_active=True,
            product_mapping__sync_status='pending',
        ).exists()
        if not has_pending:
            return {'skipped': True, 'reason': 'no_pending_rows'}

        celery_task_id = str(uuid.uuid4())
        with transaction.atomic():
            set_celery_scrape_state(
                store,
                task_id=celery_task_id,
                scope=StoreCatalogCeleryScrapeState.Scope.UPLOAD,
                upload=upload,
            )
            mark_celery_scrape_worker_started(str(store_id))
        try:
            catalog_scrape_task.apply_async(args=[str(upload_id)], task_id=celery_task_id)
        except Exception:
            clear_celery_scrape_state(str(store_id))
            raise

        append_catalog_log(
            store.id,
            'Vendor scrape re-started automatically after a stop (remaining Pending rows).',
            action_type='scrape_auto_resume',
            metadata={'upload_id': str(upload_id), 'scope': 'upload'},
        )
        return {'queued': True, 'scope': 'upload', 'upload_id': str(upload_id)}

    has_pending = ProductMapping.objects.filter(
        store_id=store_id,
        is_active=True,
        sync_status='pending',
    ).exists()
    if not has_pending:
        return {'skipped': True, 'reason': 'no_pending_rows'}

    celery_task_id = str(uuid.uuid4())
    with transaction.atomic():
        set_celery_scrape_state(
            store,
            task_id=celery_task_id,
            scope=StoreCatalogCeleryScrapeState.Scope.STORE,
            upload=None,
        )
        mark_celery_scrape_worker_started(str(store_id))
    try:
        catalog_scrape_store_task.apply_async(args=[str(store_id)], task_id=celery_task_id)
    except Exception:
        clear_celery_scrape_state(str(store_id))
        raise

    append_catalog_log(
        store.id,
        'Vendor scrape re-started automatically after a stop (remaining Pending rows).',
        action_type='scrape_auto_resume',
        metadata={'scope': 'store'},
    )
    return {'queued': True, 'scope': 'store'}
