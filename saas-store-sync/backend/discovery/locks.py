"""One live worker per discovery job.

Redis redelivers a task that is still unacked. A second delivery must not
delete products the first worker already saved, and it must not scrape beside
that worker.
"""

from __future__ import annotations

import contextvars
import logging
import threading
import time

from django.core.cache import cache

logger = logging.getLogger(__name__)

# Renewed while the scrape runs. After the worker dies the lock expires and
# the next delivery continues from the products already saved.
LOCK_TTL_SEC = 180
LOCK_REFRESH_SEC = 30

lock_owner_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    'discovery_lock_owner',
    default=None,
)

_state_lock = threading.Lock()
_refreshed_at: dict[str, float] = {}


def discovery_lock_key(job_id) -> str:
    return f'discovery-job-lock:{job_id}'


def acquire_discovery_lock(job_id, owner: str) -> bool:
    try:
        return cache.add(discovery_lock_key(job_id), owner, LOCK_TTL_SEC)
    except Exception:
        logger.warning('discovery lock unavailable; continuing job %s', job_id, exc_info=True)
        return True


def refresh_discovery_lock(job_id, owner: str | None, *, force: bool = False) -> None:
    if not owner:
        return
    token = str(job_id)
    now = time.monotonic()
    with _state_lock:
        last = _refreshed_at.get(token, 0.0)
        if not force and now - last < LOCK_REFRESH_SEC:
            return
        _refreshed_at[token] = now
    try:
        key = discovery_lock_key(job_id)
        if cache.get(key) == owner:
            cache.set(key, owner, LOCK_TTL_SEC)
    except Exception:
        with _state_lock:
            _refreshed_at.pop(token, None)
        logger.warning('discovery lock refresh failed for %s', job_id, exc_info=True)


def release_discovery_lock(job_id, owner: str) -> None:
    with _state_lock:
        _refreshed_at.pop(str(job_id), None)
    try:
        key = discovery_lock_key(job_id)
        if cache.get(key) == owner:
            cache.delete(key)
    except Exception:
        logger.warning('discovery lock release failed for %s', job_id, exc_info=True)
