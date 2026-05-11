"""
Dynamic Celery routes for catalog browser scrapes.

``Store.region`` is ``USA`` or ``AU`` (see ``stores.models.Store``). All
server-side Amazon/eBay scrape tasks are routed to:

- ``heavy-us`` — US marketplace scrapers (Amazon US, eBay US).
- ``heavy-au`` — AU marketplace scrapers (Amazon AU, eBay AU) plus ``catalog.run_vevor_au_ingest``.

Chord finalizers (aggregate chunk results, update ``ScrapeRun``, activity log)
run on ``light`` so the main app worker can finish jobs without requiring
the US worker to subscribe to non-scrape queues.

Deploy:

- Main server worker: ``-Q celery,ingest,light`` (finalizers and non-AU-heavy tasks)
- US worker: ``-Q heavy-us`` (same ``REDIS_URL`` / ``DATABASE_URL`` as main)
- AU worker: ``-Q heavy-au`` (Amazon AU, eBay AU, ``catalog.run_vevor_au_ingest``)
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


def heavy_queue_for_region(region: str | None) -> str:
    """Map ``Store.region`` to the browser-heavy queue name."""
    if region and str(region).upper() == "AU":
        return QUEUE_HEAVY_AU
    return QUEUE_HEAVY_US


class CatalogScrapeTaskRouter:
    """Routes catalog scrape tasks from ``Store.region``; finalizers → ``light``."""

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
