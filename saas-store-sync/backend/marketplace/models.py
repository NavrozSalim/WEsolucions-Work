"""
Marketplace app: Lookup table for supported marketplaces (Reverb, Etsy, Walmart, etc.).
Store management lives in the stores app.
"""
import uuid

from django.db import models


class Marketplace(models.Model):
    """Lookup table for supported marketplaces (Reverb, Etsy, Walmart, etc.)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, unique=True, db_index=True)
    name = models.CharField(max_length=255)

    class Meta:
        db_table = 'marketplace_marketplace'
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.code})"
