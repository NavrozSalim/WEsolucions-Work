"""
Call log_action() from views/signals when sensitive actions occur.
"""
import logging
from django.conf import settings

logger = logging.getLogger(__name__)


def log_action(user, action, object_type, object_id, metadata=None, request=None):
    """
    Create an AuditLog entry. Use from views after store create, token update,
    product delete, pricing/stock rule change, etc.
    """
    from audit.models import AuditLog

    metadata = metadata or {}
    ip = None
    if request:
        xff = request.META.get('HTTP_X_FORWARDED_FOR')
        ip = (xff.split(',')[0].strip() if xff else None) or request.META.get('REMOTE_ADDR')

    AuditLog.objects.create(
        user=user,
        action=action,
        object_type=object_type,
        object_id=str(object_id),
        metadata=metadata,
        ip_address=ip,
    )
    logger.info("audit", extra={"action": action, "object_type": object_type, "object_id": str(object_id)})
