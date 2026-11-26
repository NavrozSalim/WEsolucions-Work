"""Recover discovery jobs whose worker died.

A running job's worker holds its lock and renews it every 30 seconds; the lock
lapses three minutes after the worker dies (a deploy, a crash, out of memory).
The job then stayed "running" forever: nothing scraped it, Stop only asked the
gone worker to stop, Continue never appeared, and the account's next file in
that region waited behind it (one file at a time per account and region).

- Stop on a job no worker holds closes it at once, so Continue is available.
- ``revive_stalled_discovery_jobs`` (Celery Beat, every few minutes) finds
  running jobs with no worker and no activity for ``DISCOVERY_STALL_MINUTES``:
  a job whose Stop was asked is closed; any other is sent back to its region
  worker, which continues from the first product not saved yet. A job that dies
  again without saving anything new is closed after
  ``DISCOVERY_WATCHDOG_MAX_EMPTY_REVIVALS`` tries, so Continue can be used
  by hand instead of a loop.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

from django.core.cache import cache
from django.db.models import Max
from django.utils import timezone

from .locks import discovery_lock_key
from .models import DiscoveryJob

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(os.environ.get(name) or default)))
    except ValueError:
        return default


def stall_timedelta() -> timedelta:
    return timedelta(minutes=_env_int('DISCOVERY_STALL_MINUTES', 15, 5, 24 * 60))


def max_empty_revivals() -> int:
    return _env_int('DISCOVERY_WATCHDOG_MAX_EMPTY_REVIVALS', 3, 1, 20)


def job_has_live_worker(job_id) -> bool:
    """True while a worker holds the job's lock. When the lock cannot be read, assume it does."""
    try:
        return cache.get(discovery_lock_key(job_id)) is not None
    except Exception:
        logger.warning('discovery watchdog: lock for %s unreadable', job_id, exc_info=True)
        return True


def close_stopped_job(job: DiscoveryJob, message: str = 'Stopped.') -> None:
    """Finish a job no worker is running, as Stop would have, and start the account's next file."""
    job.status = DiscoveryJob.Status.CANCELLED
    job.error_message = message
    job.finished_at = timezone.now()
    job.save(update_fields=['status', 'error_message', 'finished_at'])
    try:
        from .queue import promote_after_job

        promote_after_job(job.id, job.owner_id, job.marketplace)
    except Exception:
        logger.exception('discovery watchdog: next file did not start after %s', job.id)


def _parse(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed)


def _resend(job: DiscoveryJob) -> None:
    from .routing import queue_for_marketplace
    from .tasks import run_discovery_job

    queue = job.queue_name or queue_for_marketplace(job.marketplace)
    result = run_discovery_job.apply_async(args=[str(job.id)], queue=queue)
    task_id = str(getattr(result, 'id', '') or '')[:255]
    if task_id:
        job.celery_task_id = task_id
        job.save(update_fields=['celery_task_id'])


def revive_stalled_discovery_jobs(now=None) -> list[dict]:
    now = now or timezone.now()
    stall = stall_timedelta()
    results = []
    running = (
        DiscoveryJob.objects.filter(status=DiscoveryJob.Status.RUNNING)
        .defer('source_bytes', 'result_bytes')
        .annotate(last_saved=Max('products__created_at'))
    )
    for job in running:
        if job_has_live_worker(job.id):
            continue
        stats = dict(job.stats or {})
        last_alive = max(
            t for t in (
                job.started_at, job.created_at, job.last_saved, _parse(stats.get('watchdog_sent_at')),
            ) if t
        )
        if now - last_alive < stall:
            continue
        quiet_min = int((now - last_alive).total_seconds() // 60)
        if job.cancel_requested:
            close_stopped_job(job)
            logger.warning('Discovery watchdog: job %s had no worker; its Stop is now finished.', job.id)
            results.append({'job_id': str(job.id), 'action': 'closed'})
            continue

        saved = job.products.count()
        empty = int(stats.get('watchdog_empty_revivals') or 0)
        if stats.get('watchdog_sent_at') and saved <= int(stats.get('watchdog_saved_at_send') or 0):
            empty += 1
        else:
            empty = 0
        if empty >= max_empty_revivals():
            close_stopped_job(
                job,
                message=(
                    f'Stopped: the worker kept stopping on this file ({empty} restarts without new '
                    f'products). Click Continue to try again.'
                ),
            )
            logger.warning('Discovery watchdog: job %s gave up after %s empty restarts.', job.id, empty)
            results.append({'job_id': str(job.id), 'action': 'gave_up'})
            continue

        stats.update(
            watchdog_sent_at=now.isoformat(),
            watchdog_saved_at_send=saved,
            watchdog_empty_revivals=empty,
            watchdog_revivals=int(stats.get('watchdog_revivals') or 0) + 1,
        )
        job.stats = stats
        job.save(update_fields=['stats'])
        try:
            _resend(job)
        except Exception:
            logger.exception('Discovery watchdog could not resume job %s', job.id)
            results.append({'job_id': str(job.id), 'action': 'enqueue_failed'})
            continue
        logger.warning(
            'Discovery watchdog: job %s had no worker for %s min; resumed from %s saved products.',
            job.id, quiet_min, saved,
        )
        results.append({'job_id': str(job.id), 'action': 'resumed', 'saved': saved})
    return results
