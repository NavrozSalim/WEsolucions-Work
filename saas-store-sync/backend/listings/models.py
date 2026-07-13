"""
Listings app: products created inside the system for managed (full_store) stores,
plus marketplace orders and shipments pulled back from those marketplaces.

Catalog uploads (inventory-only sync) stay in the catalog app; this app covers
the "we create the listing on the marketplace" flow, starting with Lasoo.
"""
import uuid

from django.conf import settings
from django.db import models

from stores.models import Store


class Environment(models.TextChoices):
    STAGING = 'staging', 'Staging'
    PRODUCTION = 'production', 'Production'


class ListingStatus(models.TextChoices):
    DRAFT = 'draft', 'Draft'
    VALIDATION_FAILED = 'validation_failed', 'Validation Failed'
    READY = 'ready', 'Ready'
    UPLOADED_STAGING = 'uploaded_staging', 'Uploaded (Staging)'
    UPLOADED_PRODUCTION = 'uploaded_production', 'Uploaded (Production)'
    FAILED = 'failed', 'Upload Failed'


class ListingAction(models.TextChoices):
    """How a listing entered the system: newly created here, mapped to an
    existing marketplace listing, or (for uploads) a delete request."""
    CREATE = 'create', 'Create'
    MAPPED = 'mapped', 'Mapped'
    DELETE = 'delete', 'Delete'


class OrderStatus(models.TextChoices):
    NEW = 'new', 'New'
    PAID = 'paid', 'Paid'
    CANCELLED = 'cancelled', 'Cancelled'
    REFUNDED = 'refunded', 'Refunded'
    SENT = 'sent', 'Sent'
    SHIPPING_SUBMITTED = 'shipping_submitted', 'Shipping Submitted'
    SHIPPING_COMPLETE = 'shipping_complete', 'Shipping Complete'


class StoreListing(models.Model):
    """A product created in the system and pushed to the store's marketplace."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='store_listings')
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='managed_listings', db_index=True)

    external_product_key = models.CharField(max_length=255)
    external_variant_key = models.CharField(max_length=255)
    title = models.CharField(max_length=500, blank=True, default='')
    description = models.TextField(blank=True, default='')
    brand = models.CharField(max_length=255, blank=True, default='')
    category = models.CharField(max_length=500, blank=True, default='')
    sku = models.CharField(max_length=255, blank=True, default='', db_index=True)
    barcode = models.CharField(max_length=255, blank=True, default='')
    image_urls = models.TextField(blank=True, default='')  # pipe-joined: a|b|c
    inventory = models.IntegerField(default=0)
    infinite_quantity = models.BooleanField(default=False)
    original_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    sale_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    original_price_cents = models.IntegerField(default=0)
    sale_price_cents = models.IntegerField(default=0)
    external_data_object_json = models.TextField(blank=True, default='')

    environment = models.CharField(max_length=20, choices=Environment.choices, default=Environment.STAGING)
    action = models.CharField(max_length=20, choices=ListingAction.choices, default=ListingAction.CREATE)
    status = models.CharField(max_length=30, choices=ListingStatus.choices, default=ListingStatus.DRAFT, db_index=True)
    validation_errors_json = models.JSONField(null=True, blank=True)
    marketplace_request_json = models.JSONField(null=True, blank=True)
    marketplace_response_json = models.JSONField(null=True, blank=True)
    last_uploaded_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'listings_storelisting'
        ordering = ['-created_at']
        indexes = [models.Index(fields=['store', 'status'], name='listing_store_status')]
        constraints = [
            models.UniqueConstraint(
                fields=['store', 'external_variant_key', 'environment'],
                name='uq_listing_store_variant_env',
            ),
        ]

    def __str__(self):
        return f"{self.external_variant_key} - {self.title}"


class ListingUpload(models.Model):
    """History record for managed-store listing changes: bulk template files
    and single-listing actions (create/edit/delete). Powers Upload history."""

    class Source(models.TextChoices):
        FILE = 'file', 'File'
        SINGLE = 'single', 'Single'

    class Status(models.TextChoices):
        COMPLETED = 'completed', 'Completed'
        PARTIAL = 'partial', 'Partial'
        FAILED = 'failed', 'Failed'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='listing_uploads')
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='listing_uploads', db_index=True)

    filename = models.CharField(max_length=500, blank=True, default='')
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.FILE)
    action = models.CharField(max_length=20, choices=ListingAction.choices, default=ListingAction.CREATE)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.COMPLETED)
    total_rows = models.IntegerField(default=0)
    success_rows = models.IntegerField(default=0)
    error_rows = models.IntegerField(default=0)
    rows_json = models.JSONField(null=True, blank=True)  # per-row results incl. errors
    message = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'listings_listingupload'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.action} {self.filename or self.source} ({self.store_id})"


class MarketplaceOrder(models.Model):
    """Order/invoice pulled from the marketplace for a managed store."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='marketplace_orders')
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='marketplace_orders', db_index=True)

    external_order_key = models.CharField(max_length=255, blank=True, default='', db_index=True)
    invoice_number = models.CharField(max_length=255, blank=True, default='')
    customer_info_json = models.JSONField(null=True, blank=True)
    line_items_json = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=30, choices=OrderStatus.choices, default=OrderStatus.NEW, db_index=True)
    shipping_status = models.CharField(max_length=30, default='pending')
    total_amount_cents = models.IntegerField(null=True, blank=True)
    raw_response_json = models.JSONField(null=True, blank=True)
    environment = models.CharField(max_length=20, choices=Environment.choices, default=Environment.STAGING)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'listings_marketplaceorder'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['store', 'external_order_key', 'environment'],
                name='uq_order_store_key_env',
            ),
        ]

    def __str__(self):
        return self.invoice_number or self.external_order_key or str(self.pk)


class OrderShipment(models.Model):
    """Tracking info submitted to the marketplace for an order."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(MarketplaceOrder, on_delete=models.CASCADE, related_name='shipments')
    tracking_number = models.CharField(max_length=255, blank=True, default='')
    carrier = models.CharField(max_length=255, blank=True, default='')
    tracking_url = models.URLField(max_length=1000, blank=True, default='')
    shipped_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=30, default='pending')
    marketplace_request_json = models.JSONField(null=True, blank=True)
    marketplace_response_json = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'listings_ordershipment'
        ordering = ['-created_at']

    def __str__(self):
        return f"Shipment {self.tracking_number} for order {self.order_id}"
