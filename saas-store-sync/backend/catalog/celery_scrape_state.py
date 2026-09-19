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

    tid = (task_id or '')[:255]
    st, created = StoreCatalogCeleryScrapeState.objects.get_or_create(
        store=store,
        defaults={
            'scope': scope,
            'upload': upload,
            'root_task_id': tid,
            'cancel_requested': False,
            'first_worker_started_at': None,
        },
    )
    if created:
        from catalog.scrape_progress import invalidate_scrape_progress_cache

        invalidate_scrape_progress_cache(str(store.id))
        return
    prev_tid = (st.root_task_id or '')[:255]
    st.scope = scope
    st.upload = upload
    st.root_task_id = tid
    st.cancel_requested = False
    # update_or_create always reapplied None here, so a transient duplicate POST or
    # client retry cleared first_worker_started_at and the UI stuck on "queued".
    if prev_tid != tid:
        st.first_worker_started_at = None
    st.save(
        update_fields=[
            'scope',
            'upload',
            'root_task_id',
            'cancel_requested',
            'first_worker_started_at',
        ]
    )
    from catalog.scrape_progress import invalidate_scrape_progress_cache

    invalidate_scrape_progress_cache(str(store.id))


def mark_celery_scrape_worker_started(store_id: str | None) -> None:
    """Set first_worker_started_at so /scrape/progress/ shows running (vs queued).

    Called from the Catalog scrape API immediately after persisting scrape state, and
    from Celery workers once they begin processing — safe to call multiple times.
    """
    if not store_id:
        return
    from django.utils import timezone

    from catalog.models import StoreCatalogCeleryScrapeState

    StoreCatalogCeleryScrapeState.objects.filter(
        store_id=store_id,
        first_worker_started_at__isnull=True,
    ).update(first_worker_started_at=timezone.now())


def should_abort_celery_scrape(store_id: str | None) -> bool:
    """True when workers must stop after the current vendor URL.

    Stop sets ``cancel_requested`` and keeps this row so the UI can show
    ``phase=stopping`` until chunks drain. A missing row still means abort
    (job finished, crashed, or an older Stop path that deleted state) so a
    leftover worker does not keep going after finalize.
    """
    if not store_id:
        return False
    from catalog.models import StoreCatalogCeleryScrapeState

    try:
        st = StoreCatalogCeleryScrapeState.objects.get(store_id=store_id)
    except StoreCatalogCeleryScrapeState.DoesNotExist:
        return True
    return bool(st.cancel_requested)


def request_celery_scrape_cancel(store_id: str | None) -> bool:
    """Ask in-flight catalog scrape workers to stop after the current URL.

    Keeps the state row so ``/scrape/progress/`` stays active with
    ``phase=stopping`` until finalize clears it. Returns True when a row was updated.
    """
    if not store_id:
        return False
    from catalog.models import StoreCatalogCeleryScrapeState
    from catalog.scrape_progress import invalidate_scrape_progress_cache

    updated = StoreCatalogCeleryScrapeState.objects.filter(store_id=store_id).update(
        cancel_requested=True,
    )
    if updated:
        invalidate_scrape_progress_cache(str(store_id))
    return bool(updated)


def clear_celery_scrape_state(store_id: str | None) -> None:
    if not store_id:
        return
    from catalog.models import StoreCatalogCeleryScrapeState

    StoreCatalogCeleryScrapeState.objects.filter(store_id=store_id).delete()
    from catalog.scrape_progress import invalidate_scrape_progress_cache

    invalidate_scrape_progress_cache(str(store_id))
