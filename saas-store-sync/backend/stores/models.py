import uuid
from decimal import Decimal

from django.db import models
from django.conf import settings

from core.fields import EncryptedTextField


class Store(models.Model):
    CONNECTION_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('connected', 'Connected'),
        ('error', 'Error'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='stores')
    name = models.CharField(max_length=255)
    region = models.CharField(max_length=10, choices=[('USA', 'USA'), ('AU', 'Australia')])
    api_token = EncryptedTextField(help_text="Encrypted at rest; set ENCRYPTION_KEY in production")
    marketplace = models.ForeignKey(
        'marketplace.Marketplace',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='stores_legacy',
        db_index=True,
    )
    connection_status = models.CharField(
        max_length=20, choices=CONNECTION_STATUS_CHOICES, default='pending', db_index=True,
    )
    last_validated_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True, help_text='Whether store is active for syncing')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class StorePriceRange(models.Model):
    """Reusable price range (from_value, to_value). Use MAX value for open-ended."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    from_value = models.DecimalField(max_digits=14, decimal_places=4, default=Decimal('0'))
    to_value = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)  # NULL = MAX
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'stores_storepricerange'
        ordering = ['from_value']

    def __str__(self):
        to = self.to_value if self.to_value is not None else 'MAX'
        return f"${self.from_value} - ${to}"


class StoreVendorPriceSettings(models.Model):
    """Per-store, per-vendor pricing. Tax, fees, tiered margins, plus simple multiplier/rounding fallback."""
    ROUNDING_CHOICES = [
        ('none', 'No Rounding'),
        ('nearest_99', 'Nearest .99'),
        ('nearest_int', 'Nearest Integer'),
        ('ceil', 'Ceiling'),
        ('floor', 'Floor'),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='vendor_price_settings', db_index=True)
    vendor = models.ForeignKey('vendor.Vendor', on_delete=models.CASCADE, related_name='store_vendor_price_settings', db_index=True)
    purchase_tax_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0'), null=True, blank=True)
    marketplace_fees_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0'), null=True, blank=True)
    multiplier = models.FloatField(default=1.0, help_text="Fallback when no tier matches")
    optional_fee = models.FloatField(default=0.0)
    rounding_option = models.CharField(max_length=20, choices=ROUNDING_CHOICES, default='none')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'stores_storevendorpricesettings'
        unique_together = [('store', 'vendor')]
        verbose_name_plural = 'Store vendor price settings'

    def __str__(self):
        return f"{self.store.name} / {self.vendor.code}"


class StorePriceRangeMargin(models.Model):
    """Tiered margin per price range for a store+vendor."""
    MARGIN_TYPE_CHOICES = [
        ('percentage', 'Percentage markup'),
        ('fixed', 'Fixed dollar add-on'),
        ('direct', 'Direct multiplier'),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    price_settings = models.ForeignKey(
        StoreVendorPriceSettings,
        on_delete=models.CASCADE,
        related_name='range_margins',
        db_index=True,
    )
    price_range = models.ForeignKey(
        StorePriceRange,
        on_delete=models.CASCADE,
        related_name='margins',
        db_index=True,
    )
    margin_type = models.CharField(
        max_length=20, choices=MARGIN_TYPE_CHOICES, default='percentage',
        help_text='percentage: price = cost_after_tax × (1 + value/100). fixed: price = cost_after_tax + value.',
    )
    margin_percentage = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0'),
        help_text='Meaning depends on margin_type: percentage points (e.g. 25 = +25%%) or fixed USD amount.',
    )
    minimum_margin_cents = models.IntegerField(default=0, null=True, blank=True)
    dont_pay_discount_percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'stores_storepricerangemargin'
        unique_together = [('price_settings', 'price_range')]
        verbose_name_plural = 'Store price range margins'


class StoreVendorInventorySettings(models.Model):
    """Per-store, per-vendor inventory. Range multipliers or simple rule_type fallback."""
    RULE_TYPES = [
        ('multiplier', 'Multiplier'),
        ('fixed', 'Fixed Quantity'),
        ('cap', 'Cap (Maximum)'),
        ('floor', 'Floor (Minimum)'),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='vendor_inventory_settings', db_index=True)
    vendor = models.ForeignKey('vendor.Vendor', on_delete=models.CASCADE, related_name='store_vendor_inventory_settings', db_index=True)
    rule_type = models.CharField(max_length=20, choices=RULE_TYPES, default='multiplier')
    default_multiplier = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal('1'), null=True, blank=True)
    default_value = models.IntegerField(default=1, help_text="For fixed/cap/floor rules")
    zero_if_low = models.BooleanField(default=True, help_text="Treat '1 left' as 0")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'stores_storevendorinventorysettings'
        unique_together = [('store', 'vendor')]
        verbose_name_plural = 'Store vendor inventory settings'

    def __str__(self):
        return f"{self.store.name} / {self.vendor.code}"


class StoreInventoryRangeMultiplier(models.Model):
    """Inventory rule per quantity range: multiplier (stock × factor) or fixed value."""
    RANGE_TYPE_CHOICES = [
        ('multiplier', 'Multiplier'),
        ('fixed', 'Fixed Value'),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    inventory_settings = models.ForeignKey(
        StoreVendorInventorySettings,
        on_delete=models.CASCADE,
        related_name='range_multipliers',
        db_index=True,
    )
    from_value = models.DecimalField(max_digits=14, decimal_places=4, default=Decimal('0'))
    to_value = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)  # NULL = MAX
    range_type = models.CharField(max_length=20, choices=RANGE_TYPE_CHOICES, default='multiplier')
    multiplier = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal('1'), help_text='Used when range_type=multiplier')
    fixed_value = models.IntegerField(null=True, blank=True, help_text='Store stock when range_type=fixed')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'stores_storeinventoryrangemultiplier'
        verbose_name_plural = 'Store inventory range multipliers'


