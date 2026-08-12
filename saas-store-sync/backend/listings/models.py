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


class InventorySyncStatus(models.TextChoices):
    """Vendor scrape / marketplace inventory sync for managed listings."""
    PENDING = 'pending', 'Pending'
    SCRAPED = 'scraped', 'Scraped'
    SYNCED = 'synced', 'Synced'
    FAILED = 'failed', 'Failed'


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
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='store_listings',
    )
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='managed_listings', db_index=True)

    external_product_key = models.CharField(max_length=255)
    external_variant_key = models.CharField(max_length=255)
    title = models.CharField(max_length=500, blank=True, default='')
    description = models.TextField(blank=True, default='')
    brand = models.CharField(max_length=255, blank=True, default='')
    category = models.CharField(max_length=500, blank=True, default='')
    sku = models.CharField(max_length=255, blank=True, default='', db_index=True)
    barcode = models.CharField(max_length=255, blank=True, default='')
    # Legacy combined options string (also auto-filled from option_N name/value pairs).
    options = models.CharField(
        max_length=500,
        blank=True,
        default='',
        help_text='Combined Options summary (e.g. Size=XL; Color=Blue). Prefer option_1..4 fields.',
    )
    option_1_name = models.CharField(max_length=100, blank=True, default='')
    option_1_value = models.CharField(max_length=255, blank=True, default='')
    option_2_name = models.CharField(max_length=100, blank=True, default='')
    option_2_value = models.CharField(max_length=255, blank=True, default='')
    option_3_name = models.CharField(max_length=100, blank=True, default='')
    option_3_value = models.CharField(max_length=255, blank=True, default='')
    option_4_name = models.CharField(max_length=100, blank=True, default='')
    option_4_value = models.CharField(max_length=255, blank=True, default='')
    # Variant-specific image (required when Product Key differs from Variant Key).
    variation_image_url = models.CharField(max_length=1000, blank=True, default='')
    # Source URL (vendor / supplier product page) for price scrape / fulfillment.
    vendor_url = models.CharField(max_length=1000, blank=True, default='')
    # Nora / supplier barcode matched to inventory Excel after cleaning.
    vendor_id = models.CharField(
        max_length=255,
        blank=True,
        default='',
        db_index=True,
        help_text='Supplier Vendor ID (Nora BarCode after -G1/-V* normalization).',
    )
    # Template "Vendor Name" resolved to a vendor code (e.g. noraau, amazonus, amazonau).
    source_vendor_code = models.CharField(
        max_length=50,
        blank=True,
        default='',
        db_index=True,
        help_text='Canonical source vendor code from the Vendor Name template column.',
    )
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

    # Vendor scrape → listing price/stock (same scrapers as catalog inventory)
    vendor_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    inventory_sync_status = models.CharField(
        max_length=20,
        choices=InventorySyncStatus.choices,
        default=InventorySyncStatus.PENDING,
        db_index=True,
    )
    last_scrape_at = models.DateTimeField(null=True, blank=True)
    last_scrape_error = models.CharField(max_length=500, blank=True, default='')

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
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='listing_uploads',
    )
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
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='marketplace_orders',
    )
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


class TicketStatus(models.TextChoices):
    OPEN = 'open', 'Open'
    PENDING = 'pending', 'Pending'
    ANSWERED = 'answered', 'Answered'
    CLOSED = 'closed', 'Closed'


class TicketMessageDirection(models.TextChoices):
    INBOUND = 'inbound', 'Inbound (customer)'
    OUTBOUND = 'outbound', 'Outbound (seller)'
    SYSTEM = 'system', 'System'


class SupportTicket(models.Model):
    """Customer support ticket / message thread for a managed marketplace store."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='support_tickets',
    )
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='support_tickets', db_index=True)

    external_ticket_key = models.CharField(max_length=255, blank=True, default='', db_index=True)
    subject = models.CharField(max_length=500, blank=True, default='')
    customer_name = models.CharField(max_length=255, blank=True, default='')
    customer_email = models.CharField(max_length=255, blank=True, default='')
    related_order_key = models.CharField(max_length=255, blank=True, default='')
    status = models.CharField(max_length=30, choices=TicketStatus.choices, default=TicketStatus.OPEN, db_index=True)
    unread_count = models.IntegerField(default=0)
    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_customer_message_at = models.DateTimeField(null=True, blank=True)
    environment = models.CharField(max_length=20, choices=Environment.choices, default=Environment.STAGING)
    raw_response_json = models.JSONField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'listings_supportticket'
        ordering = ['-last_message_at', '-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['store', 'external_ticket_key', 'environment'],
                name='uq_ticket_store_key_env',
            ),
        ]
        indexes = [
            models.Index(fields=['store', 'status'], name='ticket_store_status'),
        ]

    def __str__(self):
        return self.subject or self.external_ticket_key or str(self.pk)


class TicketMessage(models.Model):
    """A single message inside a support ticket thread."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ticket = models.ForeignKey(SupportTicket, on_delete=models.CASCADE, related_name='messages')

    external_message_key = models.CharField(max_length=255, blank=True, default='', db_index=True)
    direction = models.CharField(max_length=20, choices=TicketMessageDirection.choices, default=TicketMessageDirection.INBOUND)
    body = models.TextField(blank=True, default='')
    sender_name = models.CharField(max_length=255, blank=True, default='')
    sender_type = models.CharField(max_length=50, blank=True, default='')  # customer | seller | operator | system
    delivered_to_marketplace = models.BooleanField(default=False)
    marketplace_request_json = models.JSONField(null=True, blank=True)
    marketplace_response_json = models.JSONField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'listings_ticketmessage'
        ordering = ['sent_at', 'created_at']

    def __str__(self):
        return f"{self.direction} msg for ticket {self.ticket_id}"
