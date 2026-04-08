"""
Catalog app: ProductMapping, CatalogUpload, CatalogUploadRow, CatalogSyncLog, ReverbUpdateLog.
"""
import uuid
from django.conf import settings
from django.db import models
from stores.models import Store


class ProductMapping(models.Model):
    """Store listing: store + product, marketplace SKUs, synced price/stock."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='products')
    product = models.ForeignKey(
        'products.Product',
        on_delete=models.CASCADE,
        related_name='listings',
        db_index=True,
    )
    title = models.CharField(max_length=500, null=True, blank=True)
    marketplace_child_sku = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    marketplace_parent_sku = models.CharField(max_length=255, null=True, blank=True)
    marketplace_id = models.CharField(
        max_length=255, null=True, blank=True, db_index=True,
        help_text='Reverb listing ID for API updates',
    )
    is_active = models.BooleanField(
        default=True,
        help_text='False = soft-deleted; will end listing on Reverb during Update',
    )

    store_price = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    store_stock = models.IntegerField(null=True, blank=True)
    pack_qty = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    prep_fees = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    shipping_fees = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    sync_status = models.CharField(max_length=50, default='pending', db_index=True)
    failed_sync_count = models.IntegerField(default=0)
    last_sync_time = models.DateTimeField(null=True, blank=True)
    last_scrape_time = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Last successful scrape that applied vendor price/stock locally.',
    )

    class Meta:
        db_table = 'catalog_productmapping'
        ordering = ['product__vendor_sku']
        constraints = [
            models.UniqueConstraint(
                fields=['store', 'product'],
                name='uq_productmapping_store_product',
            ),
        ]

    def __str__(self):
        return f"{self.product.vendor_sku} - {self.store.name}"


class CatalogUpload(models.Model):
    """Bulk catalog upload session. Rows stored in CatalogUploadRow."""
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        VALIDATED = 'validated', 'Validated'
        PROCESSING = 'processing', 'Processing'
        SYNCED = 'synced', 'Synced'
        PARTIAL = 'partial', 'Partial'
        FAILED = 'failed', 'Failed'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='catalog_uploads',
        db_index=True,
    )
    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name='catalog_uploads',
        db_index=True,
    )
    original_filename = models.CharField(max_length=255)
    total_rows = models.PositiveIntegerField(default=0)
    processed_rows = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    error_summary = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'catalog_catalogupload'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.original_filename} ({self.status})"


class CatalogActivityLog(models.Model):
    """User-facing catalog timeline (scrape, sync, actions). List API returns last 24 hours."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name='catalog_activity_logs',
        db_index=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='catalog_activity_logs',
    )
    action_type = models.CharField(max_length=64, db_index=True)
    message = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'catalog_catalogactivitylog'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.action_type} @ {self.created_at}"


class CatalogUploadRow(models.Model):
    """Single row from catalog upload. Raw values preserved; resolved FKs after sync."""
    class SyncStatus(models.TextChoices):
        PENDING = 'pending', 'Pending'
        ADDED = 'added', 'Added'
        UPDATED = 'updated', 'Updated'
        DELETED = 'deleted', 'Deleted'
        SKIPPED = 'skipped', 'Skipped'
        ERROR = 'error', 'Error'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    catalog_upload = models.ForeignKey(
        CatalogUpload,
        on_delete=models.CASCADE,
        related_name='rows',
        db_index=True,
    )
    row_number = models.PositiveIntegerField()

    # Raw columns - preserve exact input including "N/A"
    vendor_name_raw = models.CharField(max_length=255)
    vendor_id_raw = models.CharField(max_length=255, default='', blank=True)
    is_variation_raw = models.CharField(max_length=50, default='', blank=True)
    variation_id_raw = models.CharField(max_length=255, default='', blank=True)
    marketplace_name_raw = models.CharField(max_length=255, default='', blank=True)
    store_name_raw = models.CharField(max_length=255)
    marketplace_parent_sku_raw = models.CharField(max_length=255, default='', blank=True)
    marketplace_child_sku_raw = models.CharField(max_length=255, default='', blank=True)
    marketplace_id_raw = models.CharField(max_length=255, default='', blank=True)
    vendor_sku_raw = models.CharField(max_length=255, default='', blank=True)
    vendor_url_raw = models.CharField(max_length=1000, default='', blank=True)
    action_raw = models.CharField(max_length=20, default='Add')
    pack_qty_raw = models.CharField(max_length=255, default='', blank=True)
    prep_fees_raw = models.CharField(max_length=255, default='', blank=True)
    shipping_fees_raw = models.CharField(max_length=255, default='', blank=True)

    # Resolved after validation
    vendor = models.ForeignKey(
        'vendor.Vendor',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='catalog_upload_rows',
    )
    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='catalog_upload_rows',
    )
    sync_status = models.CharField(
        max_length=20,
        choices=SyncStatus.choices,
        default=SyncStatus.PENDING,
        db_index=True,
    )
    sync_error = models.TextField(null=True, blank=True)
    product = models.ForeignKey(
        'products.Product',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='catalog_upload_rows',
    )
    product_mapping = models.ForeignKey(
        ProductMapping,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='catalog_upload_rows',
    )

    class Meta:
        db_table = 'catalog_cataloguploadrow'
        ordering = ['catalog_upload', 'row_number']
        constraints = [
            models.UniqueConstraint(
                fields=['catalog_upload', 'row_number'],
                name='uq_cataloguploadrow_upload_row',
            ),
        ]

    def __str__(self):
        return f"Row {self.row_number} ({self.sync_status})"


class CatalogSyncLog(models.Model):
    """Per-row sync result (Add/Update/Delete)."""
    class Action(models.TextChoices):
        ADD = 'add', 'Add'
        UPDATE = 'update', 'Update'
        DELETE = 'delete', 'Delete'

    class Status(models.TextChoices):
        SUCCESS = 'success', 'Success'
        SKIPPED = 'skipped', 'Skipped'
        ERROR = 'error', 'Error'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    catalog_upload = models.ForeignKey(
        CatalogUpload,
        on_delete=models.CASCADE,
        related_name='sync_logs',
        db_index=True,
    )
    catalog_upload_row = models.OneToOneField(
        CatalogUploadRow,
        on_delete=models.CASCADE,
        related_name='sync_log',
    )
    action = models.CharField(max_length=20, choices=Action.choices)
    status = models.CharField(max_length=20, choices=Status.choices)
    message = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'catalog_catalogsynclog'
        ordering = ['-created_at']


class ReverbUpdateLog(models.Model):
    """Per-listing push to Reverb API."""
    class Status(models.TextChoices):
        SUCCESS = 'success', 'Success'
        FAILED = 'failed', 'Failed'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product_mapping = models.ForeignKey(
        ProductMapping,
        on_delete=models.CASCADE,
        related_name='reverb_update_logs',
        db_index=True,
    )
    status = models.CharField(max_length=20, choices=Status.choices)
    http_status = models.IntegerField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    retry_count = models.PositiveIntegerField(default=0)
    pushed_price = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True,
    )
    pushed_stock = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'catalog_reverbupdatelog'
        ordering = ['-created_at']
