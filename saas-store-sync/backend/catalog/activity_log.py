"""Append catalog timeline entries; prune rows older than retention window."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from django.utils import timezone

from catalog.models import CatalogActivityLog

logger = logging.getLogger(__name__)

LOG_RETENTION = timedelta(days=1)


def append_catalog_log(
    store_id,
    message: str,
    *,
    action_type: str = 'info',
    user_id=None,
    metadata: dict[str, Any] | None = None,
) -> CatalogActivityLog | None:
    """Create a log row and delete entries for this store older than 24 hours."""
    if not store_id:
        return None
    try:
        row = CatalogActivityLog.objects.create(
            store_id=store_id,
            user_id=user_id,
            action_type=(action_type or 'info')[:64],
            message=(message or '')[:4000],
            metadata=metadata or {},
        )
    except Exception as exc:
        logger.warning('append_catalog_log failed: %s', exc)
        return None
    cutoff = timezone.now() - LOG_RETENTION
    try:
        CatalogActivityLog.objects.filter(store_id=store_id, created_at__lt=cutoff).delete()
    except Exception as exc:
        logger.debug('catalog log prune skipped: %s', exc)
    return row
