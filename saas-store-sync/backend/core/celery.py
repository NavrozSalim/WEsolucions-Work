import logging
import os

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_ready

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

_logger = logging.getLogger(__name__)


@worker_ready.connect
def _preload_heb_scraper_on_worker_start(**kwargs):
    """Import HEB scraper at worker boot when proxies are configured.

    The scraper is lazy-loaded during tasks otherwise, so the ``HEB scraper
    config`` banner would not appear in ``docker compose logs`` until the first
    HEB job runs. Preloading here makes deploy verification greppable.
    """
    if not (os.environ.get("HEB_US_PROXY_URLS") or os.environ.get("HEB_US_PROXY_URL")):
        return
    try:
        import scrapers.heb_us_scraper  # noqa: F401
    except Exception as exc:
        _logger.warning("HEB scraper preload failed: %s", exc)


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
}


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
