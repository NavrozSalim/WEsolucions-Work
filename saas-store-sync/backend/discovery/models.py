import uuid

from django.conf import settings
from django.db import models


class DiscoveryJob(models.Model):
    class Marketplace(models.TextChoices):
        AMAZON_US = 'amazon_us', 'Amazon US'
        AMAZON_AU = 'amazon_au', 'Amazon AU'
        EBAY_US = 'ebay_us', 'eBay US'
        EBAY_AU = 'ebay_au', 'eBay AU'

    class Mode(models.TextChoices):
        CATEGORY = 'category', 'Category'
        PRODUCT = 'product', 'Product details'

    class Status(models.TextChoices):
        QUEUED = 'queued', 'Queued'
        RUNNING = 'running', 'Running'
        SUCCEEDED = 'succeeded', 'Succeeded'
        FAILED = 'failed', 'Failed'
        CANCELLED = 'cancelled', 'Cancelled'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='discovery_jobs',
    )
    parent = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='children',
    )
    marketplace = models.CharField(max_length=20, choices=Marketplace.choices)
    mode = models.CharField(max_length=20, choices=Mode.choices)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.QUEUED,
        db_index=True,
    )
    original_filename = models.CharField(max_length=255, blank=True)
    source_file = models.FileField(upload_to='discovery/uploads/%Y/%m/')
    source_bytes = models.BinaryField(null=True, blank=True)
    result_file = models.FileField(upload_to='discovery/results/%Y/%m/', blank=True)
    result_bytes = models.BinaryField(null=True, blank=True)
    rules = models.JSONField(default=dict, blank=True)
    columns = models.JSONField(default=list, blank=True)
    use_sample = models.BooleanField(default=False)
    zip_code = models.CharField(max_length=12, blank=True)
    queue_name = models.CharField(max_length=32, blank=True)
    celery_task_id = models.CharField(max_length=255, blank=True, default='')
    cancel_requested = models.BooleanField(default=False)
    stats = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.marketplace} {self.mode} {self.status}'

    def read_source(self) -> bytes:
        """Spreadsheet bytes from the shared database, then the local file."""
        if self.source_bytes:
            return bytes(self.source_bytes)
        if not self.source_file:
            return b''
        with self.source_file.open('rb') as handle:
            return handle.read()

    def read_result(self) -> bytes:
        """Result spreadsheet from the shared database, then the local file."""
        if self.result_bytes:
            return bytes(self.result_bytes)
        if not self.result_file:
            return b''
        with self.result_file.open('rb') as handle:
            return handle.read()


class DiscoveryProduct(models.Model):
    """One kept product, written as soon as the scraper accepts it."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(
        DiscoveryJob,
        on_delete=models.CASCADE,
        related_name='products',
    )
    product_key = models.CharField(max_length=80)
    data = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        constraints = [
            models.UniqueConstraint(fields=['job', 'product_key'], name='discovery_product_job_key'),
        ]
