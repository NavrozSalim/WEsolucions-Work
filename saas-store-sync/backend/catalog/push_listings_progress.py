"""Build marketplace push (Manual sync) progress for the Catalog UI."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.core.cache import cache
from django.utils import timezone


def _celery_phase(job_id: str) -> str:
    """Return ``queued`` or ``running`` from Celery task state."""
    try:
        from celery.result import AsyncResult

        status = (AsyncResult(job_id).status or '').upper()
    except Exception:
        return 'running'
    if status == 'PENDING':
        return 'queued'
    return 'running'


def _lock_phase(lock_owner: str) -> str:
    owner = str(lock_owner or '')
    if owner.startswith('reserved:'):
        return 'queued'
    if owner.startswith('inline:'):
        return 'running'
    if len(owner) >= 8 and '-' in owner:
        return _celery_phase(owner)
    return 'running'


def build_push_listings_progress_payload(store) -> dict[str, Any]:
    from catalog.models import CatalogActivityLog, ProductMapping
    from sync.push_listings_lock import push_listings_lock_key

    store_id = str(store.id)
    lock_owner = cache.get(push_listings_lock_key(store_id))

    eligible_total = ProductMapping.objects.filter(
        store=store,
        is_active=True,
        sync_status__in=['synced', 'scraped'],
        store_price__isnull=False,
    ).count()

    since = timezone.now() - timedelta(hours=6)
    logs = list(
        CatalogActivityLog.objects.filter(
            store=store,
            created_at__gte=since,
            action_type__in=['sync_start', 'sync_progress', 'sync_end'],
        ).order_by('-created_at')[:100]
    )

    latest_start = next((lg for lg in logs if lg.action_type == 'sync_start'), None)
    latest_end = next((lg for lg in logs if lg.action_type == 'sync_end'), None)

    run_start = latest_start
    if (
        latest_end
        and latest_start
        and latest_end.created_at > latest_start.created_at
    ):
        run_start = None

    processed = pushed = failed = skipped = 0
    total = eligible_total
    started_at = None

    if run_start:
        started_at = run_start.created_at.isoformat()
        for lg in logs:
            if lg.created_at < run_start.created_at:
                break
            if lg.action_type == 'sync_progress' and lg.metadata:
                md = lg.metadata
                processed = int(md.get('processed') or 0)
                total = int(md.get('total') or total)
                pushed = int(md.get('pushed') or 0)
                failed = int(md.get('failed') or 0)
                skipped = int(md.get('skipped_no_listing') or 0)
                break

    active = bool(lock_owner)
    phase = _lock_phase(lock_owner) if lock_owner else None
    job_id = None
    if lock_owner:
        owner = str(lock_owner)
        if not owner.startswith('reserved:') and not owner.startswith('inline:'):
            job_id = owner

    pct = round(100 * processed / total) if total > 0 else (0 if not active else 0)

    return {
        'store_id': store_id,
        'active': active,
        'phase': phase,
        'job_id': job_id,
        'total': total,
        'processed': processed,
        'pushed': pushed,
        'failed': failed,
        'skipped_no_listing': skipped,
        'pct': min(100, max(0, pct)),
        'started_at': started_at,
        'checked_at': timezone.now().isoformat(),
    }
