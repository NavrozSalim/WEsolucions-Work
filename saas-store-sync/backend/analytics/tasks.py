"""
Daily aggregation of metrics for fast analytics. Schedule with Celery Beat (e.g. 01:00 UTC).
"""
from celery import shared_task
from django.utils import timezone
from django.db.models import Q, Count
from datetime import timedelta

from stores.models import Store
from catalog.models import ProductMapping
from analytics.models import DailyStoreMetrics


def _store_metric_defaults(store, target_date):
    rows = (
        ProductMapping.objects.filter(store=store)
        .aggregate(
            out_of_stock_count=Count('id', filter=Q(store_stock=0) | Q(store_stock__isnull=True)),
            pending_count=Count('id', filter=Q(sync_status='pending')),
            failed_count=Count('id', filter=Q(sync_status='failed')),
            needs_attention_count=Count('id', filter=Q(sync_status='needs_attention')),
            synced_count=Count('id', filter=Q(sync_status='synced')),
            scraped_count=Count('id', filter=Q(sync_status='scraped')),
        )
    )
    return {
        'orders_count': 0,
        'revenue': 0,
        'out_of_stock_count': rows.get('out_of_stock_count') or 0,
        'pending_count': rows.get('pending_count') or 0,
        'failed_count': rows.get('failed_count') or 0,
        'needs_attention_count': rows.get('needs_attention_count') or 0,
        'synced_count': rows.get('synced_count') or 0,
        'scraped_count': rows.get('scraped_count') or 0,
    }


@shared_task
def aggregate_daily_metrics(date=None):
    """
    Aggregate inventory + sync-status counts for the given date (default: yesterday).
    Creates or updates DailyStoreMetrics per store so analytics queries stay fast.
    """
    target_date = date or (timezone.now().date() - timedelta(days=1))
    for store in Store.objects.all().defer('api_token', 'kogan_service_account_json'):
        DailyStoreMetrics.objects.update_or_create(
            store=store,
            date=target_date,
            defaults=_store_metric_defaults(store, target_date),
        )
    return {'date': str(target_date), 'stores': Store.objects.count()}
