"""Build marketplace push (Manual sync) progress for the Catalog UI."""
from __future__ import annotations

import re
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


def _infer_sync_step(message: str, metadata: dict) -> str:
    step = (metadata.get('sync_step') or '').strip()
    if step:
        return step
    lower = (message or '').lower()
    if 'waiting for sears' in lower:
        return 'waiting_sears'
    if 'preparing bulk push' in lower:
        return 'queue_build'
    if 'batch ' in lower and ' of ' in lower:
        return 'bulk_push'
    return 'bulk_push' if metadata.get('pushed') or metadata.get('failed') else 'queue_build'


def build_push_listings_progress_payload(store) -> dict[str, Any]:
    from catalog.marketplace_push import manual_sync_mappings
    from catalog.models import CatalogActivityLog
    from sync.push_listings_lock import push_listings_lock_key

    store_id = str(store.id)
    lock_owner = cache.get(push_listings_lock_key(store_id))

    since = timezone.now() - timedelta(hours=6)
    run_logs = CatalogActivityLog.objects.filter(store=store, created_at__gte=since)
    latest_start = (
        run_logs.filter(action_type='sync_start').order_by('-created_at').first()
    )
    latest_end = (
        run_logs.filter(action_type='sync_end').order_by('-created_at').first()
    )

    run_start = latest_start
    if (
        latest_end
        and latest_start
        and latest_end.created_at > latest_start.created_at
    ):
        run_start = None

    vendor_id = None
    if run_start is not None:
        vendor_id = (run_start.metadata or {}).get('vendor_id') or None

    processed = pushed = failed = skipped = 0
    total = 0
    started_at = None
    status_message = ''
    sync_step = ''
    batch_num = 0
    total_batches = 0
    sears_document_id = ''

    latest_progress = None
    if run_start:
        started_at = run_start.created_at.isoformat()
        latest_progress = (
            run_logs.filter(
                action_type='sync_progress',
                created_at__gte=run_start.created_at,
            )
            .order_by('-created_at')
            .first()
        )

    if latest_progress and (latest_progress.metadata or {}).get('total'):
        total = int(latest_progress.metadata.get('total') or 0)
    else:
        try:
            total = manual_sync_mappings(store, vendor_id).count()
        except Exception:
            total = 0

    if latest_progress:
        md = latest_progress.metadata or {}
        processed = int(md.get('processed') or 0)
        if not total:
            total = int(md.get('total') or 0)
        pushed = int(md.get('pushed') or 0)
        failed = int(md.get('failed') or 0)
        skipped = int(md.get('skipped_no_listing') or 0)
        status_message = (latest_progress.message or '').strip()
        sync_step = _infer_sync_step(status_message, md)
        batch_num = int(md.get('batch_num') or 0)
        total_batches = int(md.get('total_batches') or 0)
        sears_document_id = str(md.get('sears_document_id') or '').strip()
        if not sears_document_id and status_message:
            match = re.search(r'document[:\s]+(\d+)', status_message, re.IGNORECASE)
            if match:
                sears_document_id = match.group(1)
    elif run_start:
        sync_step = 'queue_build'
        status_message = f'Preparing listings for marketplace push — 0 of {total:,} queued.'

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
        'sync_step': sync_step,
        'status_message': status_message,
        'batch_num': batch_num,
        'total_batches': total_batches,
        'sears_document_id': sears_document_id,
    }
