import uuid
from django.db import models
from stores.models import Store
class SyncSchedule(models.Model):
    """Per-store sync schedule for Celery Beat. Supports crontab (e.g. daily 10 AM) or interval (e.g. every 2 hours)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.OneToOneField(Store, on_delete=models.CASCADE, related_name='sync_schedule')
    schedule_type = models.CharField(
        max_length=20,
        choices=[('crontab', 'Crontab'), ('interval', 'Interval')],
        default='crontab',
    )
    crontab_minute = models.CharField(max_length=32, default='0')
    crontab_hour = models.CharField(max_length=32, default='10')
    crontab_day_of_week = models.CharField(max_length=32, default='*')
    crontab_day_of_month = models.CharField(max_length=32, default='*')
    crontab_month_of_year = models.CharField(max_length=32, default='*')
    interval_seconds = models.IntegerField(null=True, blank=True, help_text="e.g. 7200 for every 2 hours")
    timezone = models.CharField(max_length=64, default='UTC')
    is_active = models.BooleanField(default=True)
    last_run = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # Scheduled Updates run like Auto Scraping: local run times, retry Failed, then one push.
    # ``run_times`` is the source of truth; the crontab fields are kept for older readers.
    run_times = models.JSONField(default=list, blank=True, help_text='Local "HH:MM" times, max 12.')
    retry_failed_cycles = models.PositiveSmallIntegerField(default=0)
    last_triggered_at = models.DateTimeField(null=True, blank=True)

    MAX_RUN_TIMES = 12
    MAX_RETRY_CYCLES = 10

    def __str__(self):
        return f"Schedule for {self.store.name} ({self.schedule_type})"


def _default_auto_scrape_times():
    return ['06:00']


class AutoScrapeSchedule(models.Model):
    """Per-store Auto Scraping: scrape at set local times, then retry Failed rows.

    Separate from ``SyncSchedule`` (which is deleted when Scheduled Updates is
    turned off). Each run: all active rows to Pending, scrape, then up to
    ``retry_failed_cycles`` times reset only Failed rows to Pending and scrape again.
    """
    MAX_RUN_TIMES = 6
    MAX_RETRY_CYCLES = 10

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.OneToOneField(Store, on_delete=models.CASCADE, related_name='auto_scrape_schedule')
    is_active = models.BooleanField(default=False)
    run_times = models.JSONField(default=_default_auto_scrape_times, help_text='Local "HH:MM" times, max 6.')
    timezone = models.CharField(max_length=64, default='UTC')
    retry_failed_cycles = models.PositiveSmallIntegerField(default=2)
    push_after = models.BooleanField(default=False)
    last_triggered_at = models.DateTimeField(null=True, blank=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_finished_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=20, blank=True, default='')
    last_summary = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'sync_autoscrapeschedule'

    def __str__(self):
        return f"Auto scrape for {self.store.name} ({', '.join(self.run_times or [])})"


class VendorSchedule(models.Model):
    """Own run times for one vendor on one store (inventory-only stores).

    ``kind='scrape'`` overrides the store's Auto Scraping times for this vendor;
    ``kind='update'`` overrides its Scheduled Updates times. A vendor with its own
    row is left out of the store-wide run of the same kind. Rows only run while the
    store's card of the same kind is on (``AutoScrapeSchedule`` / ``SyncSchedule``).
    """
    KIND_SCRAPE = 'scrape'
    KIND_UPDATE = 'update'
    KIND_CHOICES = [(KIND_SCRAPE, 'Auto Scraping'), (KIND_UPDATE, 'Scheduled Updates')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='vendor_schedules')
    vendor = models.ForeignKey('vendor.Vendor', on_delete=models.CASCADE, related_name='store_vendor_schedules')
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    is_active = models.BooleanField(default=True)
    run_times = models.JSONField(default=list, blank=True)
    timezone = models.CharField(max_length=64, default='UTC')
    retry_failed_cycles = models.PositiveSmallIntegerField(default=2)
    last_triggered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'sync_vendorschedule'
        constraints = [
            models.UniqueConstraint(fields=['store', 'vendor', 'kind'], name='uniq_vendor_schedule_store_vendor_kind'),
        ]

    def __str__(self):
        return f"{self.kind} for {self.vendor_id} on {self.store_id} ({', '.join(self.run_times or [])})"


class ScheduledRun(models.Model):
    """One Auto Scraping or Scheduled Update run (store-wide or one vendor).

    Also the per-store queue: a run waits while the store is busy, and only one
    run is ``running`` per store at a time. ``heartbeat_at`` moves while the run
    is alive, so a run killed by a worker restart stops blocking the store.
    """
    STATUS_WAITING = 'waiting'
    STATUS_RUNNING = 'running'
    FINISHED_STATUSES = ('success', 'partial', 'stopped', 'failed', 'skipped')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='scheduled_runs')
    vendor = models.ForeignKey(
        'vendor.Vendor', null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )
    kind = models.CharField(max_length=10)  # 'scrape' | 'update'
    status = models.CharField(max_length=20, default=STATUS_WAITING, db_index=True)
    slot_at = models.DateTimeField(null=True, blank=True, help_text='The run time that started it.')
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    summary = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'sync_scheduledrun'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['store', 'status'], name='sync_schedrun_store_status'),
            models.Index(fields=['store', 'kind', 'vendor', 'created_at'], name='sync_schedrun_lookup'),
        ]

    def __str__(self):
        return f"{self.kind} run {self.store_id} vendor={self.vendor_id} ({self.status})"


class StoreSyncRun(models.Model):
    """Execution history for sync jobs."""
    STATUS_CHOICES = [
        ('running', 'Running'),
        ('success', 'Success'),
        ('partial', 'Partial'),
        ('failed', 'Failed'),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='sync_runs')
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='running')
    listings_processed = models.IntegerField(default=0)
    listings_updated = models.IntegerField(default=0)
    error_summary = models.TextField(null=True, blank=True)

    class Meta:
        db_table = 'sync_storesyncrun'
        ordering = ['-started_at']

    def __str__(self):
        return f"Sync {self.store.name} @ {self.started_at} ({self.status})"


class ScrapeRun(models.Model):
    """Scrape run for a CatalogUpload. Tracks scrape+price/inventory calc for catalog rows."""
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        RUNNING = 'running', 'Running'
        SUCCESS = 'success', 'Success'
        PARTIAL = 'partial', 'Partial'
        FAILED = 'failed', 'Failed'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    catalog_upload = models.ForeignKey(
        'catalog.CatalogUpload',
        on_delete=models.CASCADE,
        related_name='scrape_runs',
        db_index=True,
    )
    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name='scrape_runs',
        db_index=True,
    )
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    rows_processed = models.PositiveIntegerField(default=0)
    rows_succeeded = models.PositiveIntegerField(default=0)
    error_summary = models.TextField(null=True, blank=True)

    class Meta:
        db_table = 'sync_scraperun'
        ordering = ['-started_at']

    def __str__(self):
        return f"Scrape {self.store.name} @ {self.started_at} ({self.status})"


