"""Track in-flight Celery catalog scrapes (Amazon/eBay server-side) for the UI.

Desktop vendors (HEB, Costco) use ``HebScrapeJob``. Server-side store/upload
scrapes use Celery task IDs; this table marks a store while a chord or
single-task scrape is still running so ``/scrape/progress/`` can show
\"in queue / running\" like desktop queue strips.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from catalog.models import CatalogUpload
    from stores.models import Store


def _stop_stale_timedelta() -> timedelta:
    from django.conf import settings

    try:
        m = int(getattr(settings, 'CATALOG_SCRAPE_STOP_STALE_MINUTES', 20) or 20)
    except (TypeError, ValueError):
        m = 20
    m = max(5, min(120, m))
    return timedelta(minutes=m)


def _stop_idle_timedelta() -> timedelta:
    """How long a stopped scrape can sit without writing a listing before the UI unlocks.

    One Costco page can retry for about a minute. After this quiet period the
    current page has either finished or the worker is gone, so Start Scraping
    should come back instead of waiting out the 20-minute stale cap.
    """
    from django.conf import settings

    try:
        seconds = int(getattr(settings, 'CATALOG_SCRAPE_STOP_IDLE_SECONDS', 120) or 120)
    except (TypeError, ValueError):
        seconds = 120
    seconds = max(30, min(600, seconds))
    return timedelta(seconds=seconds)


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
            'cancel_requested_at': None,
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
    st.cancel_requested_at = None
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
            'cancel_requested_at',
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


def current_celery_scrape_root_task_id(store_id: str | None) -> str:
    """Root task id observed at the start of a worker loop, or '' when none."""
    if not store_id:
        return ''
    from catalog.models import StoreCatalogCeleryScrapeState

    tid = (
        StoreCatalogCeleryScrapeState.objects.filter(store_id=store_id)
        .values_list('root_task_id', flat=True)
        .first()
    )
    return tid or ''


def should_abort_celery_scrape(
    store_id: str | None,
    *,
    root_task_id: str | None = None,
) -> bool:
    """True when workers must stop after the current vendor URL.

    Stop sets ``cancel_requested`` and keeps this row so the UI can show
    ``phase=stopping`` until chunks drain. A missing row still means abort
    (job finished, crashed, or an older Stop path that deleted state) so a
    leftover worker does not keep going after finalize.

    ``root_task_id`` is the id the loop saw when it started. A newer scrape
    replaces that id; the old loop must exit instead of joining the new run.

    A dropped database connection is not a cancel. Costco requests sit idle
    on the DB for minutes; treating that blip as Stop left the rest of the
    Pending list behind.
    """
    if not store_id:
        return False
    from django.db import InterfaceError, OperationalError, close_old_connections

    from catalog.models import StoreCatalogCeleryScrapeState

    close_old_connections()
    for attempt in (1, 2):
        try:
            st = StoreCatalogCeleryScrapeState.objects.get(store_id=store_id)
        except StoreCatalogCeleryScrapeState.DoesNotExist:
            return True
        except (OperationalError, InterfaceError):
            close_old_connections()
            if attempt == 1:
                continue
            logger.exception(
                'Scrape cancel check failed for store %s; continuing so a dropped '
                'database connection does not abandon Pending listings.',
                store_id,
            )
            return False
        if root_task_id is not None and (st.root_task_id or '') != root_task_id:
            return True
        return bool(st.cancel_requested)
    return False


def request_celery_scrape_cancel(store_id: str | None) -> bool:
    """Ask in-flight catalog scrape workers to stop after the current URL.

    Keeps the state row so ``/scrape/progress/`` stays active with
    ``phase=stopping`` until finalize or the quiet-period heal clears it.
    Returns True when this store is stopping. A repeat call does not move
    ``cancel_requested_at``.
    """
    if not store_id:
        return False
    from django.utils import timezone

    from catalog.models import StoreCatalogCeleryScrapeState
    from catalog.scrape_progress import invalidate_scrape_progress_cache

    # A second Stop must not move cancel_requested_at. That timestamp is the
    # clock for releasing a stuck "stopping" banner, and the button stays
    # visible while the row does.
    now = timezone.now()
    updated = StoreCatalogCeleryScrapeState.objects.filter(
        store_id=store_id,
        cancel_requested=False,
    ).update(
        cancel_requested=True,
        cancel_requested_at=now,
    )
    if updated:
        invalidate_scrape_progress_cache(str(store_id))
        return True
    return StoreCatalogCeleryScrapeState.objects.filter(
        store_id=store_id,
        cancel_requested=True,
    ).exists()


def _scrape_wrote_since(store_id: str, since) -> bool:
    """True when a listing on this store was scraped inside the quiet window."""
    from catalog.models import ProductMapping

    return ProductMapping.objects.filter(
        store_id=store_id,
        last_scrape_time__gte=since,
    ).exists()


def _note_stop_cleared(store_id: str) -> None:
    try:
        from catalog.activity_log import append_catalog_log

        append_catalog_log(
            store_id,
            'Vendor scrape stop finished. Start Scraping is available again. '
            'Remaining listings stay Pending.',
            action_type='scrape_stop_cleared',
        )
    except Exception:
        logger.exception('Could not log cleared scrape stop for store %s', store_id)


def heal_stale_celery_scrape_state(store_id: str | None) -> bool:
    """Clear leftover scrape-state rows that would block Start Scraping.

    - Stop clicked and the workers have gone quiet (no listing scraped inside
      ``CATALOG_SCRAPE_STOP_IDLE_SECONDS``, default 120). The current page is
      done or the worker died without finalize, so the stopping banner should
      drop and Start Scraping should come back.
    - Stop clicked but a worker is still writing: keep the row until
      ``CATALOG_SCRAPE_STOP_STALE_MINUTES`` (default 20). Legacy rows with no
      ``cancel_requested_at`` use ``enqueued_at``.
    - Queued and never started for 30+ minutes: same recovery as before.

    Returns True when a row was deleted.
    """
    if not store_id:
        return False
    from django.utils import timezone

    from catalog.models import StoreCatalogCeleryScrapeState

    try:
        st = StoreCatalogCeleryScrapeState.objects.get(store_id=store_id)
    except StoreCatalogCeleryScrapeState.DoesNotExist:
        return False

    now = timezone.now()
    if st.cancel_requested:
        marked_at = st.cancel_requested_at or st.enqueued_at
        if not marked_at:
            return False
        age = now - marked_at
        if age >= _stop_stale_timedelta():
            clear_celery_scrape_state(str(store_id))
            _note_stop_cleared(str(store_id))
            return True
        if age >= _stop_idle_timedelta() and not _scrape_wrote_since(
            str(store_id), now - _stop_idle_timedelta(),
        ):
            clear_celery_scrape_state(str(store_id))
            _note_stop_cleared(str(store_id))
            return True
        return False
    if st.first_worker_started_at is None and (now - st.enqueued_at) > timedelta(minutes=30):
        clear_celery_scrape_state(str(store_id))
        return True
    return False


def clear_celery_scrape_state(store_id: str | None) -> None:
    if not store_id:
        return
    from catalog.models import StoreCatalogCeleryScrapeState

    StoreCatalogCeleryScrapeState.objects.filter(store_id=store_id).delete()
    from catalog.scrape_progress import invalidate_scrape_progress_cache

    invalidate_scrape_progress_cache(str(store_id))
