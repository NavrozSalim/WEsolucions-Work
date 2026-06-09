"""Per-store lock so only one marketplace push (Manual sync) runs at a time."""
from __future__ import annotations

from django.core.cache import cache

PUSH_LISTINGS_LOCK_TTL_SEC = 6 * 60 * 60  # 6h — large catalogs can take hours


def push_listings_lock_key(store_id: str) -> str:
    return f'catalog:push_listings_lock:{store_id}'


def is_push_listings_locked(store_id: str) -> bool:
    return bool(cache.get(push_listings_lock_key(str(store_id))))


def try_acquire_push_listings_lock(store_id: str, owner_id: str) -> bool:
    return cache.add(
        push_listings_lock_key(str(store_id)),
        str(owner_id),
        PUSH_LISTINGS_LOCK_TTL_SEC,
    )


def release_push_listings_lock(store_id: str, owner_id: str) -> None:
    key = push_listings_lock_key(str(store_id))
    if cache.get(key) == str(owner_id):
        cache.delete(key)


def handoff_push_listings_lock(store_id: str, from_owner: str, to_owner: str) -> bool:
    """Transfer lock from HTTP reservation to Celery task id."""
    key = push_listings_lock_key(str(store_id))
    if cache.get(key) != str(from_owner):
        return False
    cache.set(key, str(to_owner), PUSH_LISTINGS_LOCK_TTL_SEC)
    return True
