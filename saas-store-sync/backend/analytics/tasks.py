"""
Daily aggregation of metrics for fast analytics. Schedule with Celery Beat (e.g. 01:00 UTC).
"""
from celery import shared_task
from django.utils import timezone
from django.db.models import Q
from datetime import timedelta

from stores.models import Store
from catalog.models import ProductMapping
from analytics.models import DailyStoreMetrics


@shared_task
def aggregate_daily_metrics(date=None):
    """
    Aggregate out-of-stock counts for the given date (default: yesterday).
    Creates or updates DailyStoreMetrics per store so analytics queries stay fast.
    """
    target_date = date or (timezone.now().date() - timedelta(days=1))
    for store in Store.objects.all():
        out_of_stock = ProductMapping.objects.filter(
            store=store
        ).filter(Q(store_stock=0) | Q(store_stock__isnull=True)).count()
        DailyStoreMetrics.objects.update_or_create(
            store=store,
            date=target_date,
            defaults={
                'orders_count': 0,
                'revenue': 0,
                'out_of_stock_count': out_of_stock,
            },
        )
    return {'date': str(target_date), 'stores': Store.objects.count()}
