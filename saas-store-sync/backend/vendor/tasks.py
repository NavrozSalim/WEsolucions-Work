"""
Vendor-related Celery tasks (maintenance).
"""
from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import connection
from django.utils import timezone

logger = logging.getLogger(__name__)


def _vendor_price_retention_days() -> int:
    try:
        return max(7, min(3650, int(getattr(settings, 'VENDOR_PRICE_RETENTION_DAYS', 90) or 90)))
    except (TypeError, ValueError):
        return 90


@shared_task(name='vendor.prune_old_vendor_prices', ignore_result=True)
def prune_old_vendor_prices_task():
    """
    Delete historical ``VendorPrice`` rows older than ``VENDOR_PRICE_RETENTION_DAYS``.

    Always keeps the latest row per product (PostgreSQL ``DISTINCT ON``). Metrics
    that use ``ingested_last_24h`` need retention >= 1 day.
    """
    if connection.vendor != 'postgresql':
        logger.warning('prune_old_vendor_prices: skipped (requires PostgreSQL)')
        return {'skipped': True, 'reason': 'not_postgresql'}

    days = _vendor_price_retention_days()
    cutoff = timezone.now() - timedelta(days=days)

    with connection.cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM vendor_vendorprice AS vp
            WHERE vp.scraped_at < %s
              AND vp.id NOT IN (
                SELECT DISTINCT ON (product_id) id
                FROM vendor_vendorprice
                ORDER BY product_id, scraped_at DESC
              )
            """,
            [cutoff],
        )
        deleted = cursor.rowcount

    logger.info(
        'prune_old_vendor_prices: deleted=%s cutoff_days=%s cutoff=%s',
        deleted,
        days,
        cutoff.isoformat(),
    )
    return {'deleted': deleted, 'retention_days': days, 'cutoff': cutoff.isoformat()}
