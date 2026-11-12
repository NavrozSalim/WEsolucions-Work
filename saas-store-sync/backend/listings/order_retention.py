"""Keep a deleted store's orders for a while so they can be linked to another store.

Orders are never deleted while their store exists. When a store is deleted its orders are
detached (store set to null, the store's name and marketplace kept on the order) and stay for
``ORDER_DETACHED_RETENTION_DAYS`` (default 7). During that time the owner can link them to
another store from the store wizard's Auto Purchasing step. After it they are purged.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Max
from django.utils import timezone

logger = logging.getLogger(__name__)


def retention_days() -> int:
    try:
        return max(1, int(getattr(settings, 'ORDER_DETACHED_RETENTION_DAYS', 7) or 7))
    except (TypeError, ValueError):
        return 7


def detach_store_orders(store) -> int:
    """Call before deleting ``store``: mark its orders so they survive the delete."""
    from .models import MarketplaceOrder

    qs = MarketplaceOrder.objects.filter(store=store)
    if store.user_id:
        qs.filter(user__isnull=True).update(user_id=store.user_id)
    n = qs.update(
        detached_at=timezone.now(),
        detached_store_name=(store.name or '')[:255],
        detached_store_marketplace=(getattr(store.marketplace, 'name', '') or '')[:100],
    )
    if n:
        logger.info('Detached %s order(s) of deleted store %s (%s)', n, store.id, store.name)
    return n


def _detached_for(user):
    from .models import MarketplaceOrder

    cutoff = timezone.now() - timedelta(days=retention_days())
    return MarketplaceOrder.objects.filter(
        user=user, store__isnull=True, detached_at__isnull=False, detached_at__gte=cutoff,
    )


def detached_groups(user) -> list[dict]:
    """Deleted stores whose orders can still be linked: one entry per deleted store name."""
    rows = (
        _detached_for(user)
        .values('detached_store_name', 'detached_store_marketplace')
        .annotate(count=Count('id'), deleted_at=Max('detached_at'))
        .order_by('-deleted_at')
    )
    days = retention_days()
    return [
        {
            'store_name': r['detached_store_name'],
            'marketplace': r['detached_store_marketplace'],
            'count': r['count'],
            'deleted_at': r['deleted_at'].isoformat() if r['deleted_at'] else None,
            'removed_at': (r['deleted_at'] + timedelta(days=days)).isoformat() if r['deleted_at'] else None,
        }
        for r in rows
    ]


def link_detached_orders(user, store, store_name: str, marketplace: str = '') -> dict:
    """Move a deleted store's orders onto ``store``. Orders the store already has (same order ID) stay put."""
    from .models import MarketplaceOrder, OrderPurchaseCheck

    qs = _detached_for(user).filter(detached_store_name=store_name)
    if marketplace:
        qs = qs.filter(detached_store_marketplace=marketplace)
    linked = skipped = 0
    for order in qs:
        clash = MarketplaceOrder.objects.filter(
            store=store, external_order_key=order.external_order_key, environment=order.environment,
        ).exists()
        if clash:
            skipped += 1
            continue
        try:
            with transaction.atomic():
                MarketplaceOrder.objects.filter(id=order.id).update(
                    store=store, detached_at=None, detached_store_name='', detached_store_marketplace='',
                )
                OrderPurchaseCheck.objects.filter(order_id=order.id).update(store=store)
            linked += 1
        except IntegrityError:
            skipped += 1
    msg = f'{linked} order(s) from "{store_name}" linked to {store.name}.'
    if skipped:
        msg += f' {skipped} skipped (this store already has an order with the same ID).'
    return {'linked': linked, 'skipped': skipped, 'message': msg}


def purge_expired_detached_orders() -> int:
    """Delete orders whose store was deleted more than the retention window ago."""
    from .models import MarketplaceOrder

    cutoff = timezone.now() - timedelta(days=retention_days())
    n, _ = MarketplaceOrder.objects.filter(store__isnull=True, detached_at__lt=cutoff).delete()
    if n:
        logger.info('Purged %s expired detached order row(s)', n)
    return n
