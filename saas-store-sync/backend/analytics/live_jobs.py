"""Lightweight active-job snapshot for the Dashboard Live jobs panel."""
from __future__ import annotations

from typing import Any

from django.db.models import Count, Q
from django.utils import timezone

from catalog.models import (
    CatalogActivityLog,
    HebScrapeJob,
    ProductMapping,
    StoreCatalogCeleryScrapeState,
)
from stores.models import Store
from sync.push_listings_lock import get_push_listings_lock_owner


def _pct(done: int, total: int) -> int | None:
    if total <= 0:
        return None
    return min(100, max(0, round(100 * done / total)))


def _desktop_job_progress(job: HebScrapeJob) -> int | None:
    stats = job.stats or {}
    for key in ('applied', 'matched', 'received', 'done', 'scraped'):
        if stats.get(key) is not None and job.url_count:
            try:
                return _pct(int(stats[key]), int(job.url_count))
            except (TypeError, ValueError):
                pass
    if not job.store_id:
        return None
    vendor = (job.vendor_code or 'heb').lower()
    rows = ProductMapping.objects.filter(
        store_id=job.store_id,
        is_active=True,
        product__vendor__code__iexact=vendor,
    ).aggregate(
        total=Count('id'),
        pending=Count('id', filter=Q(sync_status='pending')),
    )
    total = rows.get('total') or 0
    pending = rows.get('pending') or 0
    if total <= 0:
        return None
    return _pct(total - pending, total)


def _push_progress(store_id) -> tuple[int | None, str]:
    """Return (pct, status_message) from recent sync activity logs when a push lock is held."""
    from datetime import timedelta

    since = timezone.now() - timedelta(hours=6)
    logs = list(
        CatalogActivityLog.objects.filter(
            store_id=store_id,
            created_at__gte=since,
            action_type__in=['sync_start', 'sync_progress', 'sync_end'],
        ).order_by('-created_at')[:40]
    )
    latest_start = next((lg for lg in logs if lg.action_type == 'sync_start'), None)
    latest_end = next((lg for lg in logs if lg.action_type == 'sync_end'), None)
    if (
        latest_end
        and latest_start
        and latest_end.created_at > latest_start.created_at
    ):
        return None, ''
    if not latest_start:
        return None, 'Marketplace push in progress'
    for lg in logs:
        if lg.created_at < latest_start.created_at:
            break
        if lg.action_type == 'sync_progress' and lg.metadata:
            md = lg.metadata or {}
            try:
                processed = int(md.get('processed') or 0)
                total = int(md.get('total') or 0)
            except (TypeError, ValueError):
                processed, total = 0, 0
            msg = (lg.message or '').strip() or 'Pushing listings to marketplace'
            return _pct(processed, total), msg
    return None, (latest_start.message or '').strip() or 'Preparing marketplace push'


def collect_live_jobs(user) -> list[dict[str, Any]]:
    stores = list(
        Store.objects.filter(user=user)
        .only('id', 'name')
        .order_by('name')
    )
    if not stores:
        return []
    store_ids = [s.id for s in stores]
    store_name = {s.id: s.name for s in stores}
    jobs: list[dict[str, Any]] = []

    # Server-side Celery catalog scrapes
    for st in StoreCatalogCeleryScrapeState.objects.filter(store_id__in=store_ids).select_related('store'):
        phase = 'running' if st.first_worker_started_at else 'queued'
        if st.cancel_requested:
            phase = 'stopping'
        name = store_name.get(st.store_id) or st.store.name
        jobs.append({
            'id': f'celery-scrape-{st.store_id}',
            'kind': 'celery_scrape',
            'title': f'Server scrape · {name}',
            'description': f'{phase.capitalize()} · {st.scope} scope',
            'store_id': str(st.store_id),
            'store_name': name,
            'progress': None,
            'phase': phase,
            'href': f'/catalog?store={st.store_id}',
        })

    # Desktop runner jobs (HEB / Costco / etc.)
    desktop_qs = HebScrapeJob.objects.filter(
        status__in=[HebScrapeJob.Status.PENDING, HebScrapeJob.Status.CLAIMED],
    ).filter(
        Q(store_id__in=store_ids) | Q(store__isnull=True, requested_by=user)
    ).order_by('-requested_at')[:40]

    for job in desktop_qs:
        vendor = (job.vendor_code or 'heb').upper()
        name = store_name.get(job.store_id) if job.store_id else 'All stores'
        if not name:
            name = 'Store'
        status = job.status
        bits = [status]
        if job.url_count:
            bits.append(f'{job.url_count:,} URLs')
        if job.claimed_by_ip:
            bits.append(f'PC {job.claimed_by_ip}')
        jobs.append({
            'id': f'desktop-{job.vendor_code}-{job.id}',
            'kind': 'desktop_scrape',
            'title': f'{vendor} desktop scrape · {name}',
            'description': ' · '.join(bits),
            'store_id': str(job.store_id) if job.store_id else None,
            'store_name': name,
            'progress': _desktop_job_progress(job),
            'phase': status,
            'href': f'/catalog?store={job.store_id}' if job.store_id else '/catalog',
        })

    # Marketplace push (Manual sync) locks
    for sid in store_ids:
        owner = get_push_listings_lock_owner(sid)
        if not owner:
            continue
        name = store_name.get(sid) or 'Store'
        pct, msg = _push_progress(sid)
        phase = 'queued' if str(owner).startswith('reserved:') else 'running'
        jobs.append({
            'id': f'push-{sid}',
            'kind': 'push',
            'title': f'Marketplace push · {name}',
            'description': msg or phase.capitalize(),
            'store_id': str(sid),
            'store_name': name,
            'progress': pct,
            'phase': phase,
            'href': f'/catalog?store={sid}',
        })

    # Stable order: desktop / celery / push, then name
    kind_order = {'desktop_scrape': 0, 'celery_scrape': 1, 'push': 2}
    jobs.sort(key=lambda j: (kind_order.get(j['kind'], 9), j.get('store_name') or '', j['id']))
    return jobs
