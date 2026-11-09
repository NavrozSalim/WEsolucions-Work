import logging
import os

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_ready

logger = logging.getLogger(__name__)

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
    # Restart store-wide vendor scrapes whose worker died or hung mid-run.
    'catalog-scrape-watchdog': {
        'task': 'catalog.tasks.catalog_scrape_watchdog',
        'schedule': crontab(minute='*/5'),
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
    # Purge marketplace stores orphaned after user delete (past retention window).
    'purge-orphaned-stores': {
        'task': 'stores.purge_orphaned_stores',
        'schedule': crontab(minute=45, hour=3),
    },
    # Orders / tickets: region-split queues (US VPS → orders-us, AU VPS → orders-au).
    'fetch-marketplace-tickets-us-hourly': {
        'task': 'listings.fetch_us_store_tickets',
        'schedule': crontab(minute=15),
    },
    'fetch-marketplace-tickets-au-hourly': {
        'task': 'listings.fetch_au_store_tickets',
        'schedule': crontab(minute=15),
    },
    'fetch-managed-orders-us-hourly': {
        'task': 'listings.fetch_us_store_orders',
        'schedule': crontab(minute=20),
    },
    'fetch-managed-orders-au-hourly': {
        'task': 'listings.fetch_au_store_orders',
        'schedule': crontab(minute=20),
    },
}


@worker_ready.connect
def _publish_worker_scrape_capabilities(sender=None, **_kwargs):
    """Tell the main server whether this worker can scrape Costco AU itself.

    Costco proxies are configured on the AU worker only; the main server
    routes Start Scraping from this shared flag.
    """
    try:
        consumer_app = getattr(sender, 'app', None) or app
        queue_names = list(consumer_app.amqp.queues.consume_from.keys())
        from catalog.costco_mode import publish_costco_au_worker_mode

        publish_costco_au_worker_mode(queue_names)
    except Exception:
        logger.exception('Could not publish Costco AU scrape mode at worker start')


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
