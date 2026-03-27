"""
Vendor app: Vendors, latest scraped prices, Google OAuth credentials.
"""
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models


class Vendor(models.Model):
    """Lookup table for vendor sources (Amazon, Vevor, AliExpress, eBay)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, unique=True, db_index=True)
    name = models.CharField(max_length=255)

    class Meta:
        db_table = 'vendor_vendor'
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.code})"


class VendorPrice(models.Model):
    """
    Scraped vendor price/stock per product. Multiple records over time.
    Query latest via product.vendor_prices.order_by('-scraped_at').first()
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(
        'products.Product',
        on_delete=models.CASCADE,
        related_name='vendor_prices',
        db_index=True,
    )
    price = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
    )
    stock = models.PositiveIntegerField(null=True, blank=True)
    error_code = models.CharField(max_length=50, null=True, blank=True, db_index=True)
    scraped_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        db_table = 'vendor_vendorprice'
        ordering = ['-scraped_at']
        indexes = [
            models.Index(fields=['product', '-scraped_at'], name='vprice_prod_scraped'),
        ]
        verbose_name_plural = 'Vendor prices'
        get_latest_by = 'scraped_at'

    def __str__(self):
        return f"{self.product_id} @ {self.price} (scraped {self.scraped_at})"


class GoogleOAuthCredentials(models.Model):
    """Google OAuth tokens per user (for Sheets, Drive, etc.)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='google_oauth_credentials',
        db_index=True,
    )
    user_email = models.EmailField(null=True, blank=True)
    google_user_id = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    access_token = models.TextField(null=True, blank=True)
    refresh_token = models.TextField(null=True, blank=True)
    token_uri = models.URLField(max_length=500, null=True, blank=True)
    client_id = models.CharField(max_length=500, null=True, blank=True)
    client_secret = models.CharField(max_length=500, null=True, blank=True)
    scopes = models.JSONField(default=list, blank=True)
    expiry = models.DateTimeField(null=True, blank=True)
    is_valid = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'vendor_googleoauthcredentials'
        verbose_name_plural = 'Google OAuth credentials'

    def __str__(self):
        return f"Google OAuth: {self.user_email or self.user_id}"
