"""Proxies for discovery's category and search pages.

Product pages already go through the catalog scrapers, which use the store
proxy pools. The pages discovery crawls to find products (categories, search
results) used a plain HTTP session from the server's own IP, the address most
likely to be blocked. Each crawl session now takes a proxy from the same pool
the catalog scraper uses for that marketplace; when a page comes back blocked,
that proxy rests (the pool's cooldown) and the session moves to the next one.

No pool configured on the server: nothing changes (the server's own IP).
``DISCOVERY_CRAWL_PROXIES=0`` turns it off.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

BLOCK_STATUSES = {403, 429, 503}


def crawl_proxies_enabled() -> bool:
    return (os.environ.get('DISCOVERY_CRAWL_PROXIES') or '1').strip().lower() not in ('0', 'false', 'no', 'off')


def crawl_retries() -> int:
    """Extra tries on another proxy after a blocked page (``DISCOVERY_CRAWL_PROXY_RETRIES``, default 2)."""
    try:
        return max(0, min(5, int(os.environ.get('DISCOVERY_CRAWL_PROXY_RETRIES') or '2')))
    except ValueError:
        return 2


class CrawlProxy:
    """The proxy of one crawl session, from the marketplace's catalog pool."""

    def __init__(self, marketplace: str):
        self.marketplace = marketplace
        self.assignment = None
        self._pool_state: dict = {}  # the eBay pools keep their pick in a dict

    # --- the four catalog pools, behind one shape --------------------------------

    def _pool(self):
        mp = self.marketplace
        if mp == 'amazon_au':
            from scrapers import amazon_au_proxies as p

            return (p.proxies_configured, lambda rotate: p.acquire_proxy(force_rotate=rotate),
                    p.mark_blocked, p.mark_success)
        if mp == 'amazon_us':
            from scrapers import amazon_us_proxies as p

            return (p.proxies_configured, lambda rotate: p.acquire_proxy(force_rotate=rotate),
                    p.mark_blocked, p.mark_success)
        if mp == 'ebay_au':
            from scrapers import ebay_au_proxies as p

            return (p.proxies_configured, lambda rotate: p.acquire_proxy(self._pool_state, force_rotate=rotate),
                    p.mark_proxy_blocked, p.mark_proxy_success)
        if mp == 'ebay_us':
            from scrapers import ebay_us_proxies as p

            return (p.proxies_configured, lambda rotate: p.acquire_proxy(self._pool_state, force_rotate=rotate),
                    p.mark_proxy_blocked, p.mark_proxy_success)
        return None

    def configured(self) -> bool:
        pool = self._pool()
        if pool is None or not crawl_proxies_enabled():
            return False
        try:
            return bool(pool[0]())
        except Exception:
            logger.exception('discovery crawl proxies unavailable for %s', self.marketplace)
            return False

    def attach(self, session, *, rotate: bool = False) -> bool:
        """Put the next proxy on ``session``. False when no proxy is free (the session keeps its last one)."""
        pool = self._pool()
        if pool is None:
            return False
        try:
            assignment = pool[1](rotate)
        except Exception:
            logger.exception('discovery crawl proxy not acquired for %s', self.marketplace)
            return False
        if assignment is None:
            return False
        self.assignment = assignment
        session.proxies = assignment.as_requests_proxy()
        logger.info('discovery %s crawl via proxy %s', self.marketplace, assignment.label)
        return True

    def blocked(self) -> None:
        pool = self._pool()
        if pool is not None and self.assignment is not None:
            try:
                pool[2](self.assignment)
            except Exception:
                logger.exception('discovery crawl proxy not rested for %s', self.marketplace)

    def worked(self) -> None:
        pool = self._pool()
        if pool is not None and self.assignment is not None:
            try:
                pool[3](self.assignment)
            except Exception:
                pass


def use_crawl_proxy(session, marketplace: str | None) -> None:
    """Give a new crawl session its marketplace's proxy, when the server has that pool."""
    if session is None or not marketplace:
        return
    proxy = CrawlProxy(marketplace)
    if not proxy.configured():
        return
    if proxy.attach(session):
        session._crawl_proxy = proxy
