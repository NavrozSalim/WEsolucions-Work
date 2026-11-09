"""Decide where Costco AU listings are scraped: the AU server or the desktop runner.

Costco AU is scraped on the AU worker when that worker has residential proxies
(``COSTCO_AU_PROXY_URLS``). The proxies are configured on the AU VPS only, but
the routing decision is also made on the main server (Start Scraping, scheduled
updates, the progress strip, the scrape watchdog). Reading the local env there
said "no proxies", so Costco was queued for a desktop runner that never claimed
it and the server scrape skipped every Costco row.

The AU worker now publishes its proxy count to the shared Redis cache when it
starts, and every process reads that one value.
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

COSTCO_AU_SERVER_CACHE_KEY = 'scrapers:costco_au:server_proxy_count'
# The worker queue that runs Costco AU browser scrapes.
COSTCO_AU_WORKER_QUEUE = 'heavy-au'
# Rows loops ask once per listing; reread the shared flag at most this often.
_MEMO_SECONDS = 60.0
_memo: dict = {'at': None, 'value': False}


def _local_proxy_count() -> int:
    try:
        from scrapers.costco_au_proxies import load_proxy_urls

        return len(load_proxy_urls())
    except Exception:
        logger.exception('Could not read Costco AU proxy settings')
        return 0


def _published_by_au_worker() -> bool:
    now = time.monotonic()
    if _memo['at'] is not None and now - _memo['at'] < _MEMO_SECONDS:
        return bool(_memo['value'])
    try:
        from django.core.cache import cache

        value = bool(cache.get(COSTCO_AU_SERVER_CACHE_KEY))
    except Exception:
        logger.warning('Could not read the Costco AU worker flag from the cache', exc_info=True)
        value = False
    _memo['at'] = now
    _memo['value'] = value
    return value


def reset_costco_au_mode_memo() -> None:
    _memo['at'] = None
    _memo['value'] = False


def costco_au_runs_on_server() -> bool:
    """Routing: True when Costco AU listings go through the server scrape (not the desktop runner)."""
    if _local_proxy_count():
        return True
    return _published_by_au_worker()


def costco_au_scrapes_in_this_process() -> bool:
    """True when this process has its own Costco AU proxies and may scrape Costco itself.

    More than one worker can consume ``heavy-au``. One without proxies must
    leave Costco rows Pending: from a datacenter IP every row would come back
    blocked and be marked Failed with stock 0.
    """
    return _local_proxy_count() > 0


def publish_costco_au_worker_mode(queue_names) -> int | None:
    """Called when a Celery worker starts. Only a ``heavy-au`` worker with proxies publishes.

    A ``heavy-au`` worker without proxies leaves the flag alone: another AU
    worker may have the proxies, and clearing it would send Costco back to the
    desktop runner for everyone. Clear the key by hand if Costco proxies are
    removed for good.

    Returns the proxy count found, or ``None`` when this worker does not
    consume ``heavy-au``.
    """
    names = {str(q) for q in (queue_names or ())}
    if COSTCO_AU_WORKER_QUEUE not in names:
        return None
    count = _local_proxy_count()
    if count:
        from django.core.cache import cache

        cache.set(COSTCO_AU_SERVER_CACHE_KEY, count, timeout=None)
        reset_costco_au_mode_memo()
        logger.info(
            'Costco AU scrape mode published by %s worker: server (%s proxies)',
            COSTCO_AU_WORKER_QUEUE, count,
        )
    else:
        logger.warning(
            'This %s worker has no Costco AU proxies (COSTCO_AU_PROXY_URLS). It will '
            'leave Costco listings Pending; any Costco scrape it picks up does nothing.',
            COSTCO_AU_WORKER_QUEUE,
        )
    return count
