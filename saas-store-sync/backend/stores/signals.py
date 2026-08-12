"""Store lifecycle signals."""
from django.conf import settings
from django.db.models.signals import pre_delete
from django.dispatch import receiver


@receiver(pre_delete, sender=settings.AUTH_USER_MODEL)
def orphan_stores_before_user_delete(sender, instance, **kwargs):
    """Retain marketplace stores/listings/orders when a user account is removed."""
    from stores.orphan import orphan_stores_for_user

    orphan_stores_for_user(instance)
