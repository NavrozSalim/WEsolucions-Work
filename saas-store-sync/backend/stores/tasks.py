"""Store maintenance Celery tasks."""
from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name='stores.purge_orphaned_stores', ignore_result=True)
def purge_orphaned_stores_task():
    """Hard-delete stores orphaned longer than STORE_ORPHAN_RETENTION_DAYS."""
    from stores.orphan import purge_expired_orphaned_stores

    result = purge_expired_orphaned_stores()
    logger.info('purge_orphaned_stores: %s', result)
    return result
