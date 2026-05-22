"""
Build and cache catalog scrape progress payloads for ``CatalogScrapeProgressView``.

Polling this endpoint is hot under active scrapes; a short Redis/LocMem cache
plus fewer aggregate queries keeps Postgres load predictable.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, Max, Q
from django.utils import timezone

logger = logging.getLogger(__name__)

CACHE_KEY_VERSION = 'v1'


def scrape_progress_cache_key(store_id: str) -> str:
    return f'catalog:scrape_progress:{CACHE_KEY_VERSION}:{store_id}'


def scrape_progress_cache_ttl() -> int:
    try:
        return max(5, min(120, int(getattr(settings, 'SCRAPE_PROGRESS_CACHE_SECONDS', 12) or 12)))
    except (TypeError, ValueError):
        return 12


def invalidate_scrape_progress_cache(store_id: str | None) -> None:
    if not store_id:
        return
    try:
        cache.delete(scrape_progress_cache_key(str(store_id)))
    except Exception:
        logger.exception('invalidate_scrape_progress_cache failed store_id=%s', store_id)


def heal_stale_server_vendor_job(store, vendor_code: str, job) -> None:
    """Mark a stuck ``CLAIMED`` server-vendor job done when no listings are pending.

    Server-side feed ingests (VevorAU) set ``CLAIMED`` at dispatch and ``DONE`` when
    the Celery task finishes. If the worker crashed or an older build omitted the
    finalize step, the UI would show "scraping" forever even though every listing
    has left ``sync_status='pending'``.
    """
    from catalog.ingest_views import SUPPORTED_VENDORS

    if job is None or job.status != 'claimed':
        return
    runner = (SUPPORTED_VENDORS.get(vendor_code) or {}).get('runner')
    if runner != 'server':
        return
    from catalog.models import HebScrapeJob, ProductMapping
    from catalog.views import _vendor_db_ids_for

    vendor_ids = _vendor_db_ids_for(vendor_code)
    if not vendor_ids:
        return
    has_sync_pending = ProductMapping.objects.filter(
        store=store,
        is_active=True,
        sync_status='pending',
        product__vendor_id__in=vendor_ids,
    ).exists()
    if has_sync_pending:
        return

    job.status = HebScrapeJob.Status.DONE
    if not job.completed_at:
        job.completed_at = timezone.now()
    job.save(update_fields=['status', 'completed_at'])
    invalidate_scrape_progress_cache(str(store.id))
    logger.info(
        'Healed stale %s scrape job %s for store %s (no sync pending left)',
        vendor_code, job.id, store.id,
    )


def get_scrape_progress_payload(store) -> dict[str, Any]:
    """Return progress dict (from cache when fresh)."""
    key = scrape_progress_cache_key(str(store.id))
    cached = cache.get(key)
    if cached is not None:
        return cached
    payload = build_scrape_progress_payload(store)
    try:
        cache.set(key, payload, scrape_progress_cache_ttl())
    except Exception:
        logger.exception('scrape progress cache.set failed store_id=%s', store.id)
    return payload


def build_scrape_progress_payload(store) -> dict[str, Any]:
    from catalog.ingest_views import SUPPORTED_VENDORS
    from catalog.models import HebScrapeJob, ProductMapping, StoreCatalogCeleryScrapeState
    from catalog.views import _compute_vendor_queue_payload, _vendor_db_ids_for
    from vendor.models import Vendor, VendorPrice

    active = ProductMapping.objects.filter(store=store, is_active=True)
    total = active.count()
    by_status_rows = active.values('sync_status').annotate(n=Count('id'))
    by_status = {r['sync_status']: r['n'] for r in by_status_rows}

    now = timezone.now()
    recent_5m = now - timedelta(minutes=5)
    recent_24h = now - timedelta(hours=24)

    # One pass: sync_status counts per vendor id (avoids per-vendor COUNT loops).
    status_by_vendor_id: dict[Any, dict[str, int]] = {}
    for row in (
        active.values('product__vendor_id', 'sync_status')
        .annotate(n=Count('id'))
    ):
        vid = row['product__vendor_id']
        if vid is None:
            continue
        status_by_vendor_id.setdefault(vid, {})[row['sync_status']] = row['n']

    product_ids = list(active.values_list('product_id', flat=True).distinct())
    vp_stats_by_vendor_id: dict[Any, dict[str, Any]] = {}
    if product_ids:
        for row in (
            VendorPrice.objects.filter(product_id__in=product_ids)
            .values('product__vendor_id')
            .annotate(
                last_ingest=Max('scraped_at'),
                ingested_last_5m=Count('id', filter=Q(scraped_at__gte=recent_5m)),
                ingested_last_24h=Count('id', filter=Q(scraped_at__gte=recent_24h)),
            )
        ):
            vid = row['product__vendor_id']
            if vid is None:
                continue
            last = row['last_ingest']
            vp_stats_by_vendor_id[vid] = {
                'last_ingest_at': last.isoformat() if last else None,
                'ingested_last_5m': row['ingested_last_5m'] or 0,
                'ingested_last_24h': row['ingested_last_24h'] or 0,
            }

    store_vendor_codes = list(
        active.values_list('product__vendor__code', flat=True).distinct()
    )
    store_vendor_codes = [c for c in store_vendor_codes if c]

    runner_codes = set(SUPPORTED_VENDORS.keys())
    other_codes: list[str] = []
    for raw_code in store_vendor_codes:
        rc = (raw_code or '').strip().lower()
        if not rc:
            continue
        mapped = rc
        if rc in ('heb', 'hebus') or rc.startswith('heb_'):
            mapped = 'heb'
        elif rc in ('costcoau', 'costco') or rc.startswith('costco_'):
            mapped = 'costco'
        elif rc in ('vevorau', 'vevor') or rc.startswith('vevor_'):
            mapped = 'vevor'
        if mapped in runner_codes:
            continue
        if rc not in other_codes:
            other_codes.append(rc)

    iter_codes = list(SUPPORTED_VENDORS.keys()) + other_codes
    vendors_payload: dict[str, dict] = {}

    for vendor_code in iter_codes:
        is_runner = vendor_code in runner_codes
        vendor_ids = (
            _vendor_db_ids_for(vendor_code)
            if is_runner
            else list(Vendor.objects.filter(code__iexact=vendor_code).values_list('id', flat=True))
        )
        if not vendor_ids:
            continue

        v_by_status: dict[str, int] = {}
        v_total = 0
        for vid in vendor_ids:
            for st, n in status_by_vendor_id.get(vid, {}).items():
                v_by_status[st] = v_by_status.get(st, 0) + n
                v_total += n

        v_last_ingest_at = None
        v_ingested_last_5m = 0
        v_ingested_last_24h = 0
        if v_total:
            for vid in vendor_ids:
                stats = vp_stats_by_vendor_id.get(vid)
                if not stats:
                    continue
                v_ingested_last_5m += stats['ingested_last_5m']
                v_ingested_last_24h += stats['ingested_last_24h']
                li = stats['last_ingest_at']
                if li and (v_last_ingest_at is None or li > v_last_ingest_at):
                    v_last_ingest_at = li

        v_scraped = v_by_status.get('scraped', 0) + v_by_status.get('synced', 0)
        v_pending = (
            v_by_status.get('pending', 0)
            + v_by_status.get('needs_attention', 0)
            + v_by_status.get('failed', 0)
        )
        v_pct = int(round(v_scraped * 100 / v_total)) if v_total else 0

        latest_job = None
        v_job_payload = None
        v_queue_payload = None
        if is_runner:
            latest_job = (
                HebScrapeJob.objects.filter(store=store, vendor_code=vendor_code)
                .order_by('-requested_at')
                .first()
            )
            heal_stale_server_vendor_job(store, vendor_code, latest_job)
            if latest_job is not None:
                v_job_payload = {
                    'id': str(latest_job.id),
                    'vendor': latest_job.vendor_code,
                    'status': latest_job.status,
                    'requested_at': latest_job.requested_at.isoformat(),
                    'claimed_at': latest_job.claimed_at.isoformat() if latest_job.claimed_at else None,
                    'completed_at': latest_job.completed_at.isoformat() if latest_job.completed_at else None,
                    'url_count': latest_job.url_count,
                    'stats': latest_job.stats or {},
                }
            v_queue_payload = _compute_vendor_queue_payload(store, vendor_code, latest_job)

        default_label = vendor_code.upper()
        label = (
            SUPPORTED_VENDORS.get(vendor_code, {}).get('label', default_label)
            if is_runner
            else default_label
        )
        runner_kind = (
            SUPPORTED_VENDORS.get(vendor_code, {}).get('runner', 'desktop')
            if is_runner
            else 'live'
        )
        # Costco AU dynamically becomes a 'live' server vendor when residential
        # proxies are configured for the AU worker — same UX as Amazon/eBay.
        if vendor_code == 'costco' and runner_kind == 'desktop':
            try:
                from scrapers.costco_au_proxies import load_proxy_urls
                if load_proxy_urls():
                    runner_kind = 'live'
            except Exception:
                pass
        if vendor_code == 'heb' and runner_kind == 'desktop':
            try:
                from scrapers.heb_us_proxies import load_proxy_urls
                if load_proxy_urls():
                    runner_kind = 'live'
            except Exception:
                pass
        if runner_kind == 'live':
            v_job_payload = None
            v_queue_payload = None

        vendors_payload[vendor_code] = {
            'vendor': vendor_code,
            'label': label,
            'has_products': v_total > 0,
            'total': v_total,
            'scraped': v_scraped,
            'pending': v_pending,
            'sync_pending': v_by_status.get('pending', 0),
            'by_status': v_by_status,
            'pct': v_pct,
            'last_ingest_at': v_last_ingest_at,
            'ingested_last_5m': v_ingested_last_5m,
            'ingested_last_24h': v_ingested_last_24h,
            'job': v_job_payload,
            'queue': v_queue_payload,
            'runner': runner_kind,
        }

    heb = vendors_payload.get('heb') or {}
    heb_total = heb.get('total', 0)
    heb_scraped = heb.get('scraped', 0)
    heb_pending = heb.get('pending', 0)

    server_celery_scrape = {
        'active': False,
        'store_id': str(store.id),
        'phase': None,
    }
    try:
        st = StoreCatalogCeleryScrapeState.objects.filter(store=store).first()
        if st:
            server_celery_scrape = {
                'active': True,
                'store_id': str(store.id),
                'phase': 'running',
                'scope': st.scope,
                'upload_id': str(st.upload_id) if st.upload_id else None,
                'enqueued_at': st.enqueued_at.isoformat(),
                'task_id': st.root_task_id or '',
            }
    except Exception:
        pass

    return {
        'store_id': str(store.id),
        'total': total,
        'by_status': by_status,
        'server_celery_scrape': server_celery_scrape,
        'has_heb': bool(heb_total > 0),
        'heb_total': heb_total,
        'heb_scraped': heb_scraped,
        'heb_pending': heb_pending,
        'heb_by_status': heb.get('by_status', {}),
        'heb_pct': heb.get('pct', 0),
        'heb_last_ingest_at': heb.get('last_ingest_at'),
        'heb_ingested_last_5m': heb.get('ingested_last_5m', 0),
        'heb_ingested_last_24h': heb.get('ingested_last_24h', 0),
        'heb_job': heb.get('job'),
        'heb_queue': heb.get('queue'),
        'vendors': vendors_payload,
        'checked_at': now.isoformat(),
        'ui_hints': {
            'scheduled_updates_other_stores': (
                'Each store can have its own automatic update schedule. When several are '
                'due at the same time, they may all show activity in logs — that is separate '
                'from a catalog scrape you start on this page.'
            ),
        },
    }
