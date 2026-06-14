"""Per Sears seller_id lock — one feed push at a time across all stores sharing credentials."""
from __future__ import annotations

from django.core.cache import cache

SEARS_SELLER_LOCK_TTL_SEC = 24 * 60 * 60  # 24h — large bulk syncs can take many hours


def sears_seller_lock_key(seller_id: str) -> str:
    return f'catalog:sears_seller_push_lock:{seller_id}'


def try_acquire_sears_seller_lock(seller_id: str, owner_id: str) -> bool:
    if not seller_id:
        return True
    return cache.add(
        sears_seller_lock_key(str(seller_id)),
        str(owner_id),
        SEARS_SELLER_LOCK_TTL_SEC,
    )


def release_sears_seller_lock(seller_id: str, owner_id: str) -> None:
    if not seller_id:
        return
    key = sears_seller_lock_key(str(seller_id))
    if cache.get(key) == str(owner_id):
        cache.delete(key)
