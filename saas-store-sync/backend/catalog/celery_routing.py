"""
Dynamic Celery routes for catalog browser scrapes.

``Store.region`` is ``USA`` or ``AU`` (see ``stores.models.Store``). All
server-side Amazon/eBay scrape tasks are routed to:

- ``heavy-us`` — US marketplace scrapers (Amazon US, eBay US, HEB US).
- ``heavy-au`` — AU marketplace scrapers (Amazon AU, eBay AU).

Chord finalizers (aggregate chunk results, update ``ScrapeRun``, activity log)
run on ``light`` so the main app worker can finish jobs without requiring
the US worker to subscribe to non-scrape queues.

``catalog.run_vevor_au_ingest`` also runs on ``light`` (XLSX feed, no browser).

Managed Inventory Start Scraping (``listings.scrape_store_listings``) uses the
same ``heavy-us`` / ``heavy-au`` split so Lasoo eBay AU links run on the AU
scraper VPS, not a Gunicorn thread on the main app.

Deploy:

- Main server workers: ``-Q celery`` | ``-Q ingest`` | ``-Q sync`` | ``-Q light`` — ingest for file/sync; sync for ``run_store_*``; light for scrape finalizers + Beat tick.
- US worker: ``-Q heavy-us`` (same ``REDIS_URL`` / ``DATABASE_URL`` as main)
- AU worker: ``-Q heavy-au`` (Amazon AU, eBay AU)
- Single-host dev: listen to ``heavy-us`` and ``heavy-au`` together if needed.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

QUEUE_HEAVY_US = "heavy-us"
QUEUE_HEAVY_AU = "heavy-au"
QUEUE_SCRAPE_FINALIZE = "light"

# Full task names as registered with Celery (module path unless overridden).
_SCRAPE_HEAD_OR_CHUNK = frozenset(
    {
        "catalog.tasks.catalog_scrape_task",
        "catalog.tasks.catalog_scrape_store_task",
        "catalog.tasks.catalog_scrape_upload_chunk_task",
        "catalog.tasks.catalog_scrape_store_chunk_task",
    }
)
_FINALIZE = frozenset(
    {
        "catalog.tasks.catalog_scrape_upload_finalize",
        "catalog.tasks.catalog_scrape_store_finalize",
    }
)
_LISTING_SCRAPE = "listings.scrape_store_listings"


def heavy_queue_for_region(region: str | None) -> str:
    """Map ``Store.region`` to the browser-heavy queue name."""
    if region and str(region).upper() == "AU":
        return QUEUE_HEAVY_AU
    return QUEUE_HEAVY_US


class CatalogScrapeTaskRouter:
    """Routes catalog + managed-listing browser scrapes from ``Store.region``.

    Finalizers → ``light``. ``listings.scrape_store_listings`` → ``heavy-au`` / ``heavy-us``.
    """

    def route_for_task(
        self,
        name: str,
        args: tuple[Any, ...] | None = None,
        kwargs: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
        *,
        task=None,
        **kw: Any,
    ) -> dict[str, str] | None:
        args = args or ()
        kwargs = kwargs or {}
        if name in _FINALIZE:
            return {"queue": QUEUE_SCRAPE_FINALIZE}

        if name == _LISTING_SCRAPE:
            store_id = None
            if len(args) > 1:
                store_id = args[1]
            else:
                store_id = kwargs.get("store_id")
            if not store_id:
                logger.warning("listing scrape route: missing store_id for %s", name)
                return {"queue": QUEUE_HEAVY_US}
            try:
                from stores.models import Store

                region = Store.objects.values_list("region", flat=True).get(id=store_id)
            except Exception as exc:
                logger.exception(
                    "listing scrape route: could not resolve region for %s; defaulting to %s: %s",
                    name,
                    QUEUE_HEAVY_US,
                    exc,
                )
                return {"queue": QUEUE_HEAVY_US}
            return {"queue": heavy_queue_for_region(region)}

        if name not in _SCRAPE_HEAD_OR_CHUNK:
            return None

        try:
            if name in (
                "catalog.tasks.catalog_scrape_task",
                "catalog.tasks.catalog_scrape_upload_chunk_task",
            ):
                upload_id = self._first_positional(args, kwargs, ("upload_id",))
                if not upload_id:
                    logger.warning("catalog scrape route: missing upload_id for %s", name)
                    return {"queue": QUEUE_HEAVY_US}
                from catalog.models import CatalogUpload

                region = CatalogUpload.objects.values_list("store__region", flat=True).get(
                    id=upload_id
                )
            elif name in (
                "catalog.tasks.catalog_scrape_store_task",
                "catalog.tasks.catalog_scrape_store_chunk_task",
            ):
                store_id = self._first_positional(args, kwargs, ("store_id",))
                if not store_id:
                    logger.warning("catalog scrape route: missing store_id for %s", name)
                    return {"queue": QUEUE_HEAVY_US}
                from stores.models import Store

                region = Store.objects.values_list("region", flat=True).get(id=store_id)
            else:
                return None
        except Exception as exc:
            logger.exception(
                "catalog scrape route: could not resolve region for %s; defaulting to %s: %s",
                name,
                QUEUE_HEAVY_US,
                exc,
            )
            return {"queue": QUEUE_HEAVY_US}

        return {"queue": heavy_queue_for_region(region)}

    @staticmethod
    def _first_positional(
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        kw_names: tuple[str, ...],
    ) -> Any:
        if args:
            return args[0]
        for key in kw_names:
            if key in kwargs and kwargs[key] is not None:
                return kwargs[key]
        return None
