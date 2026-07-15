import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

app = Celery('core')

app.config_from_object('django.conf:settings', namespace='CELERY')

# Durability: keep long-running scrape/ingest jobs in the broker until the
# worker ACKs completion. If a worker is killed mid-task the job is
# re-delivered instead of silently dropped, so user-initiated scrapes never
# vanish just because the server was busy.
app.conf.task_acks_late = True
app.conf.task_reject_on_worker_lost = True
app.conf.worker_prefetch_multiplier = 1

app.autodiscover_tasks()

app.conf.beat_schedule = {
    'check-store-schedules': {
        'task': 'sync.tasks.check_scheduled_updates',
        'schedule': crontab(minute='*'),
    },
    # Populate daily dashboard trend data from ProductMapping snapshots.
    'aggregate-daily-analytics-metrics': {
        'task': 'analytics.tasks.aggregate_daily_metrics',
        'schedule': crontab(minute=10, hour=0),
    },
    # Prune old VendorPrice history (keeps latest row per product).
    'prune-old-vendor-prices': {
        'task': 'vendor.prune_old_vendor_prices',
        'schedule': crontab(minute=30, hour=3),
    },
    # Pull Lasoo customer tickets/messages into Tickets Management.
    'fetch-marketplace-tickets-hourly': {
        'task': 'listings.fetch_all_store_tickets',
        'schedule': crontab(minute=15),
    },
    # Pull Reverb selling orders (incremental by updated_at) into Orders.
    'fetch-reverb-orders-hourly': {
        'task': 'listings.fetch_all_reverb_orders',
        'schedule': crontab(minute=20),
    },
}


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
