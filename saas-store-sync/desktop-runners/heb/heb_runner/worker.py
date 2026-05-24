"""Subprocess worker: scrape one URL chunk in its own Chrome session."""

from __future__ import annotations

import time
from typing import Any

from .config import MIN_GAP_SEC
from .scraper import HebBrowserSession


def scrape_chunk(urls: list[str]) -> list[dict[str, Any]]:
    """Scrape ``urls`` sequentially in a fresh browser. Called from worker processes."""
    if not urls:
        return []

    session = HebBrowserSession()
    items: list[dict[str, Any]] = []
    try:
        session.start()
        for i, url in enumerate(urls, start=1):
            result = session.scrape(url)
            items.append(result)
            if i < len(urls) and MIN_GAP_SEC > 0:
                time.sleep(MIN_GAP_SEC)
    finally:
        session.close()
    return items
