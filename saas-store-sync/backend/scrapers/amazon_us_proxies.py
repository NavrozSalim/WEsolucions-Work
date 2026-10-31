"""
Residential proxy pool for Amazon US (``amazon.com``) HTTP scrapes.

Same sticky pool as Amazon AU / eBay AU. Only US lists are read, so an AU
proxy never ends up in front of ``amazon.com`` on a shared env file.

Configuration (precedence for URLs):

    AMAZON_US_PROXY_URLS  — Amazon-only list (optional)
    US_PROXY_URLS         — shared US list when the Amazon list is unset

    AMAZON_US_DISABLE_PROXIES          — 1 to send Amazon from the VPS IP
    AMAZON_US_MIN_REQUEST_GAP_SEC      — per-proxy gap (default 2)
    AMAZON_US_PROXY_BLOCK_COOLDOWN_SEC — cooldown after a block (default 300)
"""
from __future__ import annotations

import logging
import os
import re
import threading
from typing import Optional

from .costco_au_proxies import (
    CostcoAuProxyPool,
    ProxyAssignment,
    _normalize_proxy,
)

logger = logging.getLogger("scrapers.amazon_us.proxies")

_POOL_LOCK = threading.Lock()
_POOL: Optional[CostcoAuProxyPool] = None


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def proxies_disabled() -> bool:
    return (os.environ.get("AMAZON_US_DISABLE_PROXIES") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def load_us_proxy_urls(primary_var: str, env: dict | None = None) -> list[str]:
    """``primary_var`` first, then ``US_PROXY_URLS``. Shared by Amazon US and eBay US."""
    source = os.environ if env is None else env
    scheme = (source.get("PROXY_SCHEME") or "http").lower()
    user = source.get("PROXY_USER", "")
    password = source.get("PROXY_PASS", "")
    for var in (primary_var, "US_PROXY_URLS"):
        raw = (source.get(var) or "").strip()
        if not raw:
            continue
        out: list[str] = []
        for part in re.split(r"[\s,]+", raw):
            norm = _normalize_proxy(part.strip(), scheme, user, password) if part.strip() else None
            if norm and norm not in out:
                out.append(norm)
        if out:
            return out
    return []


def load_amazon_us_proxy_urls(env: dict | None = None) -> list[str]:
    return load_us_proxy_urls("AMAZON_US_PROXY_URLS", env)


def get_pool() -> Optional[CostcoAuProxyPool]:
    if proxies_disabled():
        return None
    global _POOL
    if _POOL is not None:
        return _POOL
    with _POOL_LOCK:
        if _POOL is None:
            urls = load_amazon_us_proxy_urls()
            if not urls:
                return None
            _POOL = CostcoAuProxyPool(
                urls, min_gap_sec=max(0.0, _env_float("AMAZON_US_MIN_REQUEST_GAP_SEC", 2.0)),
            )
            logger.info(
                "Amazon US proxy pool initialised: %d proxies, min_gap=%.1fs",
                len(urls), _POOL._min_gap_sec,
            )
    return _POOL


def reset_pool_for_tests() -> None:
    global _POOL
    with _POOL_LOCK:
        _POOL = None


def proxies_configured() -> bool:
    pool = get_pool()
    return pool is not None and pool.size > 0


def acquire_proxy(*, force_rotate: bool = False) -> Optional[ProxyAssignment]:
    pool = get_pool()
    if pool is None:
        return None
    return pool.acquire(force_rotate=force_rotate)


def wait_for_gap(assignment: Optional[ProxyAssignment]) -> None:
    pool = get_pool()
    if pool is not None and assignment is not None:
        pool.wait_for_gap(assignment)


def mark_blocked(assignment: Optional[ProxyAssignment]) -> None:
    pool = get_pool()
    if pool is not None and assignment is not None:
        pool.mark_blocked(
            assignment,
            cooldown_sec=max(10.0, _env_float("AMAZON_US_PROXY_BLOCK_COOLDOWN_SEC", 300.0)),
        )


def mark_success(assignment: Optional[ProxyAssignment]) -> None:
    pool = get_pool()
    if pool is not None and assignment is not None:
        pool.mark_success(assignment)


__all__ = [
    "acquire_proxy",
    "get_pool",
    "load_amazon_us_proxy_urls",
    "load_us_proxy_urls",
    "mark_blocked",
    "mark_success",
    "proxies_configured",
    "proxies_disabled",
    "reset_pool_for_tests",
    "wait_for_gap",
]
