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
    EBAY_US_PROXY_BLOCK_COOLDOWN_SEC — cooldown after a block (default 300)
"""
from __future__ import annotations

import logging
import os
import threading
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
                urls, min_gap_sec=max(0.0, _env_float("EBAY_US_MIN_REQUEST_GAP_SEC", 3.0)),
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


def mark_proxy_blocked(assignment: ProxyAssignment | None) -> None:
    pool = get_ebay_us_pool()
    if pool and assignment:
        pool.mark_blocked(
            assignment,
            cooldown_sec=max(10.0, _env_float("EBAY_US_PROXY_BLOCK_COOLDOWN_SEC", 300.0)),
        )


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
