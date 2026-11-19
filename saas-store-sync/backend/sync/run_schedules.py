"""
Auto Scheduling: Auto Scraping and Scheduled Updates runs, store-wide or per vendor.

Both kinds run the same way at their local run times:

  1. Active listings in scope go to Pending; vendor feeds refresh; vendor scrape (wait).
  2. Up to ``retry_failed_cycles`` times: reset only Failed listings in scope to Pending
     and scrape again. Stops early at 0 Failed, on Stop scraping, or when the scrape is
     still running at the wait limit.
  3. Scheduled Updates only: one marketplace push of the listings in scope (the Manual
     sync push, so Failed rows send their stock 0). MyDeal inventory-only stores have no
     server push: the run queues a "Upload the file on the Marketplace" run for the
     inventory bot laptop instead (Stock/Status + Price files of the vendors in scope).
     Auto Scraping never pushes.

While any run is active on a store, the per-row pushes in ``catalog.scrape_failure``
(stock 0 for a Failed row, the no-price fallback) are held: Auto Scraping sends nothing,
and a Scheduled Update sends everything once at the end.

Scope. A store-wide run covers every vendor except the ones that have their own times
of the same kind (``VendorSchedule``). A vendor run covers only that vendor. Vendor
times are for inventory-only stores (catalog ``ProductMapping`` rows).

Queue. One run per store at a time. A due run is saved as ``waiting`` and the minute
checker starts it when the store is free (no other run, no scrape, no store update,
no marketplace push). A run that waits longer than ``MAX_QUEUE_WAIT`` is skipped.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

KIND_SCRAPE = 'scrape'
KIND_UPDATE = 'update'
KINDS = (KIND_SCRAPE, KIND_UPDATE)

KIND_LABEL = {KIND_SCRAPE: 'Auto Scraping', KIND_UPDATE: 'Scheduled update'}

MAX_SCRAPE_RUN_TIMES = 6
MAX_UPDATE_RUN_TIMES = 12
MAX_RETRY_CYCLES = 10

_TIME_RE = re.compile(r'^([01]?\d|2[0-3]):([0-5]\d)$')

# A run time still counts as due this long after it passed (covers a late Beat tick).
DUE_GRACE = timedelta(minutes=5)
# A run waits at most this long for the store to be free, then it is skipped.
MAX_QUEUE_WAIT = timedelta(hours=3)
# A running run whose heartbeat is older than this is treated as dead (worker restart).
RUN_STALE = timedelta(minutes=45)
# The final marketplace push sends no heartbeats; during it a run stays alive this long
# (a managed push may run for hours; ``push_store_inventory`` allows 5).
PUSH_STALE = timedelta(hours=6)
# Heartbeats are written at most this often.
HEARTBEAT_EVERY_SEC = 60
# Manual "Update now" runs (StoreSyncRun) count as busy for this long.
STORE_UPDATE_BUSY = timedelta(hours=3)


# ---------------------------------------------------------------------------
# Settings (payload validation)
# ---------------------------------------------------------------------------

def max_run_times(kind: str) -> int:
    return MAX_UPDATE_RUN_TIMES if kind == KIND_UPDATE else MAX_SCRAPE_RUN_TIMES


def normalize_run_time(value) -> str:
    m = _TIME_RE.match(str(value or '').strip())
    if not m:
        raise ValueError(f'"{value}" is not a valid time. Use HH:MM (24-hour).')
    return f'{int(m.group(1)):02d}:{m.group(2)}'


def normalize_run_times(raw, kind: str) -> list[str]:
    if isinstance(raw, str):
        raw = [t for t in raw.split(',') if t.strip()]
    if not isinstance(raw, (list, tuple)) or not raw:
        raise ValueError('Add at least one run time.')
    times = sorted({normalize_run_time(t) for t in raw})
    limit = max_run_times(kind)
    if len(times) > limit:
        raise ValueError(f'At most {limit} run times a day.')
    return times


def normalize_timezone(value) -> str:
    tz_name = str(value or 'UTC').strip() or 'UTC'
    try:
        ZoneInfo(tz_name)
    except Exception:
        raise ValueError(f'Unknown timezone "{tz_name}".') from None
    return tz_name


def normalize_cycles(value, default: int) -> int:
    if value is None or value == '':
        value = default
    try:
        cycles = int(value)
    except (TypeError, ValueError):
        raise ValueError('Retry failed cycles must be a whole number.') from None
    if cycles < 0 or cycles > MAX_RETRY_CYCLES:
        raise ValueError(f'Retry failed cycles must be 0 to {MAX_RETRY_CYCLES}.')
    return cycles


def normalize_vendor_rows(raw, kind: str) -> list[dict]:
    """``[{vendor_id, run_times, retry_failed_cycles}]`` → cleaned rows (one per vendor)."""
    if raw in (None, ''):
        return []
    if not isinstance(raw, (list, tuple)):
        raise ValueError('Vendor times must be a list.')
    rows, seen = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError('Each vendor time must be an object.')
        vendor_id = str(item.get('vendor_id') or '').strip()
        if not vendor_id:
            raise ValueError('Choose a vendor for each vendor time.')
        if vendor_id in seen:
            raise ValueError('Each vendor can only be added once.')
        seen.add(vendor_id)
        try:
            times = normalize_run_times(item.get('run_times'), kind)
        except ValueError as exc:
            raise ValueError(f'Vendor times: {exc}') from None
        rows.append({
            'vendor_id': vendor_id,
            'run_times': times,
            'retry_failed_cycles': normalize_cycles(item.get('retry_failed_cycles'), 2),
        })
    return rows


def _int_or(value, default):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _hours_from_crontab_field(field) -> list[int]:
    field = str(field or '').strip() or '*'
    if field == '*':
        return list(range(0, 24, 2))
    hours = set()
    for part in field.split(','):
        part = part.strip()
        if not part:
            continue
        if '/' in part:
            base, step = part.split('/', 1)
            step = _int_or(step, 0)
            if step <= 0:
                continue
            start = 0 if base.strip() in ('*', '') else _int_or(base, 0)
            hours.update(range(max(0, start), 24, step))
        elif '-' in part:
            lo, hi = part.split('-', 1)
            hours.update(range(max(0, _int_or(lo, 0)), min(23, _int_or(hi, 23)) + 1))
        else:
            h = _int_or(part, -1)
            if 0 <= h <= 23:
                hours.add(h)
    return sorted(hours)


def run_times_from_crontab(schedule_type, crontab_hour, crontab_minute, interval_seconds=None) -> list[str]:
    """Old Frequency fields → run times (same rules as migration 0009)."""
    if schedule_type == 'interval' and interval_seconds:
        step = max(1, round(int(interval_seconds) / 3600))
        if 24 / step > MAX_UPDATE_RUN_TIMES:
            step = 2
        hours, minute = list(range(0, 24, step)), 0
    else:
        hours = _hours_from_crontab_field(crontab_hour)
        minute = _int_or(crontab_minute, 0)
        if not 0 <= minute <= 59:
            minute = 0
    if not hours:
        hours = [10]
    return [f'{h:02d}:{minute:02d}' for h in hours][:MAX_UPDATE_RUN_TIMES]


def sync_schedule_run_times(sched) -> list[str]:
    """Run times of a ``SyncSchedule``; falls back to its old crontab fields."""
    times = list(sched.run_times or [])
    if times:
        return times
    return run_times_from_crontab(
        sched.schedule_type, sched.crontab_hour, sched.crontab_minute, sched.interval_seconds,
    )


def normalize_card_payload(data: dict, kind: str) -> dict:
    """Validate the wizard's ``auto_scrape`` / ``sync_schedule`` object (when enabled).

    A Scheduled Updates object from an older page (crontab fields, no ``run_times``)
    is converted from its Frequency.
    """
    raw_times = data.get('run_times')
    if kind == KIND_UPDATE and not raw_times:
        raw_times = run_times_from_crontab(
            data.get('schedule_type', 'crontab'),
            data.get('crontab_hour', '10'),
            data.get('crontab_minute', '0'),
            data.get('interval_seconds'),
        )
    default_cycles = 2 if kind == KIND_SCRAPE else 0
    return {
        'run_times': normalize_run_times(raw_times, kind),
        'timezone': normalize_timezone(data.get('timezone')),
        'retry_failed_cycles': normalize_cycles(data.get('retry_failed_cycles'), default_cycles),
        'vendors': normalize_vendor_rows(data.get('vendors'), kind) if 'vendors' in data else None,
    }


# ---------------------------------------------------------------------------
# Due check
# ---------------------------------------------------------------------------

def due_slot(run_times, tz_name, last_triggered_at, now_utc=None):
    """Return the aware datetime of the run time that is due now, or None.

    A run time is due from its minute until ``DUE_GRACE`` later, once: the slot is
    taken when ``last_triggered_at`` is at or after it.
    """
    now_utc = now_utc or timezone.now()
    try:
        tz = ZoneInfo(tz_name or 'UTC')
    except Exception:
        tz = ZoneInfo('UTC')
    now_local = now_utc.astimezone(tz)

    best = None
    for raw in run_times or []:
        try:
            hhmm = normalize_run_time(raw)
        except ValueError:
            continue
        h, m = (int(x) for x in hhmm.split(':'))
        for day_offset in (0, -1):  # yesterday's slot covers a tick just after midnight
            d = (now_local + timedelta(days=day_offset)).date()
            slot = datetime(d.year, d.month, d.day, h, m, tzinfo=tz)
            if slot <= now_local < slot + DUE_GRACE:
                if last_triggered_at is None or last_triggered_at < slot:
                    if best is None or slot > best:
                        best = slot
    return best


def _claim(model, pk, slot, now_utc) -> bool:
    """Atomically mark ``slot`` as triggered. False when another tick already took it."""
    from django.db.models import Q

    return bool(
        model.objects.filter(pk=pk)
        .filter(Q(last_triggered_at__isnull=True) | Q(last_triggered_at__lt=slot))
        .update(last_triggered_at=now_utc)
    )


def _due_rows(kind: str):
    """Yield ``(model, row, store, vendor_id, run_times, tz)`` for every row that may run."""
    from sync.models import AutoScrapeSchedule, SyncSchedule, VendorSchedule

    if kind == KIND_SCRAPE:
        for sched in AutoScrapeSchedule.objects.filter(is_active=True, store__is_active=True).select_related('store'):
            yield AutoScrapeSchedule, sched, sched.store, None, sched.run_times, sched.timezone
        card_on = set(
            AutoScrapeSchedule.objects.filter(is_active=True).values_list('store_id', flat=True)
        )
    else:
        for sched in SyncSchedule.objects.filter(is_active=True, store__is_active=True).select_related('store'):
            yield SyncSchedule, sched, sched.store, None, sync_schedule_run_times(sched), sched.timezone
        card_on = set(SyncSchedule.objects.filter(is_active=True).values_list('store_id', flat=True))

    rows = (
        VendorSchedule.objects.filter(kind=kind, is_active=True, store__is_active=True, store_id__in=card_on)
        .exclude(store__management_mode='full_store')
        .select_related('store')
    )
    for row in rows:
        yield VendorSchedule, row, row.store, row.vendor_id, row.run_times, row.timezone


def check_due(kind: str, now_utc=None) -> list[str]:
    """Claim due run times of ``kind`` and queue a waiting run for each. Returns run ids."""
    from sync.models import ScheduledRun

    now_utc = now_utc or timezone.now()
    created = []
    for model, row, store, vendor_id, times, tz in _due_rows(kind):
        slot = due_slot(times, tz, row.last_triggered_at, now_utc)
        if slot is None or not _claim(model, row.pk, slot, now_utc):
            continue
        if _same_run_open(store.id, kind, vendor_id, now_utc):
            _log(
                store,
                f'{_run_name(kind, vendor_id)} at {slot.strftime("%H:%M")} skipped: '
                'the previous run of this schedule is still waiting or running.',
                f'{_action_prefix(kind)}_skipped',
                {'reason': 'previous_run_active', 'vendor_id': str(vendor_id) if vendor_id else None},
            )
            continue
        run = ScheduledRun.objects.create(
            store=store, vendor_id=vendor_id, kind=kind, status=ScheduledRun.STATUS_WAITING,
            slot_at=slot, summary={},
        )
        created.append(str(run.id))
    return created


def _alive_q(now_utc):
    """Running runs that are still alive (fresh heartbeat, or in the push phase)."""
    from django.db.models import Q

    from sync.models import ScheduledRun

    return Q(status=ScheduledRun.STATUS_RUNNING) & (
        Q(heartbeat_at__gte=now_utc - RUN_STALE)
        | Q(summary__phase='push', heartbeat_at__gte=now_utc - PUSH_STALE)
    )


def _same_run_open(store_id, kind, vendor_id, now_utc) -> bool:
    from sync.models import ScheduledRun

    qs = ScheduledRun.objects.filter(store_id=store_id, kind=kind, vendor_id=vendor_id)
    if qs.filter(status=ScheduledRun.STATUS_WAITING, created_at__gte=now_utc - MAX_QUEUE_WAIT).exists():
        return True
    return qs.filter(_alive_q(now_utc)).exists()


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------

def _scrape_running(store) -> bool:
    if getattr(store, 'management_mode', '') == 'full_store':
        from listings import scrape_progress as scrape_prog

        try:
            return bool(scrape_prog.enrich_progress_from_listings(store.id).get('active'))
        except Exception:
            logger.exception('Managed scrape progress check failed store=%s', store.id)
            return False
    from catalog.models import StoreCatalogCeleryScrapeState

    return StoreCatalogCeleryScrapeState.objects.filter(store_id=store.id).exists()


def _store_update_running(store, now_utc) -> bool:
    from sync.models import StoreSyncRun

    return StoreSyncRun.objects.filter(
        store=store, status='running', finished_at__isnull=True,
        started_at__gte=now_utc - STORE_UPDATE_BUSY,
    ).exists()


def _push_running(store) -> bool:
    try:
        from sync.push_listings_lock import is_push_listings_locked

        return is_push_listings_locked(str(store.id))
    except Exception:  # noqa: BLE001 — cache down: do not block runs on it
        logger.exception('push lock check failed store=%s', store.id)
        return False


def store_busy_reason(store, now_utc=None) -> str:
    """Why a new run cannot start on this store now ('' when it is free)."""
    from sync.models import ScheduledRun

    now_utc = now_utc or timezone.now()
    active = (
        ScheduledRun.objects.filter(_alive_q(now_utc), store_id=store.id)
        .select_related('vendor')
        .first()
    )
    if active is not None:
        return f'{_run_name(active.kind, active.vendor_id, active.vendor)} is running'
    if _scrape_running(store):
        return 'a vendor scrape is running'
    if _store_update_running(store, now_utc):
        return 'a store update is running'
    if _push_running(store):
        return 'a marketplace sync is running'
    return ''


def run_hold_active(store_id) -> bool:
    """True while a scheduled run is going on this store (per-row marketplace pushes are held)."""
    from sync.models import ScheduledRun

    if not store_id:
        return False
    try:
        return ScheduledRun.objects.filter(_alive_q(timezone.now()), store_id=store_id).exists()
    except Exception:  # noqa: BLE001 — never block a scrape on this check
        logger.exception('run_hold_active check failed store=%s', store_id)
        return False


def _finish(run, status, summary=None, now_utc=None):
    from sync.models import ScheduledRun

    fields = {'status': status, 'finished_at': now_utc or timezone.now()}
    if summary is not None:
        fields['summary'] = summary
    ScheduledRun.objects.filter(pk=run.pk).update(**fields)


def expire_runs(now_utc=None) -> None:
    """Skip runs that waited too long; mark runs whose worker died as failed."""
    from sync.models import ScheduledRun

    now_utc = now_utc or timezone.now()
    too_old = ScheduledRun.objects.filter(
        status=ScheduledRun.STATUS_WAITING, created_at__lt=now_utc - MAX_QUEUE_WAIT,
    ).select_related('store', 'vendor')
    for run in too_old:
        summary = dict(run.summary or {})
        summary.update({'stop_reason': 'waited_too_long'})
        _finish(run, 'skipped', summary, now_utc)
        _log(
            run.store,
            f'{_run_name(run.kind, run.vendor_id, run.vendor)} skipped: the store was busy for '
            f'{int(MAX_QUEUE_WAIT.total_seconds() // 3600)} hours.',
            f'{_action_prefix(run.kind)}_skipped',
            {'reason': 'waited_too_long', 'run_id': str(run.id)},
        )

    # Checked in Python: an ``exclude()`` on the JSON ``phase`` key drops rows whose
    # summary has no phase (SQL NULL), so dead runs would never be found.
    from django.db.models import Q

    maybe_dead = ScheduledRun.objects.filter(
        Q(heartbeat_at__isnull=True) | Q(heartbeat_at__lt=now_utc - RUN_STALE),
        status=ScheduledRun.STATUS_RUNNING,
    ).select_related('store', 'vendor')
    for run in maybe_dead:
        pushing = (run.summary or {}).get('phase') == 'push'
        if pushing and run.heartbeat_at and run.heartbeat_at >= now_utc - PUSH_STALE:
            continue
        summary = dict(run.summary or {})
        summary.update({'stop_reason': 'worker_lost'})
        _finish(run, 'failed', summary, now_utc)
        _log(
            run.store,
            f'{_run_name(run.kind, run.vendor_id, run.vendor)} stopped: the worker running it '
            'stopped (server restart). The next run time starts a new run.',
            f'{_action_prefix(run.kind)}_end',
            {'reason': 'worker_lost', 'run_id': str(run.id)},
        )


def try_start_next(store_id, now_utc=None):
    """Start the oldest waiting run of this store if the store is free. Returns the run or None."""
    from stores.models import Store
    from sync.models import ScheduledRun

    now_utc = now_utc or timezone.now()
    with transaction.atomic():
        store = Store.objects.select_for_update().filter(pk=store_id).first()
        if store is None:
            return None
        waiting = list(
            ScheduledRun.objects.filter(
                store_id=store_id, status=ScheduledRun.STATUS_WAITING,
                created_at__gte=now_utc - MAX_QUEUE_WAIT,
            ).select_related('vendor').order_by('created_at', 'id')
        )
        if not waiting:
            return None
        reason = store_busy_reason(store, now_utc)
        if reason:
            for run in waiting:
                summary = dict(run.summary or {})
                if summary.get('wait_logged'):
                    continue
                summary['wait_logged'] = True
                ScheduledRun.objects.filter(pk=run.pk).update(summary=summary)
                _log(
                    store,
                    f'{_run_name(run.kind, run.vendor_id, run.vendor)} is waiting: {reason}. '
                    'It starts when that finishes.',
                    f'{_action_prefix(run.kind)}_waiting',
                    {'reason': reason, 'run_id': str(run.id)},
                )
            return None
        run = waiting[0]
        ScheduledRun.objects.filter(pk=run.pk).update(
            status=ScheduledRun.STATUS_RUNNING, started_at=now_utc, heartbeat_at=now_utc,
        )
        run.status = ScheduledRun.STATUS_RUNNING
        run.started_at = run.heartbeat_at = now_utc
        return run


def start_waiting_runs(now_utc=None, enqueue=None) -> list[str]:
    """Expire old runs, then start the next waiting run on every free store."""
    from sync.models import ScheduledRun

    now_utc = now_utc or timezone.now()
    expire_runs(now_utc)
    store_ids = (
        ScheduledRun.objects.filter(status=ScheduledRun.STATUS_WAITING)
        .values_list('store_id', flat=True)
        .distinct()
    )
    started = []
    for store_id in list(store_ids):
        run = try_start_next(store_id, now_utc)
        if run is None:
            continue
        started.append(str(run.id))
        _enqueue(run, enqueue)
    return started


def _enqueue(run, enqueue=None):
    if enqueue is not None:
        enqueue(str(run.id))
        return
    from sync.tasks import run_scheduled_run

    try:
        run_scheduled_run.apply_async(args=[str(run.id)])
    except Exception:
        # Not sent: put it back so the next minute tick tries again.
        from sync.models import ScheduledRun

        logger.exception('Could not queue scheduled run %s', run.id)
        ScheduledRun.objects.filter(pk=run.pk, status=ScheduledRun.STATUS_RUNNING).update(
            status=ScheduledRun.STATUS_WAITING, started_at=None, heartbeat_at=None,
        )


def queue_store_run(store_id, kind: str, vendor_id=None, enqueue=None):
    """Queue a store-wide (or vendor) run now and start it if the store is free."""
    from sync.models import ScheduledRun

    now_utc = timezone.now()
    if _same_run_open(store_id, kind, vendor_id, now_utc):
        return None
    run = ScheduledRun.objects.create(
        store_id=store_id, vendor_id=vendor_id, kind=kind, status=ScheduledRun.STATUS_WAITING,
        slot_at=now_utc, summary={},
    )
    started = try_start_next(store_id, now_utc)
    if started is not None:
        _enqueue(started, enqueue)
    return run


def tick(kind: str, now_utc=None) -> dict:
    """Celery Beat, every minute (per kind): queue due runs, then start what can start."""
    now_utc = now_utc or timezone.now()
    created = check_due(kind, now_utc)
    started = start_waiting_runs(now_utc)
    return {'kind': kind, 'queued': created, 'started': started}


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def _log(store, message, action_type, metadata=None):
    try:
        from catalog.activity_log import append_catalog_log

        append_catalog_log(store.id, message, action_type=action_type, metadata=metadata or {})
    except Exception:
        logger.exception('append_catalog_log failed for %s', action_type)


def _action_prefix(kind: str) -> str:
    return 'auto_scrape' if kind == KIND_SCRAPE else 'scheduled_update'


def _vendor_name(vendor_id, vendor=None) -> str:
    if vendor is not None:
        return (getattr(vendor, 'name', '') or '').strip() or str(vendor_id)
    if not vendor_id:
        return ''
    from vendor.models import Vendor

    name = Vendor.objects.filter(pk=vendor_id).values_list('name', flat=True).first()
    return (name or '').strip() or str(vendor_id)


def _run_name(kind, vendor_id=None, vendor=None) -> str:
    label = KIND_LABEL.get(kind, kind)
    if vendor_id:
        return f'{label} for {_vendor_name(vendor_id, vendor)}'
    return label


def _is_managed(store) -> bool:
    return getattr(store, 'management_mode', '') == 'full_store'


def _store_is_mydeal(store) -> bool:
    from catalog.mydeal_templates import store_is_mydeal

    return store_is_mydeal(store)


def _card_and_row(store, kind, vendor_id):
    """``(card_on, cycles)`` for the run's schedule row; ``(False, 0)`` when it is off or gone."""
    from sync.models import AutoScrapeSchedule, SyncSchedule, VendorSchedule

    card_model = AutoScrapeSchedule if kind == KIND_SCRAPE else SyncSchedule
    card = card_model.objects.filter(store=store).first()
    if card is None or not card.is_active:
        return False, 0
    if not vendor_id:
        return True, int(card.retry_failed_cycles or 0)
    if _is_managed(store):
        return False, 0
    row = VendorSchedule.objects.filter(store=store, vendor_id=vendor_id, kind=kind, is_active=True).first()
    if row is None:
        return False, 0
    return True, int(row.retry_failed_cycles or 0)


def _excluded_vendor_ids(store, kind) -> list:
    """Vendors with their own times of ``kind`` (left out of the store-wide run)."""
    from sync.models import VendorSchedule

    if _is_managed(store):
        return []
    return list(
        VendorSchedule.objects.filter(store=store, kind=kind, is_active=True).values_list('vendor_id', flat=True)
    )


class _Heartbeat:
    def __init__(self, run):
        self.run = run
        self._last = 0.0

    def __call__(self, force=False):
        from sync.models import ScheduledRun

        now = time.monotonic()
        if not force and now - self._last < HEARTBEAT_EVERY_SEC:
            return
        self._last = now
        ScheduledRun.objects.filter(pk=self.run.pk).update(heartbeat_at=timezone.now())


# --- catalog (ProductMapping) stores --------------------------------------

def _catalog_scope(qs, include, exclude):
    if include:
        qs = qs.filter(product__vendor_id__in=list(include))
    if exclude:
        qs = qs.exclude(product__vendor_id__in=list(exclude))
    return qs


def _catalog_failed_count(store, include, exclude) -> int:
    from catalog.models import ProductMapping

    qs = ProductMapping.objects.filter(store=store, is_active=True, sync_status='failed')
    return _catalog_scope(qs, include, exclude).count()


def _catalog_reset_all(store, include, exclude) -> int:
    from catalog.tasks import _chunked_reset_store_active_listings_pending_scrape
    from stores.models import Store

    stats = _chunked_reset_store_active_listings_pending_scrape(
        store, vendor_ids=include or None, exclude_vendor_ids=exclude or None,
    ) or {}
    if not include and not exclude:
        # Whole store reset: same as a Scheduled Update / Reset all to Pending.
        Store.objects.filter(pk=store.pk).update(catalog_pending_reset_at=None, catalog_zero_pending_at=None)
    return int(stats.get('rows_updated') or 0)


def _catalog_reset_failed(store, include, exclude) -> int:
    from catalog.models import ProductMapping

    qs = ProductMapping.objects.filter(store=store, is_active=True, sync_status='failed')
    ids = list(_catalog_scope(qs, include, exclude).values_list('id', flat=True))
    if not ids:
        return 0
    return ProductMapping.objects.filter(id__in=ids).update(
        sync_status='pending', failed_sync_count=0, scrape_error=None,
    )


def _catalog_scrape_pass(store, source, heartbeat) -> dict:
    """Vendor feeds + live browser scrape, waiting for the scrape. Returns a stop reason or {}."""
    from sync.tasks import _run_browser_scrape_for_scheduled_update, _scheduled_ingest_refresh

    heartbeat(force=True)
    try:
        _scheduled_ingest_refresh(store)
    except Exception as exc:  # noqa: BLE001 — one vendor feed must not stop the scrape
        logger.exception('Scheduled run ingest refresh failed store=%s', store.id)
        _log(store, f'Vendor feed refresh failed ({str(exc)[:200]}); continuing with the scrape.',
             'scheduled_run_warning')
    heartbeat(force=True)
    res = _run_browser_scrape_for_scheduled_update(store, source, on_poll=heartbeat) or {}
    heartbeat(force=True)
    if res.get('user_cancelled'):
        return {'stop': 'user_cancelled'}
    if res.get('still_running'):
        return {'stop': 'scrape_still_running'}
    if res.get('error'):
        return {'stop': 'error', 'error': str(res.get('error'))[:500]}
    return {}


def _catalog_push(store, run, include, exclude) -> dict:
    """Manual sync push of the rows in scope, under the store's push lock."""
    from sync.push_listings_cancel import clear_push_listings_cancel, should_abort_push_listings
    from sync.push_listings_lock import release_push_listings_lock, try_acquire_push_listings_lock
    from sync.tasks import _execute_store_push_listings_only, _store_can_push_to_marketplace

    if not _store_can_push_to_marketplace(store.id):
        return {'push': 'skipped_not_connected'}
    store_key = str(store.id)
    owner = f'scheduled-run:{run.id}'
    if not try_acquire_push_listings_lock(store_key, owner):
        return {'push': 'skipped_push_already_running'}
    try:
        if should_abort_push_listings(store_key):
            return {'push': 'cancelled'}
        res = _execute_store_push_listings_only(
            store.id,
            disable_schedule=False,
            vendor_id=(include[0] if include else None),
            exclude_vendor_ids=(exclude or None),
        ) or {}
    finally:
        clear_push_listings_cancel(store_key)
        release_push_listings_lock(store_key, owner)
    if res.get('error'):
        return {'push': f"skipped_{res['error']}"}
    return {
        'push': 'cancelled' if res.get('cancelled') else 'done',
        'pushed': int(res.get('pushed') or 0),
        'push_failed': int(res.get('failed') or 0),
        'push_skipped_no_listing': int(res.get('skipped_no_listing') or 0),
    }


def _mydeal_bot_upload(store, include, exclude) -> dict:
    """MyDeal: queue the inventory bot laptop upload of the vendors in scope.

    The ZIP is built when the laptop claims the run, so it carries the prices and
    stock this run just scraped. The Unmapped group (MyDeal SKUs with no catalog
    listing) is never uploaded automatically.
    """
    from catalog import mydeal_upload_runs as upload_runs
    from catalog.mydeal_templates import UNMAPPED_VENDOR, vendor_template_summary

    include_ids = {str(v) for v in include or []}
    exclude_ids = {str(v) for v in exclude or []}
    vendor_ids = []
    for v in vendor_template_summary(store):
        vid = str(v['id'])
        if vid == UNMAPPED_VENDOR or vid in exclude_ids:
            continue
        if include_ids and vid not in include_ids:
            continue
        if v.get('price_rows') or v.get('inventory_rows'):
            vendor_ids.append(vid)
    if not vendor_ids:
        return {'push': 'skipped_no_template_rows'}
    try:
        run = upload_runs.start_run(store, None, vendor_ids, list(upload_runs.KINDS))
    except upload_runs.UploadRunError as exc:
        msg = str(exc)
        if 'already waiting or running' in msg:
            return {'push': 'skipped_upload_already_queued'}
        return {'push': 'skipped_upload_error', 'push_error': msg[:300]}
    return {'push': 'queued', 'upload_run_id': str(run.id), 'upload_vendors': len(vendor_ids)}


# --- managed (full_store StoreListing) stores: store-wide only ---------------

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


def _managed_scrape_pass(store, heartbeat) -> dict:
    from listings import listing_service
    from sync.tasks import _wait_managed_listing_scrape

    heartbeat(force=True)
    try:
        listing_service.start_scrape_async(store.user, store)
    except Exception as exc:  # noqa: BLE001
        return {'stop': 'error', 'error': str(exc)[:500]}
    res = _wait_managed_listing_scrape(store, on_poll=heartbeat) or {}
    heartbeat(force=True)
    if res.get('cancelled'):
        return {'stop': 'user_cancelled'}
    if res.get('error'):
        return {'stop': 'scrape_still_running'}
    return {}


def _managed_push(store) -> dict:
    from listings import listing_service
    from sync.tasks import _store_can_push_to_marketplace

    if not _store_can_push_to_marketplace(store.id):
        return {'push': 'skipped_not_connected'}
    try:
        res = listing_service.push_inventory(store.user, store) or {}
    except Exception as exc:  # noqa: BLE001 — MarketplaceError and others
        logger.exception('Scheduled update managed push failed store=%s', store.id)
        return {'push': 'failed', 'push_error': str(exc)[:300]}
    out = {
        'push': 'done' if res.get('ok', True) else 'failed',
        'pushed': int(res.get('pushed') or 0),
        'push_failed': int(res.get('failed') or 0),
    }
    if not res.get('ok', True) and res.get('message'):
        out['push_error'] = str(res.get('message'))[:300]
    return out


_STOP_TEXT = {
    'user_cancelled': 'you clicked Stop scraping',
    'scrape_still_running': 'the scrape was still running at the wait limit',
    'error': 'the scrape could not start',
}


def execute_run(run_id) -> dict:
    """Run one waiting→running ``ScheduledRun`` (called from the Celery task)."""
    from sync.models import ScheduledRun, SyncSchedule

    run = ScheduledRun.objects.select_related('store', 'store__marketplace', 'store__user', 'vendor').filter(
        pk=run_id,
    ).first()
    if run is None:
        return {'run_id': str(run_id), 'skipped': True, 'reason': 'not_found'}
    if run.status != ScheduledRun.STATUS_RUNNING:
        return {'run_id': str(run_id), 'skipped': True, 'reason': f'status_{run.status}'}

    store, kind, vendor_id = run.store, run.kind, run.vendor_id
    name = _run_name(kind, vendor_id, run.vendor)
    prefix = _action_prefix(kind)
    now = timezone.now()

    card_on, max_cycles = _card_and_row(store, kind, vendor_id)
    if not store.is_active or not card_on:
        _finish(run, 'skipped', {'stop_reason': 'turned_off'})
        _log(store, f'{name} skipped: it was turned off (or the store is inactive) before it started.',
             f'{prefix}_skipped', {'reason': 'turned_off', 'run_id': str(run.id)})
        return {'run_id': str(run.id), 'skipped': True, 'reason': 'turned_off'}
    managed = _is_managed(store)
    if managed and store.user is None:
        _finish(run, 'skipped', {'stop_reason': 'no_store_user'})
        return {'run_id': str(run.id), 'skipped': True, 'reason': 'no_store_user'}

    include = [vendor_id] if vendor_id else []
    exclude = [] if vendor_id else _excluded_vendor_ids(store, kind)
    heartbeat = _Heartbeat(run)
    source = 'auto_scrape' if kind == KIND_SCRAPE else 'scheduled_update'

    if managed:
        reset_all = lambda: _managed_reset(store, 'all')  # noqa: E731
        reset_failed = lambda: _managed_reset(store, 'failed')  # noqa: E731
        scrape_pass = lambda: _managed_scrape_pass(store, heartbeat)  # noqa: E731
        failed_count = lambda: _managed_failed_count(store)  # noqa: E731
        push = lambda: _managed_push(store)  # noqa: E731
    else:
        reset_all = lambda: _catalog_reset_all(store, include, exclude)  # noqa: E731
        reset_failed = lambda: _catalog_reset_failed(store, include, exclude)  # noqa: E731
        scrape_pass = lambda: _catalog_scrape_pass(store, source, heartbeat)  # noqa: E731
        failed_count = lambda: _catalog_failed_count(store, include, exclude)  # noqa: E731
        if _store_is_mydeal(store):
            push = lambda: _mydeal_bot_upload(store, include, exclude)  # noqa: E731
        else:
            push = lambda: _catalog_push(store, run, include, exclude)  # noqa: E731

    summary = {
        'started_at': now.isoformat(),
        'kind': kind,
        'vendor_id': str(vendor_id) if vendor_id else None,
        'excluded_vendor_ids': [str(v) for v in exclude],
        'retry_failed_cycles': max_cycles,
        'reset_all': 0,
        'failed_after': [],   # Failed count after the first scrape, then after each cycle
        'cycles_run': 0,
        'stop_reason': '',
        'push': '',
    }
    if (run.summary or {}).get('wait_logged'):
        summary['waited'] = True
    ScheduledRun.objects.filter(pk=run.pk).update(summary=summary)

    def _save_progress():
        ScheduledRun.objects.filter(pk=run.pk).update(summary=summary, heartbeat_at=timezone.now())

    excluded_note = ''
    if exclude:
        excluded_note = ' (vendors with their own times are left out)'
    try:
        summary['reset_all'] = reset_all()
        _log(
            store,
            f"{name} started: {summary['reset_all']} active listing(s) set to Pending{excluded_note}; "
            f'then up to {max_cycles} retry cycle(s) for Failed listings'
            + ('; nothing is sent to the marketplace.' if kind == KIND_SCRAPE
               else '; then one marketplace push at the end.'),
            f'{prefix}_start',
            {'run_id': str(run.id), 'reset_all': summary['reset_all'], 'retry_failed_cycles': max_cycles,
             'vendor_id': summary['vendor_id']},
        )
        _save_progress()

        if summary['reset_all'] == 0:
            # Every vendor has its own times (or there are no active listings).
            summary['stop_reason'] = 'nothing_in_scope'
            _log(store, f'{name} finished: no active listings to scrape{excluded_note}.',
                 f'{prefix}_end', dict(summary, run_id=str(run.id)))
            _finish(run, 'success', summary)
            return {'run_id': str(run.id), 'status': 'success', **summary}

        stop = scrape_pass()
        n_failed = failed_count()
        summary['failed_after'].append(n_failed)
        _save_progress()

        cycle = 0
        while not stop.get('stop') and n_failed > 0 and cycle < max_cycles:
            cycle += 1
            n_reset = reset_failed()
            _log(
                store,
                f'{name} cycle {cycle} of {max_cycles}: {n_reset} failed listing(s) set to Pending, scraping again.',
                f'{prefix}_cycle',
                {'run_id': str(run.id), 'cycle': cycle, 'of': max_cycles, 'failed_reset': n_reset},
            )
            if n_reset == 0:
                break
            stop = scrape_pass()
            n_failed = failed_count()
            summary['failed_after'].append(n_failed)
            summary['cycles_run'] = cycle
            _save_progress()

        summary['stop_reason'] = stop.get('stop') or ('no_failed' if n_failed == 0 else 'cycles_done')
        if stop.get('error'):
            summary['error'] = stop['error']

        push_failed = False
        if kind == KIND_UPDATE:
            if stop.get('stop') == 'scrape_still_running':
                # Rows are still changing; the next run time pushes them.
                summary['push'] = 'skipped_scrape_still_running'
            else:
                summary['phase'] = 'push'
                _save_progress()
                summary.update(push())
                summary['phase'] = 'done'
                push_failed = summary.get('push') == 'failed' or bool(summary.get('push_failed'))
            if not vendor_id:
                SyncSchedule.objects.filter(store=store).update(last_run=timezone.now())

        trail = ' → '.join(str(n) for n in summary['failed_after'])
        push_note = _push_note(summary) if kind == KIND_UPDATE else ' Nothing was sent to the marketplace.'
        if stop.get('stop'):
            status = 'stopped'
            msg = (
                f"{name} stopped after {summary['cycles_run']} retry cycle(s): "
                f"{_STOP_TEXT.get(stop['stop'], stop['stop'])}. Failed: {trail}.{push_note}"
            )
        else:
            status = 'success' if n_failed == 0 and not push_failed else 'partial'
            msg = f"{name} finished: {summary['cycles_run']} retry cycle(s), Failed: {trail}.{push_note}"
        _log(store, msg, f'{prefix}_end', dict(summary, run_id=str(run.id)))
        _finish(run, status, summary)
        return {'run_id': str(run.id), 'status': status, **summary}
    except Exception as exc:
        logger.exception('Scheduled run failed run=%s store=%s', run.id, store.id)
        summary['stop_reason'] = 'error'
        summary['error'] = str(exc)[:500]
        _log(store, f'{name} failed: {str(exc)[:300]}', f'{prefix}_end', dict(summary, run_id=str(run.id)))
        _finish(run, 'failed', summary)
        raise


def _push_note(summary) -> str:
    push = summary.get('push') or ''
    if push == 'done':
        return (
            f" Marketplace push: {summary.get('pushed', 0)} updated"
            + (f", {summary['push_failed']} failed" if summary.get('push_failed') else '')
            + '.'
        )
    if push == 'cancelled':
        return ' Marketplace push was cancelled.'
    if push == 'skipped_not_connected':
        return ' Marketplace push skipped: the store is not connected.'
    if push == 'skipped_push_already_running':
        return ' Marketplace push skipped: a Manual sync was already running.'
    if push == 'skipped_scrape_still_running':
        return ' Marketplace push skipped: the scrape is still running.'
    if push == 'queued':
        return (
            f" Marketplace upload queued for the inventory bot laptop ({summary.get('upload_vendors', 0)} "
            'vendor file set(s)).'
        )
    if push == 'skipped_upload_already_queued':
        return ' Marketplace upload not queued: an upload for this store is already waiting or running.'
    if push == 'skipped_no_template_rows':
        return ' Marketplace upload not queued: no MyDeal template rows for these vendors.'
    if push == 'skipped_upload_error':
        return f" Marketplace upload not queued: {summary.get('push_error') or 'see the logs'}."
    if push == 'failed':
        return f" Marketplace push failed: {summary.get('push_error') or 'see the logs'}."
    if push.startswith('skipped_'):
        return f' Marketplace push skipped ({push[8:]}).'
    return ''


# ---------------------------------------------------------------------------
# Read helpers (store settings)
# ---------------------------------------------------------------------------

def run_info(run) -> dict:
    if run is None:
        return {'last_run_at': None, 'last_finished_at': None, 'last_status': '', 'last_summary': {}}
    started = run.started_at or run.created_at
    return {
        'last_run_at': started.isoformat() if started else None,
        'last_finished_at': run.finished_at.isoformat() if run.finished_at else None,
        'last_status': run.status or '',
        'last_summary': run.summary or {},
    }


def latest_runs_by_key(store) -> dict:
    """``{(kind, vendor_id_or_None): ScheduledRun}`` — newest run of each schedule."""
    from sync.models import ScheduledRun

    out = {}
    for run in ScheduledRun.objects.filter(store=store).order_by('-created_at')[:300]:
        key = (run.kind, str(run.vendor_id) if run.vendor_id else None)
        out.setdefault(key, run)
    return out
