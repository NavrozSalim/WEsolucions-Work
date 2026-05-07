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
            'cancel_requested': False,
            'first_worker_started_at': None,
        },
    )


def mark_celery_scrape_worker_started(store_id: str | None) -> None:
    """First chunk/sequential pass to touch rows sets this; until then UI shows queued."""
    if not store_id:
        return
    from django.utils import timezone

    from catalog.models import StoreCatalogCeleryScrapeState

    StoreCatalogCeleryScrapeState.objects.filter(
        store_id=store_id,
        first_worker_started_at__isnull=True,
    ).update(first_worker_started_at=timezone.now())


def should_abort_celery_scrape(store_id: str | None) -> bool:
    """True when workers must stop: scrape state was cleared (Stop) or cancel flag set.

    Clearing ``StoreCatalogCeleryScrapeState`` immediately on cancel makes
    ``/scrape/progress/`` drop the queued/running banner; workers cooperatively
    exit on the next loop iteration when the row is missing.
    """
    if not store_id:
        return False
    from catalog.models import StoreCatalogCeleryScrapeState

    try:
        st = StoreCatalogCeleryScrapeState.objects.get(store_id=store_id)
    except StoreCatalogCeleryScrapeState.DoesNotExist:
        return True
    return bool(st.cancel_requested)


def clear_celery_scrape_state(store_id: str | None) -> None:
    if not store_id:
        return
    from catalog.models import StoreCatalogCeleryScrapeState

    StoreCatalogCeleryScrapeState.objects.filter(store_id=store_id).delete()
