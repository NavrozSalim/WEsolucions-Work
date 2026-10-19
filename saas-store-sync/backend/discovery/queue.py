"""Run one discovery file at a time per account and region.

US and AU keep separate workers. Extra files stay queued until the one
ahead reaches succeeded, failed, or cancelled.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from django.contrib.auth import get_user_model
from django.db import connection, transaction

from .models import DiscoveryJob
from .routing import marketplaces_for_queue, queue_for_marketplace
from .tasks import run_discovery_job

logger = logging.getLogger(__name__)

# Set inside the row lock before the Celery message is sent, so a second
# upload cannot start another file in that window.
PENDING_TASK_ID = 'pending'


def scope_for_user(user) -> dict:
    org_id = getattr(user, 'organization_id', None)
    if org_id:
        return {'owner__organization_id': org_id}
    return {'owner_id': user.pk}


def _scope_key(user) -> tuple:
    org_id = getattr(user, 'organization_id', None)
    if org_id:
        return ('org', org_id)
    return ('user', user.pk)


def queue_positions(jobs) -> dict:
    """Waiting order among active jobs. 0 means this file is the one in progress."""
    groups = defaultdict(list)
    for job in jobs:
        if job.status not in (DiscoveryJob.Status.QUEUED, DiscoveryJob.Status.RUNNING):
            continue
        groups[(_scope_key(job.owner), queue_for_marketplace(job.marketplace))].append(job)
    positions = {}
    for group in groups.values():
        group.sort(key=lambda item: (item.created_at, str(item.id)))
        waiting = 0
        for job in group:
            if job.status == DiscoveryJob.Status.RUNNING or job.celery_task_id:
                positions[job.id] = 0
            else:
                waiting += 1
                positions[job.id] = waiting
    return positions


def positions_for(user) -> dict:
    active = (
        DiscoveryJob.objects.filter(
            **scope_for_user(user),
            status__in=(DiscoveryJob.Status.QUEUED, DiscoveryJob.Status.RUNNING),
        )
        .select_related('owner')
        .order_by('created_at', 'id')
    )
    return queue_positions(active)


def enqueue(job: DiscoveryJob) -> None:
    """Send a claimed job to its region worker."""
    queue = queue_for_marketplace(job.marketplace)
    job.queue_name = queue
    try:
        result = run_discovery_job.apply_async(args=[str(job.id)], queue=queue)
    except Exception:
        if job.celery_task_id == PENDING_TASK_ID:
            job.celery_task_id = ''
            job.save(update_fields=['celery_task_id', 'queue_name'])
        raise
    task_id = getattr(result, 'id', '') or ''
    if not isinstance(task_id, str):
        task_id = str(task_id)
    if not task_id or task_id == PENDING_TASK_ID:
        task_id = f'dispatched-{job.id}'
    job.celery_task_id = task_id[:255]
    job.save(update_fields=['queue_name', 'celery_task_id'])


def _claim_next(user, marketplace: str) -> DiscoveryJob | None:
    queue = queue_for_marketplace(marketplace)
    markets = marketplaces_for_queue(queue)
    with transaction.atomic():
        waiting = DiscoveryJob.objects.filter(
            **scope_for_user(user),
            marketplace__in=markets,
            status__in=(DiscoveryJob.Status.QUEUED, DiscoveryJob.Status.RUNNING),
        ).order_by('created_at', 'id')
        if connection.features.has_select_for_update:
            waiting = waiting.select_for_update()
        active = list(waiting)
        if any(job.status == DiscoveryJob.Status.RUNNING or job.celery_task_id for job in active):
            return None
        nxt = next(
            (
                job for job in active
                if job.status == DiscoveryJob.Status.QUEUED and not job.celery_task_id and not job.cancel_requested
            ),
            None,
        )
        if nxt is None:
            return None
        nxt.celery_task_id = PENDING_TASK_ID
        nxt.queue_name = queue
        nxt.save(update_fields=['celery_task_id', 'queue_name'])
        return nxt


def dispatch_next(user, marketplace: str) -> DiscoveryJob | None:
    """Start the oldest waiting file when this account's region worker is free."""
    job = _claim_next(user, marketplace)
    if job is None:
        return None
    enqueue(job)
    return job


def promote_after_job(job_id, owner_id, marketplace: str) -> None:
    """Start the next file after this one has left the worker."""
    status = DiscoveryJob.objects.filter(pk=job_id).values_list('status', flat=True).first()
    if status in (DiscoveryJob.Status.QUEUED, DiscoveryJob.Status.RUNNING):
        return
    user = get_user_model().objects.filter(pk=owner_id).first()
    if user is None:
        return
    try:
        dispatch_next(user, marketplace)
    except Exception:
        logger.exception('discovery queue could not start the next file after %s', job_id)
