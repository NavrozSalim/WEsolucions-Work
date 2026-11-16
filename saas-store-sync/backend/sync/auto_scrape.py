"""
Auto Scraping: scrape a store at the user's local run times, then retry Failed rows.

One run:
  1. All active listings to Pending, ingest vendors refreshed, live browser scrape (wait).
  2. Up to ``retry_failed_cycles`` times: if any active listing is Failed, reset only the
     Failed ones to Pending and scrape again. Stops early at 0 Failed, on Stop scraping,
     or when a scrape is still running at the wait limit.

Auto Scraping never sends anything to the marketplace. While a run is active,
``auto_scrape_run_active`` makes the scrape-failure helpers skip their stock-0 and
fallback pushes; the local price/stock still update. Scheduled Updates and Manual
sync are the only pushes. (``push_after`` on the model is kept but always off.)

This is the manual "Start Scraping → Reset Failed to Pending → Start Scraping …" cycle,
done on a schedule. Celery Beat calls ``sync.tasks.check_auto_scrape_schedules`` every
minute; due stores get ``sync.tasks.run_store_auto_scrape`` on the ``sync`` queue.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

logger = logging.getLogger(__name__)

_TIME_RE = re.compile(r'^([01]?\d|2[0-3]):([0-5]\d)$')

# A run time still counts as due this long after it passed (covers a late Beat tick).
DUE_GRACE = timedelta(minutes=5)
# A run marked "running" for longer than this is treated as dead (worker restart).
STALE_RUNNING = timedelta(hours=12)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def normalize_run_time(value) -> str:
    m = _TIME_RE.match(str(value or '').strip())
    if not m:
        raise ValueError(f'"{value}" is not a valid time. Use HH:MM (24-hour).')
    return f'{int(m.group(1)):02d}:{m.group(2)}'


def normalize_auto_scrape_payload(data: dict) -> dict:
    """Validate the wizard's ``auto_scrape`` object. Raises ValueError with a user-facing message."""
    from sync.models import AutoScrapeSchedule

    raw_times = data.get('run_times')
    if isinstance(raw_times, str):
        raw_times = [t for t in raw_times.split(',') if t.strip()]
    if not isinstance(raw_times, (list, tuple)) or not raw_times:
        raise ValueError('Add at least one run time.')
    times = sorted({normalize_run_time(t) for t in raw_times})
    if len(times) > AutoScrapeSchedule.MAX_RUN_TIMES:
        raise ValueError(f'At most {AutoScrapeSchedule.MAX_RUN_TIMES} run times a day.')

    tz_name = str(data.get('timezone') or 'UTC').strip() or 'UTC'
    try:
        ZoneInfo(tz_name)
    except Exception:
        raise ValueError(f'Unknown timezone "{tz_name}".') from None

    try:
        cycles = int(data.get('retry_failed_cycles', 2))
    except (TypeError, ValueError):
        raise ValueError('Retry failed cycles must be a whole number.') from None
    if cycles < 0 or cycles > AutoScrapeSchedule.MAX_RETRY_CYCLES:
        raise ValueError(f'Retry failed cycles must be 0 to {AutoScrapeSchedule.MAX_RETRY_CYCLES}.')

    return {
        'run_times': times,
        'timezone': tz_name,
        'retry_failed_cycles': cycles,
        'push_after': False,  # Auto Scraping never pushes to the marketplace.
    }


def auto_scrape_run_active(store_id) -> bool:
    """True while an Auto Scraping run is going on this store (marketplace pushes are held)."""
    from sync.models import AutoScrapeSchedule

    if not store_id:
        return False
    try:
        return AutoScrapeSchedule.objects.filter(
            store_id=store_id,
            last_status='running',
            last_run_at__gte=timezone.now() - STALE_RUNNING,
        ).exists()
    except Exception:  # noqa: BLE001 — never block a scrape on this check
        logger.exception('auto_scrape_run_active check failed store=%s', store_id)
        return False


# ---------------------------------------------------------------------------
# Due check
# ---------------------------------------------------------------------------

def due_run_time(sched, now_utc=None):
    """Return the aware datetime of the run time that is due now, or None.

    A run time is due from its minute until ``DUE_GRACE`` later, once: the slot is
    taken when ``last_triggered_at`` is at or after it.
    """
    now_utc = now_utc or timezone.now()
    try:
        tz = ZoneInfo(sched.timezone or 'UTC')
    except Exception:
        tz = ZoneInfo('UTC')
    now_local = now_utc.astimezone(tz)
    last = sched.last_triggered_at

    best = None
    for raw in sched.run_times or []:
        try:
            hhmm = normalize_run_time(raw)
        except ValueError:
            continue
        h, m = (int(x) for x in hhmm.split(':'))
        for day_offset in (0, -1):  # yesterday's slot covers a tick just after midnight
            d = (now_local + timedelta(days=day_offset)).date()
            slot = datetime(d.year, d.month, d.day, h, m, tzinfo=tz)
            if slot <= now_local < slot + DUE_GRACE:
                if last is None or last < slot:
                    if best is None or slot > best:
                        best = slot
    return best


def claim_due_slot(sched, slot, now_utc=None) -> bool:
    """Atomically mark ``slot`` as triggered at the tick time. False when another tick already took it."""
    from django.db.models import Q

    from sync.models import AutoScrapeSchedule

    return bool(
        AutoScrapeSchedule.objects.filter(pk=sched.pk)
        .filter(Q(last_triggered_at__isnull=True) | Q(last_triggered_at__lt=slot))
        .update(last_triggered_at=now_utc or timezone.now())
    )


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def _log(store, message, action_type, metadata=None):
    try:
        from catalog.activity_log import append_catalog_log

        append_catalog_log(store.id, message, action_type=action_type, metadata=metadata or {})
    except Exception:
        logger.exception('append_catalog_log failed for %s', action_type)


def _is_managed(store) -> bool:
    return getattr(store, 'management_mode', '') == 'full_store'


def _scrape_already_running(store) -> bool:
    if _is_managed(store):
        from listings import scrape_progress as scrape_prog

        try:
            return bool(scrape_prog.enrich_progress_from_listings(store.id).get('active'))
        except Exception:
            logger.exception('Managed scrape progress check failed store=%s', store.id)
            return False
    from catalog.models import StoreCatalogCeleryScrapeState

    return StoreCatalogCeleryScrapeState.objects.filter(store_id=store.id).exists()


def _store_update_running(store) -> bool:
    """A Scheduled/manual store update started in the last few hours and not finished."""
    from sync.models import StoreSyncRun

    since = timezone.now() - timedelta(hours=3)
    return StoreSyncRun.objects.filter(
        store=store, status='running', finished_at__isnull=True, started_at__gte=since,
    ).exists()


# --- catalog (ProductMapping) stores --------------------------------------

def _catalog_failed_count(store) -> int:
    from catalog.models import ProductMapping

    return ProductMapping.objects.filter(store=store, is_active=True, sync_status='failed').count()


def _catalog_reset_all(store) -> int:
    from sync.tasks import _reset_active_listings_pending_for_store_update

    return int((_reset_active_listings_pending_for_store_update(store) or {}).get('rows_updated') or 0)


def _catalog_reset_failed(store) -> int:
    from catalog.models import ProductMapping

    return ProductMapping.objects.filter(
        store=store, is_active=True, sync_status='failed',
    ).update(sync_status='pending', failed_sync_count=0, scrape_error=None)


def _catalog_scrape_pass(store) -> dict:
    """Ingest vendors + live browser scrape, waiting for the scrape. Returns a stop reason or {}."""
    from sync.tasks import _run_browser_scrape_for_scheduled_update, _scheduled_ingest_refresh

    try:
        _scheduled_ingest_refresh(store)
    except Exception as exc:  # noqa: BLE001 — one vendor feed must not stop the scrape
        logger.exception('Auto scrape ingest refresh failed store=%s', store.id)
        _log(store, f'Auto Scraping: vendor feed refresh failed ({str(exc)[:200]}); continuing with the scrape.',
             'auto_scrape_warning')
    res = _run_browser_scrape_for_scheduled_update(store, 'auto_scrape') or {}
    if res.get('user_cancelled'):
        return {'stop': 'user_cancelled'}
    if res.get('still_running'):
        return {'stop': 'scrape_still_running'}
    if res.get('error'):
        return {'stop': 'error', 'error': str(res.get('error'))[:500]}
    return {}


# --- managed (full_store StoreListing) stores -----------------------------

def _managed_failed_count(store) -> int:
    from listings.models import InventorySyncStatus, ListingStatus, StoreListing

    # Same rows ``reset_inventory_status(scope='failed')`` resets.
    return StoreListing.objects.filter(
        user=store.user,
        store=store,
        status__in=[
            ListingStatus.UPLOADED_STAGING,
            ListingStatus.UPLOADED_PRODUCTION,
            ListingStatus.READY,
            ListingStatus.FAILED,
        ],
        inventory_sync_status=InventorySyncStatus.FAILED,
    ).count()


def _managed_reset(store, scope) -> int:
    from listings import listing_service

    return int((listing_service.reset_inventory_status(store.user, store, scope=scope) or {}).get('updated') or 0)


def _managed_scrape_pass(store) -> dict:
    from listings import listing_service
    from sync.tasks import _wait_managed_listing_scrape

    try:
        listing_service.start_scrape_async(store.user, store)
    except Exception as exc:  # noqa: BLE001
        return {'stop': 'error', 'error': str(exc)[:500]}
    res = _wait_managed_listing_scrape(store) or {}
    if res.get('cancelled'):
        return {'stop': 'user_cancelled'}
    if res.get('error'):
        return {'stop': 'scrape_still_running'}
    return {}


_STOP_TEXT = {
    'user_cancelled': 'you clicked Stop scraping',
    'scrape_still_running': 'the scrape was still running at the wait limit',
    'error': 'the scrape could not start',
}


def run_auto_scrape(store_id: str) -> dict:
    """Run one Auto Scraping job for a store (called from the Celery task)."""
    from stores.models import Store
    from sync.models import AutoScrapeSchedule

    try:
        store = Store.objects.select_related('marketplace', 'user').get(id=store_id)
        sched = AutoScrapeSchedule.objects.get(store=store)
    except (Store.DoesNotExist, AutoScrapeSchedule.DoesNotExist):
        return {'store_id': str(store_id), 'skipped': True, 'reason': 'not_found'}

    if not sched.is_active or not store.is_active:
        return {'store_id': str(store_id), 'skipped': True, 'reason': 'inactive'}

    now = timezone.now()
    if (
        sched.last_status == 'running'
        and sched.last_run_at
        and now - sched.last_run_at < STALE_RUNNING
    ):
        _log(store, 'Auto Scraping skipped: the previous auto run is still going.', 'auto_scrape_skipped',
             {'reason': 'previous_run_active'})
        return {'store_id': str(store_id), 'skipped': True, 'reason': 'previous_run_active'}
    if _scrape_already_running(store) or _store_update_running(store):
        _log(store, 'Auto Scraping skipped: a scrape or store update is already running.', 'auto_scrape_skipped',
             {'reason': 'scrape_running'})
        AutoScrapeSchedule.objects.filter(pk=sched.pk).update(
            last_status='skipped',
            last_summary={'reason': 'scrape_running', 'at': now.isoformat()},
        )
        return {'store_id': str(store_id), 'skipped': True, 'reason': 'scrape_running'}

    if _is_managed(store) and store.user is None:
        return {'store_id': str(store_id), 'skipped': True, 'reason': 'no_store_user'}

    managed = _is_managed(store)
    reset_all = (lambda: _managed_reset(store, 'all')) if managed else (lambda: _catalog_reset_all(store))
    reset_failed = (lambda: _managed_reset(store, 'failed')) if managed else (lambda: _catalog_reset_failed(store))
    scrape_pass = (lambda: _managed_scrape_pass(store)) if managed else (lambda: _catalog_scrape_pass(store))
    failed_count = (lambda: _managed_failed_count(store)) if managed else (lambda: _catalog_failed_count(store))

    max_cycles = int(sched.retry_failed_cycles or 0)
    summary = {
        'started_at': now.isoformat(),
        'retry_failed_cycles': max_cycles,
        'reset_all': 0,
        'failed_after': [],   # Failed count after the first scrape, then after each cycle
        'cycles_run': 0,
        'stop_reason': '',
    }
    AutoScrapeSchedule.objects.filter(pk=sched.pk).update(
        last_run_at=now, last_finished_at=None, last_status='running', last_summary=summary,
    )

    def _save(status):
        AutoScrapeSchedule.objects.filter(pk=sched.pk).update(
            last_status=status, last_finished_at=timezone.now(), last_summary=summary,
        )

    try:
        summary['reset_all'] = reset_all()
        _log(
            store,
            f"Auto Scraping started: {summary['reset_all']} active listing(s) set to Pending; "
            f"then up to {max_cycles} retry cycle(s) for Failed listings.",
            'auto_scrape_start',
            {'reset_all': summary['reset_all'], 'retry_failed_cycles': max_cycles},
        )

        stop = scrape_pass()
        n_failed = failed_count()
        summary['failed_after'].append(n_failed)

        cycle = 0
        while not stop.get('stop') and n_failed > 0 and cycle < max_cycles:
            cycle += 1
            n_reset = reset_failed()
            _log(
                store,
                f'Auto Scraping cycle {cycle} of {max_cycles}: {n_reset} failed listing(s) set to Pending, scraping again.',
                'auto_scrape_cycle',
                {'cycle': cycle, 'of': max_cycles, 'failed_reset': n_reset},
            )
            if n_reset == 0:
                break
            stop = scrape_pass()
            n_failed = failed_count()
            summary['failed_after'].append(n_failed)
            summary['cycles_run'] = cycle
            AutoScrapeSchedule.objects.filter(pk=sched.pk).update(last_summary=summary)

        summary['stop_reason'] = stop.get('stop') or ('no_failed' if n_failed == 0 else 'cycles_done')
        if stop.get('error'):
            summary['error'] = stop['error']

        trail = ' → '.join(str(n) for n in summary['failed_after'])
        if stop.get('stop'):
            status = 'stopped'
            msg = (
                f"Auto Scraping stopped after {summary['cycles_run']} retry cycle(s): "
                f"{_STOP_TEXT.get(stop['stop'], stop['stop'])}. Failed: {trail}."
            )
        else:
            status = 'success' if n_failed == 0 else 'partial'
            msg = (
                f"Auto Scraping finished: {summary['cycles_run']} retry cycle(s), Failed: {trail}. "
                'Nothing was sent to the marketplace.'
            )
        _log(store, msg, 'auto_scrape_end', dict(summary))
        _save(status)
        return {'store_id': str(store.id), 'status': status, **summary}
    except Exception as exc:
        logger.exception('Auto scrape failed store=%s', store.id)
        summary['stop_reason'] = 'error'
        summary['error'] = str(exc)[:500]
        _log(store, f'Auto Scraping failed: {str(exc)[:300]}', 'auto_scrape_end', dict(summary))
        _save('failed')
        raise


def check_due_schedules(now_utc=None) -> list:
    """Find due Auto Scraping schedules, claim their slot, and return store ids to run."""
    from sync.models import AutoScrapeSchedule

    now_utc = now_utc or timezone.now()
    due = []
    for sched in AutoScrapeSchedule.objects.filter(is_active=True, store__is_active=True).select_related('store'):
        slot = due_run_time(sched, now_utc)
        if slot is None:
            continue
        if claim_due_slot(sched, slot, now_utc):
            due.append(str(sched.store_id))
    return due
