import uuid
from django.db import models
from django.conf import settings


class AuditLog(models.Model):
    """Immutable log for sensitive actions. Retention policy can be applied separately."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='audit_logs',
    )
    store = models.ForeignKey(
        'stores.Store',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_logs',
        db_index=True,
    )
    action = models.CharField(max_length=64)  # e.g. store_created, token_updated, product_deleted
    object_type = models.CharField(max_length=64)  # store, product_mapping, pricing_rule
    object_id = models.CharField(max_length=255)
    timestamp = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['user', '-timestamp']),
            models.Index(fields=['store', '-timestamp'], name='idx_audit_store_ts'),
            models.Index(fields=['object_type', 'object_id']),
            models.Index(fields=['-timestamp']),
        ]
        verbose_name = 'Audit log'
        verbose_name_plural = 'Audit logs'

    def __str__(self):
        return f"{self.action} {self.object_type}:{self.object_id} @ {self.timestamp}"
