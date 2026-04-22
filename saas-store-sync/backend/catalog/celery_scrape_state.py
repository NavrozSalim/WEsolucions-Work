"""Track in-flight Celery catalog scrapes (Amazon/eBay server-side) for the UI.

Desktop vendors (HEB, Costco) use ``HebScrapeJob``. Server-side store/upload
scrapes use Celery task IDs; this table marks a store while a chord or
single-task scrape is still running so ``/catalog/scrape/progress/`` can show
\"in queue / running\" like desktop queue strips.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from catalog.models import CatalogUpload
    from stores.models import Store


def set_celery_scrape_state(
    store: Store,
    *,
    task_id: str,
    scope: str,
    upload: CatalogUpload | None = None,
) -> None:
    from catalog.models import StoreCatalogCeleryScrapeState

    StoreCatalogCeleryScrapeState.objects.update_or_create(
        store=store,
        defaults={
            'scope': scope,
            'upload': upload,
            'root_task_id': (task_id or '')[:255],
        },
    )


def clear_celery_scrape_state(store_id: str | None) -> None:
    if not store_id:
        return
    from catalog.models import StoreCatalogCeleryScrapeState

    StoreCatalogCeleryScrapeState.objects.filter(store_id=store_id).delete()
