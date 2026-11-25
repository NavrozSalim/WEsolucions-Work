"""Track in-flight Celery catalog scrapes (Amazon/eBay server-side) for the UI.

Desktop vendors (HEB, Costco) use ``HebScrapeJob``. Server-side store/upload
scrapes use Celery task IDs; a row marks one running scrape so
``/scrape/progress/`` can show \"in queue / running\" like desktop queue strips.

A store can run one scrape per vendor at the same time. A row's
``scrape_vendor_id`` is that vendor's id, or ``''`` for a scrape of every vendor
(and for upload-scoped scrapes). An every-vendor run excludes any other run on
the store. A run is identified by its ``root_task_id``: its batches, lanes and
finalize step carry it, so each touches only its own row.

``vendor_id`` arguments: ``None`` means "not given"; ``''`` (or None where noted)
is the every-vendor run.
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

    Scrapers check for Stop between retries, so a lane leaves its listing within
    seconds of Stop. After this quiet period the current page has either finished
    or the worker is gone, so Start Scraping should come back instead of waiting
    out the 20-minute stale cap.
    """
    from django.conf import settings

    try:
        seconds = int(getattr(settings, 'CATALOG_SCRAPE_STOP_IDLE_SECONDS', 45) or 45)
    except (TypeError, ValueError):
        seconds = 45
    seconds = max(30, min(600, seconds))
    return timedelta(seconds=seconds)


def vendor_key(vendor_id) -> str:
    """The row key for a vendor: its id as text, or '' for every vendor."""
    return (str(vendor_id).strip()[:64] if vendor_id else '')


def _runs(store_id):
    from catalog.models import StoreCatalogCeleryScrapeState

    return StoreCatalogCeleryScrapeState.objects.filter(store_id=store_id)


def _invalidate(store_id) -> None:
    from catalog.scrape_progress import invalidate_scrape_progress_cache

    invalidate_scrape_progress_cache(str(store_id))


def scrape_conflict(store_id, vendor_id=None):
    """The running scrape that blocks starting one for ``vendor_id`` ('' / None = every vendor), or None.

    An every-vendor scrape needs the store to itself; a vendor scrape is blocked
    by the same vendor's run or by an every-vendor run.
    """
    if not store_id:
        return None
    key = vendor_key(vendor_id)
    runs = _runs(store_id)
    if key:
        runs = runs.filter(scrape_vendor_id__in=[key, ''])
    return runs.order_by('enqueued_at').first()


def set_celery_scrape_state(
    store: Store,
    *,
    task_id: str,
    scope: str,
    upload: CatalogUpload | None = None,
    vendor_id: str | None = None,
) -> None:
    from catalog.models import StoreCatalogCeleryScrapeState

    tid = (task_id or '')[:255]
    key = vendor_key(vendor_id)
    st, created = StoreCatalogCeleryScrapeState.objects.get_or_create(
        store=store,
        scrape_vendor_id=key,
        defaults={
            'scope': scope,
            'upload': upload,
            'root_task_id': tid,
            'cancel_requested': False,
            'cancel_requested_at': None,
            'first_worker_started_at': None,
            'last_heartbeat_at': None,
        },
    )
    if created:
        _invalidate(store.id)
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
        st.last_heartbeat_at = None
    st.save(
        update_fields=[
            'scope',
            'upload',
            'root_task_id',
            'cancel_requested',
            'cancel_requested_at',
            'first_worker_started_at',
            'last_heartbeat_at',
        ]
    )
    _invalidate(store.id)


def mark_celery_scrape_worker_started(store_id: str | None, root_task_id: str | None = None) -> None:
    """Set first_worker_started_at so /scrape/progress/ shows running (vs queued).

    ``root_task_id`` limits it to that run; without it every queued run of the
    store is marked. Safe to call multiple times.
    """
    if not store_id:
        return
    from django.utils import timezone

    runs = _runs(store_id).filter(first_worker_started_at__isnull=True)
    if root_task_id:
        runs = runs.filter(root_task_id=root_task_id)
    runs.update(first_worker_started_at=timezone.now())


def touch_celery_scrape_heartbeat(store_id: str | None, root_task_id: str | None) -> None:
    """Record that a worker of this run is still moving through listings.

    Only the run that owns the row (same ``root_task_id``) may refresh it, so a
    leftover worker from a replaced run cannot hide a stalled scrape from the
    watchdog. Errors are logged and ignored: a missed heartbeat must never stop
    the scrape itself.
    """
    if not store_id or not root_task_id:
        return
    from django.utils import timezone

    try:
        _runs(store_id).filter(root_task_id=root_task_id).update(last_heartbeat_at=timezone.now())
    except Exception:
        logger.exception('Could not record scrape heartbeat for store %s', store_id)


def current_celery_scrape_root_task_id(store_id: str | None, vendor_id=None) -> str:
    """Run id of the store's scrape for ``vendor_id`` ('' / None = every vendor), or '' when none."""
    if not store_id:
        return ''
    tid = (
        _runs(store_id).filter(scrape_vendor_id=vendor_key(vendor_id))
        .values_list('root_task_id', flat=True)
        .first()
    )
    return tid or ''


def scrape_run_exists(store_id, root_task_id: str | None) -> bool:
    """True while the run ``root_task_id`` still owns a row (not finished, cleared or replaced)."""
    if not store_id or not root_task_id:
        return False
    return _runs(store_id).filter(root_task_id=root_task_id).exists()


def should_abort_celery_scrape(
    store_id: str | None,
    *,
    root_task_id: str | None = None,
    vendor_id=None,
) -> bool:
    """True when workers must stop after the current vendor URL.

    ``root_task_id`` names the run (its row); without it ``vendor_id`` picks the
    row ('' / None = the every-vendor run).

    Stop sets ``cancel_requested`` and keeps the row so the UI can show
    ``phase=stopping`` until chunks drain. A missing row still means abort
    (job finished, crashed, replaced by a newer run, or an older Stop path that
    deleted state) so a leftover worker does not keep going after finalize.

    A dropped database connection is not a cancel. Costco requests sit idle
    on the DB for minutes; treating that blip as Stop left the rest of the
    Pending list behind.
    """
    if not store_id:
        return False
    from django.db import InterfaceError, OperationalError, close_old_connections

    close_old_connections()
    for attempt in (1, 2):
        try:
            runs = _runs(store_id)
            if root_task_id is not None:
                runs = runs.filter(root_task_id=root_task_id)
            else:
                runs = runs.filter(scrape_vendor_id=vendor_key(vendor_id))
            cancel = runs.values_list('cancel_requested', flat=True).first()
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
        if cancel is None:
            return True
        return bool(cancel)
    return False


def _pick_runs(store_id, *, vendor_id=None, root_task_id=None):
    runs = _runs(store_id)
    if root_task_id:
        return runs.filter(root_task_id=root_task_id)
    if vendor_id is not None:
        return runs.filter(scrape_vendor_id=vendor_key(vendor_id))
    return runs


def request_celery_scrape_cancel(store_id: str | None, *, vendor_id=None, root_task_id=None) -> bool:
    """Ask in-flight catalog scrape workers to stop after the current URL.

    ``vendor_id`` stops that vendor's run ('' = the every-vendor run); None stops
    every run of the store. Keeps the rows so ``/scrape/progress/`` shows
    ``phase=stopping`` until finalize or the quiet-period heal clears them.
    Returns True when a chosen run is stopping. A repeat call does not move
    ``cancel_requested_at``.
    """
    if not store_id:
        return False
    from django.utils import timezone

    # A second Stop must not move cancel_requested_at. That timestamp is the
    # clock for releasing a stuck "stopping" banner, and the button stays
    # visible while the row does.
    chosen = _pick_runs(store_id, vendor_id=vendor_id, root_task_id=root_task_id)
    updated = chosen.filter(cancel_requested=False).update(
        cancel_requested=True,
        cancel_requested_at=timezone.now(),
    )
    if updated:
        _invalidate(store_id)
        return True
    return chosen.filter(cancel_requested=True).exists()


def _scrape_wrote_since(store_id: str, since, vendor: str = '') -> bool:
    """True when a listing of this run (the vendor's, or any for '') was scraped inside the quiet window.

    Only the stopped run's own vendor counts: another vendor's scrape running at
    the same time must not keep this one stuck on "stopping".
    """
    from catalog.models import ProductMapping

    rows = ProductMapping.objects.filter(store_id=store_id, last_scrape_time__gte=since)
    if vendor:
        rows = rows.filter(product__vendor_id=vendor)
    return rows.exists()


def _note_stop_cleared(store_id: str, vendor: str = '') -> None:
    try:
        from catalog.activity_log import append_catalog_log

        who = 'Vendor scrape'
        if vendor:
            from vendor.models import Vendor

            name = Vendor.objects.filter(pk=vendor).values_list('name', flat=True).first()
            if name:
                who = f'{name} scrape'
        append_catalog_log(
            store_id,
            f'{who} stop finished. Start Scraping is available again. '
            'Remaining listings stay Pending.',
            action_type='scrape_stop_cleared',
            metadata={'vendor_id': vendor or None},
        )
    except Exception:
        logger.exception('Could not log cleared scrape stop for store %s', store_id)


def _heal_run(st, now) -> bool:
    store_id = str(st.store_id)
    vendor = st.scrape_vendor_id or ''
    if st.cancel_requested:
        marked_at = st.cancel_requested_at or st.enqueued_at
        if not marked_at:
            return False
        age = now - marked_at
        quiet = age >= _stop_idle_timedelta() and not _scrape_wrote_since(
            store_id, now - _stop_idle_timedelta(), vendor,
        )
        if age >= _stop_stale_timedelta() or quiet:
            clear_celery_scrape_state(store_id, root_task_id=st.root_task_id or None, vendor_id=vendor)
            _note_stop_cleared(store_id, vendor)
            return True
        return False
    if st.first_worker_started_at is None and (now - st.enqueued_at) > timedelta(minutes=30):
        clear_celery_scrape_state(store_id, root_task_id=st.root_task_id or None, vendor_id=vendor)
        return True
    return False


def heal_stale_celery_scrape_state(store_id: str | None) -> bool:
    """Clear leftover scrape rows of the store that would block Start Scraping.

    For each running scrape of the store:

    - Stop clicked and its workers have gone quiet (none of its vendor's listings
      scraped inside ``CATALOG_SCRAPE_STOP_IDLE_SECONDS``). The current page is
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

    now = timezone.now()
    healed = False
    for st in list(_runs(store_id)):
        if _heal_run(st, now):
            healed = True
    return healed


def clear_celery_scrape_state(store_id: str | None, *, root_task_id=None, vendor_id=None) -> None:
    """Remove a finished scrape's row.

    ``root_task_id`` removes that run only; else ``vendor_id`` removes that
    vendor's run ('' = the every-vendor run). With neither, every run of the
    store is removed: only for whole-store resets, never at the end of one run.
    """
    if not store_id:
        return
    _pick_runs(store_id, vendor_id=vendor_id, root_task_id=root_task_id).delete()
    _invalidate(store_id)
