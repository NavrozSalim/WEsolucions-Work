"""
Products app: Product mappings, uploads, scrape history.

Product = vendor catalog item. ProductMapping (catalog app) links Product to Store.
"""
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models


class Upload(models.Model):
    """Bulk upload/import record (file metadata and processing state)."""
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        PROCESSING = 'processing', 'Processing'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'
        EXPIRED = 'expired', 'Expired'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='uploads',
        db_index=True,
    )
    store = models.ForeignKey(
        'stores.Store',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='uploads',
        db_index=True,
    )
    original_name = models.CharField(max_length=255)
    stored_key = models.CharField(max_length=500, null=True, blank=True, db_index=True)
    note = models.TextField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    processed_count = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    progress_data = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'products_upload'
        ordering = ['-completed_at', '-id']

    def __str__(self):
        return f"{self.original_name} ({self.status})"


class Product(models.Model):
    """
    Vendor/source catalog item. Global, not store-specific.
    ProductMapping links Product to Store for marketplace listings.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vendor = models.ForeignKey(
        'vendor.Vendor',
        on_delete=models.PROTECT,
        related_name='products',
        db_index=True,
    )
    upload = models.ForeignKey(
        Upload,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='products',
        db_index=True,
    )
    vendor_sku = models.CharField(max_length=255, db_index=True)
    variation_id = models.CharField(max_length=255, default='', blank=True, db_index=True)
    vendor_url = models.URLField(max_length=1000, null=True, blank=True)

    class Meta:
        db_table = 'products_product'
        ordering = ['vendor_sku']
        constraints = [
            models.UniqueConstraint(
                fields=['vendor', 'vendor_sku', 'variation_id'],
                name='uq_product_vendor_sku_variation',
            ),
        ]

    def __str__(self):
        return f"{self.vendor_sku} @ {self.vendor.code}"


class Scrape(models.Model):
    """
    Scrape history per product. Each sync run creates a Scrape record.
    VendorPrice holds only the latest; this table is the full audit log.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='scrapes',
        db_index=True,
    )
    scrape_time = models.DateTimeField(db_index=True)
    price_cents = models.PositiveIntegerField(null=True, blank=True)
    stock = models.PositiveIntegerField(null=True, blank=True)
    error_code = models.CharField(max_length=50, null=True, blank=True, db_index=True)
    raw_response = models.JSONField(null=True, blank=True)
    calculated_shipping_price = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        null=True,
        blank=True,
    )
    error_details = models.TextField(null=True, blank=True)
    final_inventory = models.PositiveIntegerField(null=True, blank=True)
    final_price = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
    )
    needs_rescrape = models.BooleanField(default=False, db_index=True)
    raw_ended_listings = models.JSONField(null=True, blank=True)
    raw_handling_time = models.JSONField(null=True, blank=True)
    raw_price = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
    )
    raw_quantity = models.PositiveIntegerField(null=True, blank=True)
    raw_seller_away = models.JSONField(null=True, blank=True)
    raw_shipping = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = 'products_scrape'
        ordering = ['-scrape_time']
        indexes = [
            models.Index(fields=['product', '-scrape_time'], name='scrape_prod_time'),
        ]
        get_latest_by = 'scrape_time'

    def __str__(self):
        return f"Scrape {self.product_id} @ {self.scrape_time}"
