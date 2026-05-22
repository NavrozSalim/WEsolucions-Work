"""
HEB US proxy pool.

Static US residential proxies for server-side HEB scraping on the US worker.
Configuration precedence (highest first):

    HEB_US_PROXY_URLS  — full URLs (http://user:pass@host:port,...)
    PROXY_URLS         — generic fallback
    HEB_US_PROXY_URL / PROXY_URL — single URL
    PROXY_ENDPOINTS + PROXY_USER/PASS
"""
from __future__ import annotations

import logging
import os
import random
import re
import threading
import time
from dataclasses import dataclass
from typing import Iterable, Optional
from urllib.parse import quote, urlparse, urlunparse

logger = logging.getLogger("scrapers.heb_us.proxies")


def _env(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip()


def _split(raw: str) -> list[str]:
    if not raw:
        return []
    parts = re.split(r"[\s,]+", raw)
    return [p.strip() for p in parts if p and p.strip()]


def _normalize_proxy(raw: str, default_scheme: str = "http",
                     default_user: str = "", default_pass: str = "") -> str | None:
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw:
        return None

    if "://" in raw:
        try:
            parts = urlparse(raw)
        except Exception:
            return None
        host = parts.hostname
        if not host:
            return None
        port = parts.port
        user = parts.username
        pwd = parts.password
        scheme = (parts.scheme or default_scheme).lower()
        netloc = host if not port else f"{host}:{port}"
        if user:
            cred = quote(user, safe="")
            if pwd is not None:
                cred = f"{cred}:{quote(pwd, safe='')}"
            netloc = f"{cred}@{netloc}"
        return urlunparse((scheme, netloc, "", "", "", ""))

    if ":" not in raw:
        return None
    host, _, port = raw.partition(":")
    host = host.strip()
    port = port.strip()
    if not host or not port.isdigit():
        return None
    netloc = f"{host}:{port}"
    if default_user:
        cred = quote(default_user, safe="")
        if default_pass:
            cred = f"{cred}:{quote(default_pass, safe='')}"
        netloc = f"{cred}@{netloc}"
    scheme = (default_scheme or "http").lower()
    return f"{scheme}://{netloc}"


def load_proxy_urls(env: Optional[dict] = None) -> list[str]:
    """Read proxy URLs from environment in precedence order."""
    env = env if env is not None else os.environ

    def get(name: str, default: str = "") -> str:
        v = env.get(name)
        if v is None:
            return default
        return str(v).strip()

    default_user = get("PROXY_USER")
    default_pass = get("PROXY_PASS")
    default_scheme = (get("PROXY_SCHEME") or "http").lower()

    candidates: list[str] = []
    for var in ("HEB_US_PROXY_URLS", "PROXY_URLS"):
        raw = get(var)
        if raw:
            candidates.extend(_split(raw))
            if candidates:
                break

    if not candidates:
        for var in ("HEB_US_PROXY_URL", "PROXY_URL"):
            raw = get(var)
            if raw:
                candidates.append(raw)
                break

    if not candidates:
        candidates.extend(_split(get("PROXY_ENDPOINTS")))

    out: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        norm = _normalize_proxy(raw, default_scheme, default_user, default_pass)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
    return out


def proxies_configured(env: Optional[dict] = None) -> bool:
    return bool(load_proxy_urls(env))


@dataclass
class ProxyAssignment:
    index: int
    url: str
    label: str

    def as_requests_proxy(self) -> dict:
        return {"http": self.url, "https": self.url}


class HebUsProxyPool:
    def __init__(self, urls: Iterable[str], *, min_gap_sec: float = 0.0) -> None:
        self._urls = [u for u in urls if u]
        self._min_gap_sec = max(0.0, float(min_gap_sec or 0.0))
        self._lock = threading.Lock()
        self._cursor = random.randint(0, max(0, len(self._urls) - 1)) if self._urls else 0
        self._sticky: dict[int, int] = {}
        self._cooldown_until: dict[int, float] = {}
        self._last_req_at: dict[int, float] = {}

    @property
    def size(self) -> int:
        return len(self._urls)

    def _label(self, url: str) -> str:
        try:
            p = urlparse(url)
            host = p.hostname or ""
            port = p.port
            return f"{host}:{port}" if port else host
        except Exception:
            return "unknown"

    def _next_free_index(self, now: float, *, exclude: Optional[int] = None) -> Optional[int]:
        n = len(self._urls)
        if n == 0:
            return None
        start = self._cursor % n
        for i in range(n):
            idx = (start + i) % n
            if exclude is not None and idx == exclude:
                continue
            if self._cooldown_until.get(idx, 0.0) <= now:
                return idx
        candidates = [(idx, self._cooldown_until.get(idx, 0.0)) for idx in range(n)
                        if exclude is None or idx != exclude]
        if not candidates:
            return None
        return min(candidates, key=lambda kv: kv[1])[0]

    def acquire(self, *, force_rotate: bool = False) -> Optional[ProxyAssignment]:
        if not self._urls:
            return None
        tid = threading.get_ident()
        now = time.monotonic()
        with self._lock:
            if force_rotate:
                self._sticky.pop(tid, None)
            current = self._sticky.get(tid)
            if current is not None and current < len(self._urls):
                if self._cooldown_until.get(current, 0.0) <= now:
                    url = self._urls[current]
                    return ProxyAssignment(index=current, url=url, label=self._label(url))
            idx = self._next_free_index(now, exclude=current if force_rotate else None)
            if idx is None:
                return None
            self._sticky[tid] = idx
            self._cursor = idx + 1
            url = self._urls[idx]
            return ProxyAssignment(index=idx, url=url, label=self._label(url))

    def mark_blocked(self, assignment: ProxyAssignment, cooldown_sec: float = 300.0) -> None:
        if assignment is None:
            return
        now = time.monotonic()
        with self._lock:
            self._cooldown_until[assignment.index] = now + max(0.0, float(cooldown_sec))
        log_fn = logger.warning if cooldown_sec >= 300.0 else logger.debug
        log_fn(
            "HEB US proxy %d (%s) cooled down for %.0fs after block",
            assignment.index, assignment.label, cooldown_sec,
        )

    def wait_for_gap(self, assignment: ProxyAssignment, *, jitter_pct: float = 0.20) -> None:
        if assignment is None or self._min_gap_sec <= 0:
            self._record_request(assignment)
            return
        now = time.monotonic()
        with self._lock:
            last = self._last_req_at.get(assignment.index, 0.0)
        elapsed = now - last
        gap = self._min_gap_sec
        if jitter_pct > 0:
            gap += random.uniform(0, gap * jitter_pct)
        wait = gap - elapsed
        if wait > 0:
            time.sleep(wait)
        self._record_request(assignment)

    def _record_request(self, assignment: Optional[ProxyAssignment]) -> None:
        if assignment is None:
            return
        with self._lock:
            self._last_req_at[assignment.index] = time.monotonic()


_POOL_LOCK = threading.Lock()
_POOL: Optional[HebUsProxyPool] = None


def _min_gap_sec_from_env() -> float:
    raw = os.environ.get("HEB_US_MIN_REQUEST_GAP_SEC", "3")
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 3.0


def get_pool() -> Optional[HebUsProxyPool]:
    global _POOL
    if _POOL is not None:
        return _POOL
    with _POOL_LOCK:
        if _POOL is None:
            urls = load_proxy_urls()
            if not urls:
                return None
            _POOL = HebUsProxyPool(urls, min_gap_sec=_min_gap_sec_from_env())
            logger.info(
                "HEB US proxy pool initialised: %d proxies, min_gap=%.1fs",
                len(urls), _POOL._min_gap_sec,
            )
    return _POOL


def reset_pool_for_tests() -> None:
    global _POOL
    with _POOL_LOCK:
        _POOL = None


__all__ = [
    "ProxyAssignment",
    "HebUsProxyPool",
    "load_proxy_urls",
    "proxies_configured",
    "get_pool",
    "reset_pool_for_tests",
]
