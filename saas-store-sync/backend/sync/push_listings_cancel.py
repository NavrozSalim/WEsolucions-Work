"""Cooperative cancel for Manual sync (marketplace push) — mirrors scrape cancel."""
from __future__ import annotations

from django.core.cache import cache

from sync.push_listings_lock import PUSH_LISTINGS_LOCK_TTL_SEC

PUSH_LISTINGS_CANCEL_TTL_SEC = PUSH_LISTINGS_LOCK_TTL_SEC


class PushListingsCancelled(Exception):
    """Raised when the user stops Manual sync and workers exit cooperatively."""


def push_listings_cancel_key(store_id: str) -> str:
    return f'catalog:push_listings_cancel:{store_id}'


def request_push_listings_cancel(store_id: str) -> None:
    cache.set(
        push_listings_cancel_key(str(store_id)),
        '1',
        PUSH_LISTINGS_CANCEL_TTL_SEC,
    )


def should_abort_push_listings(store_id: str) -> bool:
    return bool(cache.get(push_listings_cancel_key(str(store_id))))


def clear_push_listings_cancel(store_id: str) -> None:
    cache.delete(push_listings_cancel_key(str(store_id)))
