"""Scheduled ordering: per Auto Purchasing vendor, at the times set on the store's Auto Purchasing step.

At each time:
1. the laptop fetches the marketplace orders file (as the Fetch button); the import checks every new
   order for stock and profit;
2. once the fetch has finished and no order of the store is still being checked, the vendor's orders
   placed that day that are ready to buy are sent to the laptop (as Process all for that vendor).
   Ready orders from earlier days are not sent: they wait for the person.

Loss orders, blocked orders and orders from other vendors are left for the person, as with Process all.
Vendors with the same time share one fetch. A time that comes while the previous run of that vendor is
still waiting is skipped.
"""
from __future__ import annotations

import logging
import os
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)


def _hours(name, default) -> timedelta:
    try:
        return timedelta(hours=float(os.environ.get(name) or default))
    except ValueError:
        return timedelta(hours=default)


# The fetch may wait this long for the laptop; then the orders checked so far are processed.
FETCH_WAIT = _hours('AUTO_ORDER_FETCH_WAIT_HOURS', 3)
# After the fetch, the checks may take this long before the ready orders are sent anyway.
CHECK_WAIT = _hours('AUTO_ORDER_CHECK_WAIT_HOURS', 1)


def _rows():
    from stores.models import StoreAutoBuyVendor

    return (
        StoreAutoBuyVendor.objects.filter(auto_process=True, store__is_active=True, store__user__isnull=False)
        .select_related('store', 'store__user', 'store__marketplace')
    )


def _claim(row, slot, now) -> bool:
    from stores.models import StoreAutoBuyVendor

    return bool(
        StoreAutoBuyVendor.objects.filter(pk=row.pk)
        .filter(Q(process_last_triggered_at__isnull=True) | Q(process_last_triggered_at__lt=slot))
        .update(process_last_triggered_at=now)
    )


def _label(vendor) -> str:
    from .auto_purchase_check import AUTO_BUY_LABELS

    return AUTO_BUY_LABELS.get(vendor, vendor)


def _save(row, **fields):
    for k, v in fields.items():
        setattr(row, k, v)
    row.save(update_fields=list(fields))


def start_due(now=None) -> list[str]:
    """Start the scheduled runs whose time has come. Returns 'store/vendor' for each one started."""
    from stores.auto_buy import default_timezone
    from sync.run_schedules import due_slot

    from .auto_purchase_jobs import AutoPurchaseError, start_fetch

    now = now or timezone.now()
    started = []
    for row in _rows():
        store = row.store
        slot = due_slot(row.process_times, row.process_timezone or default_timezone(store.region),
                        row.process_last_triggered_at, now)
        if slot is None or not _claim(row, slot, now):
            continue
        at = slot.strftime('%H:%M')
        if row.process_pending_since:
            _save(row, process_last_message=(
                f'{at} skipped: the {row.process_pending_since:%H:%M} UTC run is still waiting for the fetch '
                f'or the checks.'
            ))
            continue
        fetch_id = None
        try:
            run, _created = start_fetch(store, store.user)
            fetch_id = run.id
            note = f'{at}: fetching orders on the laptop, then {_label(row.vendor)} orders are sent to buy.'
        except AutoPurchaseError as exc:
            note = f'{at}: fetch not started ({exc}). Orders already checked are sent to buy.'
        _save(row, process_pending_since=now, process_fetch_run_id=fetch_id, process_last_message=note)
        logger.info('Scheduled ordering started: store %s vendor %s fetch %s', store.id, row.vendor, fetch_id)
        started.append(f'{store.id}/{row.vendor}')
    return started


def _waiting_reason(row, now) -> str:
    """Why this run still waits ('' = ready to send the orders)."""
    from stores.auto_order_bot import ACTIVE
    from stores.models import AutoOrderRun

    from .models import OrderPurchaseCheck, PurchaseCheckStatus

    waited = now - row.process_pending_since
    if row.process_fetch_run_id:
        run = AutoOrderRun.objects.filter(pk=row.process_fetch_run_id).only('status').first()
        if run is not None and run.status in ACTIVE and waited < FETCH_WAIT:
            return 'fetch'
    checking = OrderPurchaseCheck.objects.filter(
        order__store_id=row.store_id, status=PurchaseCheckStatus.CHECKING,
    ).exists()
    if checking and waited < FETCH_WAIT + CHECK_WAIT:
        return 'checks'
    return ''


def _fetch_note(row) -> str:
    from stores.auto_order_bot import ACTIVE
    from stores.models import AutoOrderRun

    if not row.process_fetch_run_id:
        return ''
    run = AutoOrderRun.objects.filter(pk=row.process_fetch_run_id).only('status').first()
    if run is None:
        return ''
    if run.status in ACTIVE:
        return 'The laptop did not fetch the orders in time; sent the orders already checked. '
    if run.status != AutoOrderRun.Status.FINISHED:
        return f'Fetch {run.get_status_display().lower()}; sent the orders already checked. '
    return ''


def finish_ready(now=None) -> list[str]:
    """Send the orders of every scheduled run whose fetch and checks are done: that vendor's ready
    orders placed on the run's day. Older ready orders are left for the person."""
    from zoneinfo import ZoneInfo

    from stores.auto_buy import default_timezone

    from .auto_purchase_jobs import AutoPurchaseError, process_all

    now = now or timezone.now()
    done = []
    for row in _rows().filter(process_pending_since__isnull=False):
        if _waiting_reason(row, now):
            continue
        note = _fetch_note(row)
        store = row.store
        # Only the orders placed on the day of this run (in the schedule's timezone).
        tz_name = row.process_timezone or default_timezone(store.region)
        day = row.process_pending_since.astimezone(ZoneInfo(tz_name)).date()
        try:
            result = process_all(store, store.user, vendor=row.vendor, ordered_on=day, tz_name=tz_name)
            message = note + result['message']
        except AutoPurchaseError as exc:
            message = note + f'Not sent: {exc}'
        except Exception:
            logger.exception('Scheduled ordering failed: store %s vendor %s', store.id, row.vendor)
            message = note + 'Not sent: unexpected error (see the server log).'
        _save(row, process_pending_since=None, process_fetch_run_id=None,
              process_last_run_at=now, process_last_message=message[:2000])
        logger.info('Scheduled ordering: store %s vendor %s: %s', store.id, row.vendor, message)
        done.append(f'{store.id}/{row.vendor}')
    return done


def tick(now=None) -> dict:
    now = now or timezone.now()
    return {'started': start_due(now), 'sent': finish_ready(now)}
