"""Orphan / reclaim / purge stores when a user is deleted.

Keeps marketplace connection data (listings, orders, catalog mappings) for a
retention window so another user can reconnect the same credentials and recover
system-only fields (e.g. vendor URLs) without keeping data forever.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from stores.credentials import marketplace_kind

logger = logging.getLogger(__name__)


def retention_days() -> int:
    try:
        return max(1, min(3650, int(getattr(settings, 'STORE_ORPHAN_RETENTION_DAYS', 90))))
    except (TypeError, ValueError):
        return 90


def _norm(value) -> str:
    return str(value or '').strip()


def credential_fingerprint_from_parts(*parts: str) -> str | None:
    cleaned = [_norm(p) for p in parts]
    if not any(cleaned):
        return None
    joined = '|'.join(cleaned)
    return hashlib.sha256(joined.encode('utf-8')).hexdigest()


def store_credential_fingerprint(store) -> str | None:
    """Stable hash of the credentials that identify a marketplace connection."""
    kind = marketplace_kind(getattr(store, 'marketplace', None))
    if kind == 'lasoo':
        return credential_fingerprint_from_parts(
            getattr(store, 'lasoo_staging_auth_key', None),
            getattr(store, 'lasoo_production_auth_key', None),
        )
    if kind == 'mydeal':
        return credential_fingerprint_from_parts(
            getattr(store, 'mydeal_sandbox_seller_id', None),
            getattr(store, 'mydeal_production_seller_id', None),
            getattr(store, 'mydeal_sandbox_client_id', None),
            getattr(store, 'mydeal_production_client_id', None),
        )
    if kind == 'kogan':
        return credential_fingerprint_from_parts(
            getattr(store, 'kogan_sheet_id', None),
            getattr(store, 'kogan_tab_name', None),
            getattr(store, 'kogan_service_account_json', None),
        )
    # Reverb, Sears, Walmart, and other api_token-based marketplaces
    return credential_fingerprint_from_parts(getattr(store, 'api_token', None))


def fingerprint_from_create_payload(*, marketplace, store_data: dict) -> str | None:
    """Build a fingerprint from validated create payload (before Store exists)."""
    kind = marketplace_kind(marketplace)
    if kind == 'lasoo':
        return credential_fingerprint_from_parts(
            store_data.get('lasoo_staging_auth_key'),
            store_data.get('lasoo_production_auth_key'),
        )
    if kind == 'mydeal':
        return credential_fingerprint_from_parts(
            store_data.get('mydeal_sandbox_seller_id'),
            store_data.get('mydeal_production_seller_id'),
            store_data.get('mydeal_sandbox_client_id'),
            store_data.get('mydeal_production_client_id'),
        )
    if kind == 'kogan':
        return credential_fingerprint_from_parts(
            store_data.get('kogan_sheet_id'),
            store_data.get('kogan_tab_name'),
            store_data.get('kogan_service_account_json'),
        )
    return credential_fingerprint_from_parts(store_data.get('api_token'))


@transaction.atomic
def orphan_stores_for_user(user) -> int:
    """Detach all stores from ``user`` and mark them for retention purge."""
    from stores.models import Store
    from sync.models import SyncSchedule

    now = timezone.now()
    stores = list(Store.objects.filter(user=user).select_related('marketplace'))
    if not stores:
        return 0

    for store in stores:
        store.orphaned_at = now
        store.credential_fingerprint = store_credential_fingerprint(store) or ''
        store.is_active = False
        store.user = None
        store.save(update_fields=[
            'orphaned_at',
            'credential_fingerprint',
            'is_active',
            'user',
            'updated_at',
        ])
        SyncSchedule.objects.filter(store=store).update(is_active=False)
        logger.info(
            'Orphaned store %s (%s) for deleted user; retention %s days',
            store.id,
            store.name,
            retention_days(),
        )
    return len(stores)


def find_orphaned_store(*, marketplace, fingerprint: str | None):
    """Return the newest orphaned store matching marketplace + credential fingerprint."""
    from stores.models import Store

    if not marketplace or not fingerprint:
        return None
    return (
        Store.objects.filter(
            user__isnull=True,
            orphaned_at__isnull=False,
            marketplace=marketplace,
            credential_fingerprint=fingerprint,
        )
        .order_by('-orphaned_at')
        .first()
    )


@transaction.atomic
def re_orphan_store(store) -> None:
    """Return a single store to orphaned state (e.g. reclaim failed credential verify)."""
    from sync.models import SyncSchedule

    now = timezone.now()
    store.orphaned_at = now
    store.credential_fingerprint = store_credential_fingerprint(store) or ''
    store.is_active = False
    store.user = None
    store.save(update_fields=[
        'orphaned_at',
        'credential_fingerprint',
        'is_active',
        'user',
        'updated_at',
    ])
    SyncSchedule.objects.filter(store=store).update(is_active=False)


@transaction.atomic
def reclaim_store(store, user, *, store_data: dict | None = None):
    """Assign an orphaned store to ``user`` and apply fresh connection fields."""
    from stores.models import Store

    store_data = store_data or {}
    name = _norm(store_data.get('name')) or store.name
    # Avoid unique (user, name, marketplace) clash with an existing live store.
    if Store.objects.filter(user=user, name=name, marketplace=store.marketplace).exclude(pk=store.pk).exists():
        name = f'{name} (reclaimed)'
        base = name
        n = 2
        while Store.objects.filter(user=user, name=name, marketplace=store.marketplace).exclude(pk=store.pk).exists():
            name = f'{base} {n}'
            n += 1

    updatable = (
        'name',
        'region',
        'management_mode',
        'api_token',
        'is_active',
        'kogan_service_account_json',
        'kogan_sheet_id',
        'kogan_tab_name',
        'kogan_sku_column',
        'kogan_stock_column',
        'kogan_price_column',
        'kogan_rrp_column',
        'kogan_first_price_column',
        'mydeal_setup_method',
        'mydeal_environment',
        'mydeal_sandbox_base_url',
        'mydeal_production_base_url',
        'mydeal_sandbox_client_id',
        'mydeal_sandbox_client_secret',
        'mydeal_sandbox_seller_id',
        'mydeal_sandbox_seller_token',
        'mydeal_production_client_id',
        'mydeal_production_client_secret',
        'mydeal_production_seller_id',
        'mydeal_production_seller_token',
        'lasoo_environment',
        'lasoo_staging_base_url',
        'lasoo_production_base_url',
        'lasoo_staging_auth_key',
        'lasoo_production_auth_key',
    )
    for key in updatable:
        if key in store_data and store_data[key] is not None:
            setattr(store, key, store_data[key])
    store.name = name
    store.user = user
    store.orphaned_at = None
    store.credential_fingerprint = store_credential_fingerprint(store) or ''
    store.is_active = True if store_data.get('is_active', True) else False
    store.save()

    _reassign_store_related_users(store, user)
    logger.info('Reclaimed store %s (%s) for user %s', store.id, store.name, getattr(user, 'email', user.pk))
    return store


def _reassign_store_related_users(store, user) -> None:
    """Point related rows that still reference the old (deleted) user at the new owner."""
    from catalog.models import CatalogUpload
    from listings.models import ListingUpload, MarketplaceOrder, StoreListing, SupportTicket
    from products.models import Product

    StoreListing.objects.filter(store=store).update(user=user)
    ListingUpload.objects.filter(store=store).update(user=user)
    MarketplaceOrder.objects.filter(store=store).update(user=user)
    SupportTicket.objects.filter(store=store).update(user=user)
    CatalogUpload.objects.filter(store=store).update(user=user)

    product_ids = store.products.values_list('product_id', flat=True)
    if product_ids:
        Product.objects.filter(id__in=product_ids).update(owner=user)


@transaction.atomic
def purge_expired_orphaned_stores() -> dict:
    """Hard-delete orphaned stores past the retention window."""
    from stores.models import Store

    cutoff = timezone.now() - timedelta(days=retention_days())
    qs = Store.objects.filter(user__isnull=True, orphaned_at__isnull=False, orphaned_at__lt=cutoff)
    count = qs.count()
    deleted = 0
    for store in qs.iterator():
        store_id = store.id
        name = store.name
        store.delete()
        deleted += 1
        logger.info('Purged orphaned store %s (%s) past retention', store_id, name)
    return {'scanned': count, 'deleted': deleted, 'retention_days': retention_days()}
