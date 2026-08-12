"""Organization-aware query helpers."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db.models import QuerySet

User = get_user_model()


def organization_user_ids(user) -> list:
    """User ids that share the same organization (or just self)."""
    if not user or not getattr(user, 'is_authenticated', False):
        return []
    org = getattr(user, 'organization', None)
    if org is None and user.account_type == User.AccountType.SUPER_USER:
        org = getattr(user, 'owned_organization', None)
    if org is not None:
        return list(org.users.values_list('id', flat=True))
    return [user.id]


def stores_for_user(user) -> QuerySet:
    """Stores owned by the user or any member of their organization.

    Orphaned stores (owner deleted, waiting reclaim/purge) are excluded.
    """
    from stores.models import Store

    base = Store.objects.filter(orphaned_at__isnull=True, user__isnull=False)

    if getattr(user, 'is_superuser', False) or getattr(user, 'is_staff', False):
        return base

    ids = organization_user_ids(user)
    if not ids:
        return Store.objects.none()
    return base.filter(user_id__in=ids)


def user_can_access_store(user, store) -> bool:
    if not user or not store:
        return False
    if getattr(store, 'orphaned_at', None) is not None or store.user_id is None:
        return False
    if getattr(user, 'is_superuser', False) or getattr(user, 'is_staff', False):
        return True
    return store.user_id in set(organization_user_ids(user))
