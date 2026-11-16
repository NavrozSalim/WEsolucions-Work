"""
Residential proxy pool for eBay US (``ebay.com``).

Same API as ``ebay_au_proxies`` so ``EbayHTTP`` can drive either market.
Only US lists are read.

Configuration (precedence for URLs):

    EBAY_US_PROXY_URLS — eBay-only list (optional)
    US_PROXY_URLS      — shared US list when the eBay list is unset

    EBAY_US_DISABLE_PROXIES          — 1 to send eBay from the VPS IP
    EBAY_US_MIN_REQUEST_GAP_SEC      — per-proxy gap (default 3)
    EBAY_US_HTTP_RETRIES             — proxy rotations before giving up (default 2)
    EBAY_US_PROXY_BLOCK_COOLDOWN_SEC — rest after a block (default 300)
    EBAY_US_PROXY_SHARED             — workers share rest and use through Redis (default 0)
    EBAY_US_PROXY_HOLD_SEC           — a worker keeps its proxy to itself this long (default 120)
    EBAY_US_PROXY_MAX_WAIT_SEC       — wait for a free proxy before leaving a listing Pending (default 240)
"""
from __future__ import annotations

import logging
import os
import random
import threading
import time
from typing import Optional

from .amazon_us_proxies import load_us_proxy_urls
from .costco_au_proxies import CostcoAuProxyPool, ProxyAssignment

logger = logging.getLogger("scrapers.ebay_us.proxies")

_POOL_LOCK = threading.Lock()
_POOL: Optional[CostcoAuProxyPool] = None

_SESSION_PROXY_URL = "ebay_us_proxy_url"
_SESSION_PROXY_INDEX = "ebay_us_proxy_index"


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def ebay_us_proxy_disabled() -> bool:
    return (os.environ.get("EBAY_US_DISABLE_PROXIES") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def load_ebay_us_proxy_urls(env: dict | None = None) -> list[str]:
    return load_us_proxy_urls("EBAY_US_PROXY_URLS", env)


def get_ebay_us_pool() -> Optional[CostcoAuProxyPool]:
    if ebay_us_proxy_disabled():
        return None
    global _POOL
    if _POOL is not None:
        return _POOL
    with _POOL_LOCK:
        if _POOL is None:
            urls = load_ebay_us_proxy_urls()
            if not urls:
                return None
            _POOL = CostcoAuProxyPool(
                urls, min_gap_sec=max(0.0, _env_float("EBAY_US_MIN_REQUEST_GAP_SEC", 3.0)), name="eBay US",
            )
            logger.info(
                "eBay US proxy pool initialised: %d proxies, min_gap=%.1fs",
                len(urls), _POOL._min_gap_sec,
            )
    return _POOL


def reset_pool_for_tests() -> None:
    global _POOL
    with _POOL_LOCK:
        _POOL = None


def proxies_configured() -> bool:
    pool = get_ebay_us_pool()
    return pool is not None and pool.size > 0


def acquire_proxy(session: dict | None, *, force_rotate: bool = False) -> Optional[ProxyAssignment]:
    pool = get_ebay_us_pool()
    if pool is None:
        return None
    return pool.acquire(force_rotate=force_rotate)


def remember_proxy(session: dict | None, assignment: ProxyAssignment | None) -> None:
    if session is None or assignment is None:
        return
    session[_SESSION_PROXY_URL] = assignment.url
    session[_SESSION_PROXY_INDEX] = assignment.index


def mark_proxy_success(assignment: ProxyAssignment | None) -> None:
    pool = get_ebay_us_pool()
    if pool and assignment:
        pool.mark_success(assignment)


def block_cooldown_sec() -> float:
    """How long a proxy eBay refused rests (``EBAY_US_PROXY_BLOCK_COOLDOWN_SEC``, default 300)."""
    return max(10.0, _env_float("EBAY_US_PROXY_BLOCK_COOLDOWN_SEC", 300.0))


def mark_proxy_blocked(assignment: ProxyAssignment | None) -> None:
    pool = get_ebay_us_pool()
    if pool and assignment:
        pool.mark_blocked(assignment, cooldown_sec=block_cooldown_sec())
        if shared_mode():
            _shared_set(f"rest:{assignment.label}", "1", block_cooldown_sec())
            release_hold(assignment)


# ---------------------------------------------------------------------------
# Shared pool memory. Each worker process has its own pool object, so without
# this a proxy one worker saw refused was picked by another seconds later, and
# two workers could use one IP at once. Redis (the Django cache) holds which
# proxies are resting after a refusal and which worker holds which proxy.
# ---------------------------------------------------------------------------

_SHARED_PREFIX = "ebay_us_proxy:"


class ProxyPoolBusy(Exception):
    """Every eBay US proxy is resting or held by another worker."""


def shared_mode() -> bool:
    """Workers share proxy rest and use (``EBAY_US_PROXY_SHARED``, default off).

    Tried on 2026-11-16: with refused IPs resting 10 minutes for every worker,
    7 of 49 Chrome tries got through (24% without) and prices per hour fell
    from about 200 to 7. eBay's refusals did not depend on how hard an IP was used.
    """
    return (os.environ.get("EBAY_US_PROXY_SHARED") or "0").strip().lower() in ("1", "true", "yes", "on")


def hold_sec() -> float:
    """How long a worker keeps a proxy to itself after its last use (``EBAY_US_PROXY_HOLD_SEC``, default 120)."""
    return max(10.0, _env_float("EBAY_US_PROXY_HOLD_SEC", 120.0))


def max_wait_sec() -> float:
    """How long a worker waits for a free proxy before leaving the listing Pending (default 240)."""
    return max(0.0, _env_float("EBAY_US_PROXY_MAX_WAIT_SEC", 240.0))


def _cache():
    try:
        from django.core.cache import cache

        return cache
    except Exception:
        return None


def _shared_get(key: str):
    cache = _cache()
    if cache is None:
        return None
    try:
        return cache.get(_SHARED_PREFIX + key)
    except Exception:
        return None


def _shared_set(key: str, value: str, timeout: float) -> None:
    cache = _cache()
    if cache is None:
        return
    try:
        cache.set(_SHARED_PREFIX + key, value, int(max(1, timeout)))
    except Exception:
        pass


def _owner() -> str:
    return f"{os.getpid()}:{threading.get_ident()}"


def is_resting(label: str) -> bool:
    return _shared_get(f"rest:{label}") is not None


def try_hold(label: str) -> bool:
    """Take (or keep) this proxy for this worker. False while another worker holds it."""
    cache = _cache()
    if cache is None:
        return True
    key = _SHARED_PREFIX + f"hold:{label}"
    me = _owner()
    try:
        if cache.add(key, me, int(hold_sec())):
            return True
        if cache.get(key) == me:
            cache.set(key, me, int(hold_sec()))
            return True
        return False
    except Exception:
        return True


def release_hold(assignment: ProxyAssignment | None) -> None:
    cache = _cache()
    if cache is None or assignment is None:
        return
    key = _SHARED_PREFIX + f"hold:{assignment.label}"
    try:
        if cache.get(key) == _owner():
            cache.delete(key)
    except Exception:
        pass


def acquire_shared(current: ProxyAssignment | None, *, force_rotate: bool = False) -> Optional[ProxyAssignment]:
    """A proxy that is not resting and not held by another worker, or None.

    Keeps ``current`` while it is fine, so one Chrome stays on one IP.
    """
    pool = get_ebay_us_pool()
    if pool is None or pool.size == 0:
        return None
    if current is not None and not force_rotate:
        if not is_resting(current.label) and try_hold(current.label):
            return current
    if current is not None:
        release_hold(current)
    n = pool.size
    start = random.randrange(n)
    for i in range(n):
        idx = (start + i) % n
        if force_rotate and current is not None and idx == current.index and n > 1:
            continue
        url = pool._urls[idx]
        label = pool._label(url)
        if is_resting(label):
            continue
        if try_hold(label):
            return ProxyAssignment(index=idx, url=url, label=label)
    return None


def acquire_shared_wait(current: ProxyAssignment | None, *, force_rotate: bool = False) -> ProxyAssignment:
    """Like :func:`acquire_shared`, but waits for a proxy to come free.

    Raises :class:`ProxyPoolBusy` after ``max_wait_sec``; the listing is then left Pending.
    """
    deadline = time.monotonic() + max_wait_sec()
    logged = False
    while True:
        assignment = acquire_shared(current, force_rotate=force_rotate)
        if assignment is not None:
            return assignment
        if time.monotonic() >= deadline:
            raise ProxyPoolBusy("every eBay US proxy is resting or in use")
        if not logged:
            logger.info("eBay US: every proxy is resting or in use; waiting for one to free up")
            logged = True
        time.sleep(min(10.0, max(1.0, deadline - time.monotonic())))


def bind_curl_client(client, assignment: ProxyAssignment) -> None:
    client.proxies = assignment.as_requests_proxy()


def http_retry_limit() -> int:
    raw = (os.environ.get("EBAY_US_HTTP_RETRIES") or "2").strip()
    try:
        return max(0, int(raw)) + 1
    except ValueError:
        return 3


__all__ = [
    "ProxyAssignment",
    "acquire_proxy",
    "bind_curl_client",
    "ebay_us_proxy_disabled",
    "get_ebay_us_pool",
    "http_retry_limit",
    "load_ebay_us_proxy_urls",
    "mark_proxy_blocked",
    "mark_proxy_success",
    "proxies_configured",
    "remember_proxy",
    "reset_pool_for_tests",
]
