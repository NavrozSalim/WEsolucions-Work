"""Let Stop Scraping interrupt a listing between its retries.

The store scrape loop checks for Stop before each listing, but one listing can
retry for minutes (eBay US tries Chrome up to 5 times on fresh proxies, Amazon and
Costco retry page loads). Without a check inside those retries, Stop waited for
every lane to finish its current listing.

The loop wraps each ``get_price_and_stock`` call in ``scrape_stop_check(fn)``.
Scrapers call ``raise_if_scrape_stopped()`` before a retry. When Stop was clicked,
that raises ``ScrapeStopped`` and the loop leaves the listing untouched (still
Pending), the same as a Stop seen between listings.

Outside a store scrape (manual tests, single-listing scrapes) no check is set and
``raise_if_scrape_stopped()`` does nothing.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Callable

# Retries come seconds apart; the check reads the database, so it runs at most
# this often per listing.
_MIN_CHECK_GAP_SEC = 2.0

_local = threading.local()


class ScrapeStopped(BaseException):
    """Stop Scraping was clicked while a listing was still retrying.

    A BaseException, not an Exception: scrapers catch ``Exception`` around each
    attempt, and the store loop records an ``Exception`` as a failed listing.
    Neither may swallow a Stop.
    """


@contextmanager
def scrape_stop_check(check: Callable[[], bool] | None):
    """Make ``check()`` (True = Stop was clicked) visible to the scrapers in this block."""
    prev = getattr(_local, 'state', None)
    _local.state = {'check': check, 'last': 0.0, 'stopped': False} if check else None
    try:
        yield
    finally:
        _local.state = prev


def raise_if_scrape_stopped() -> None:
    """Raise ``ScrapeStopped`` when Stop Scraping was clicked for the running scrape."""
    state = getattr(_local, 'state', None)
    if not state:
        return
    if state['stopped']:
        raise ScrapeStopped()
    now = time.monotonic()
    if now - state['last'] < _MIN_CHECK_GAP_SEC:
        return
    state['last'] = now
    try:
        hit = bool(state['check']())
    except Exception:  # noqa: BLE001 - a failed check must not end the listing
        return
    if hit:
        state['stopped'] = True
        raise ScrapeStopped()
