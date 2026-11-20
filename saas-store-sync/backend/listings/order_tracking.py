"""Tracking Bot: the worker laptop reads the vendor's tracking number of purchased orders.

A ``tracking`` job (``stores.AutoOrderRun``) carries an orders file with one row per purchased order
(our order id, the vendor order number, the customer's name). The Tracking Bot on the laptop opens each
order in the purchase account's Chrome profile, clicks Track package and reads the tracking ID and carrier.
Its result file comes back through the order-bot ingest endpoints and is saved as ``OrderTracking`` rows.

* Every few hours (Celery beat) and from the Get tracking buttons on the Orders page.
* Only orders bought in the last ``TRACKING_DAYS`` days, with a real vendor order number and no tracking
  the user entered; an order whose packages all have tracking is not checked again by the schedule.
* A number the user typed (Replace) is never changed by the bot.
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta
from pathlib import Path

import openpyxl
from django.db import transaction
from django.utils import timezone

from .auto_purchase_jobs import AutoPurchaseError, _bot, _new_run, purchase_account_for

logger = logging.getLogger(__name__)

# Purchase-account vendors the Tracking Bot can read (Amazon AU first; others later).
TRACKING_VENDORS = ('amazon_au',)
TRACKING_DAYS = 30
ORDER_COLUMNS = ['order_ref', 'invoice', 'vendor', 'vendor_order_number', 'customer_name', 'purchased_at']
# Statuses the schedule checks again (found / cancelled orders are left alone).
RECHECK_STATUSES = ('', 'not_shipped', 'partly', 'failed', 'queued')


def _customer_name(order) -> str:
    raw = order.raw_response_json if isinstance(order.raw_response_json, dict) else {}
    cust = raw.get('customer') or order.customer_info_json or {}
    name = cust.get('name') if isinstance(cust, dict) else ''
    if not name and isinstance(order.customer_info_json, dict):
        name = order.customer_info_json.get('name') or ''
    return ' '.join(str(name or '').split())


def order_vendor(check, store) -> str:
    vendors = sorted({ln.get('auto_buy_vendor') for ln in check.lines_json or [] if ln.get('auto_buy_vendor')})
    return vendors[0] if vendors else (store.auto_buy_vendor or '')


def vendor_numbers(check) -> list[str]:
    """The real vendor order numbers of a purchase ("A, B" when several were placed)."""
    raw = (check.vendor_order_number or '').strip()
    if not raw or 'UNREAD' in raw.upper():
        return []
    return [n.strip() for n in re.split(r'[,;\s]+', raw) if n.strip()]


def current_trackings(order):
    from .models import OrderTracking

    return OrderTracking.objects.filter(order=order, replaced_at__isnull=True)


def has_user_tracking(order) -> bool:
    from .models import OrderTracking

    return current_trackings(order).filter(source=OrderTracking.Source.USER).exists()


def active_tracking_run(check):
    from stores.auto_order_bot import ACTIVE
    from stores.models import AutoOrderRun

    if not check.tracking_run_id:
        return None
    return AutoOrderRun.objects.filter(id=check.tracking_run_id, kind=AutoOrderRun.Kind.TRACKING,
                                       status__in=ACTIVE).first()


def can_get_tracking(check) -> bool:
    """Get tracking is offered for a purchased order with a vendor order number from a supported vendor."""
    from .models import OrderStatus, PurchaseStatus

    if check is None or check.purchase_status != PurchaseStatus.PURCHASED or not vendor_numbers(check):
        return False
    if check.order.status in (OrderStatus.CANCELLED, OrderStatus.REFUNDED):
        return False
    return order_vendor(check, check.order.store) in TRACKING_VENDORS


def _bought_at(check):
    return check.purchased_at or check.purchase_requested_at or check.updated_at


def due_checks(store, now=None):
    """Purchased orders of ``store`` the schedule should look up (see module docstring)."""
    from .models import OrderPurchaseCheck, OrderTracking, PurchaseStatus

    now = now or timezone.now()
    since = now - timedelta(days=TRACKING_DAYS)
    qs = (OrderPurchaseCheck.objects.select_related('order', 'order__store')
          .filter(order__store=store, purchase_status=PurchaseStatus.PURCHASED,
                  tracking_status__in=RECHECK_STATUSES)
          .exclude(order__in=OrderTracking.objects.filter(source=OrderTracking.Source.USER,
                                                          replaced_at__isnull=True).values('order')))
    out = []
    for check in qs:
        if not can_get_tracking(check) or _bought_at(check) < since:
            continue
        if active_tracking_run(check) is not None:
            continue
        out.append(check)
    return out


def write_tracking_file(checks, path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(ORDER_COLUMNS)
    for c in checks:
        bought = _bought_at(c)
        ws.append([str(c.order_id), c.order.invoice_number or c.order.external_order_key or '',
                   order_vendor(c, c.order.store), ', '.join(vendor_numbers(c)), _customer_name(c.order),
                   timezone.localtime(bought).strftime('%Y-%m-%d') if bought else ''])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def _store_tracking_run(store):
    from stores.auto_order_bot import ACTIVE
    from stores.models import AutoOrderRun

    return AutoOrderRun.objects.filter(store=store, kind=AutoOrderRun.Kind.TRACKING, status__in=ACTIVE).first()


def start_tracking(store, user=None, checks=None, *, auto=False) -> list:
    """Queue Tracking Bot jobs for ``checks`` (default: every due order of the store), one job per purchase
    account. Returns the new runs. ``auto`` (the schedule) does nothing while a tracking job of the store
    is still waiting, so jobs do not pile up while the laptop is off."""
    from .models import TrackingStatus

    if auto and _store_tracking_run(store) is not None:
        return []
    checks = due_checks(store) if checks is None else checks
    by_account = {}
    for c in checks:
        vendor = order_vendor(c, store)
        account = purchase_account_for(store, vendor)
        if account is None:
            continue
        by_account.setdefault(account.id, (account, []))[1].append(c)
    marketplace = _bot().bot_marketplace(store) or 'tracking'
    runs = []
    for account, group in by_account.values():
        run = _new_run(store, user or store.user, kind='tracking', marketplace=marketplace,
                       account_name=account.name, vendor=account.vendor)
        run_dir = Path(run.run_dir)
        write_tracking_file(group, run_dir / 'orders.xlsx')
        _bot().write_accounts_file(store.user, run_dir / 'accounts.json')
        run.message = f'{len(group)} order(s) to read tracking for' + (' (schedule)' if auto else '')
        run.save(update_fields=['message'])
        stamp = timezone.localtime().strftime('%d/%m/%Y %H:%M')
        for c in group:
            c.tracking_status = TrackingStatus.QUEUED
            c.tracking_run_id = run.id
            c.tracking_note = f'sent to the laptop {stamp}'
            c.save(update_fields=['tracking_status', 'tracking_run_id', 'tracking_note', 'updated_at'])
        runs.append(run)
        logger.info('Tracking job queued: store %s run %s account %s orders %d', store.id, run.id,
                    account.name, len(group))
    return runs


def start_order_tracking(order, user):
    """Get tracking on one order (also when it already has tracking: a later package may have shipped)."""
    from .models import OrderPurchaseCheck

    check = OrderPurchaseCheck.objects.select_related('order', 'order__store').filter(order=order).first()
    if check is None or not can_get_tracking(check):
        raise AutoPurchaseError('Get tracking is for purchased Amazon AU orders that have a vendor order number.',
                                409)
    if active_tracking_run(check) is not None:
        raise AutoPurchaseError('The Tracking Bot is already reading this order.', 409)
    runs = start_tracking(order.store, user, [check])
    if not runs:
        raise AutoPurchaseError("No purchase account for this order's vendor on the store's Auto Purchasing step.")
    return runs[0]


# ---- results ---------------------------------------------------------------------------------------------

def _rows_by_order(run) -> dict:
    out = {}
    for r in (run.done_rows or []) + (run.remaining_rows or []):
        ref = str(r.get('order_ref') or '').strip()
        if ref:
            out.setdefault(ref, []).append(r)
    return out


def _add_tracking(order, row, user=None) -> bool:
    """Save a tracking number the bot read unless the order already has it. True when added."""
    from .models import OrderTracking

    number = ' '.join(str(row.get('tracking_number') or '').split())[:255]
    if not number:
        return False
    if current_trackings(order).filter(tracking_number__iexact=number).exists():
        return False
    OrderTracking.objects.create(
        order=order, tracking_number=number, source=OrderTracking.Source.BOT, created_by=user,
        vendor_order_number=str(row.get('vendor_order_number') or '')[:255],
        carrier=' '.join(str(row.get('carrier') or '').split())[:255],
        ship_name=' '.join(str(row.get('ship_name') or '').split())[:255],
    )
    return True


def apply_tracking_result(run) -> None:
    """Laptop finished a tracking job: save the numbers it read and each order's tracking status."""
    from .models import OrderPurchaseCheck, TrackingStatus

    rows = _rows_by_order(run)
    now = timezone.localtime().strftime('%d/%m/%Y %H:%M')
    tail = ' / '.join((run.stdout or '').strip().splitlines()[-2:])[:200]
    for check in OrderPurchaseCheck.objects.select_related('order').filter(tracking_run_id=run.id):
        order = check.order
        mine = rows.get(str(order.id), [])
        notes = []
        with transaction.atomic():
            if not mine:
                status = TrackingStatus.FAILED
                notes.append(f'the laptop job {run.status}' + (f' ({tail})' if tail else '') +
                             ' before reading this order')
            else:
                statuses = {str(r.get('status') or '').strip() for r in mine}
                found = [r for r in mine if r.get('status') == 'found' and str(r.get('tracking_number') or '').strip()]
                if has_user_tracking(order):
                    notes.append('kept the tracking you entered')
                else:
                    added = sum(_add_tracking(order, r) for r in found)
                    if added:
                        notes.append(f'{added} tracking number(s) read')
                if found and 'not_shipped' in statuses:
                    status = TrackingStatus.PARTLY
                elif found:
                    status = TrackingStatus.FOUND
                elif 'not_shipped' in statuses:
                    status = TrackingStatus.NOT_SHIPPED
                elif 'cancelled' in statuses:
                    status = TrackingStatus.CANCELLED
                else:
                    status = TrackingStatus.FAILED
                for r in mine:
                    if str(r.get('name_ok') or '').lower() == 'no':
                        notes.append(f"the name on the vendor's tracking page ({r.get('ship_name')}) is not "
                                     f"the customer ({_customer_name(order)})")
                    err = str(r.get('error') or '').strip()
                    if err and err not in notes:
                        notes.append(err)
            check.tracking_status = status
            check.tracking_checked_at = timezone.now()
            check.tracking_note = f'{now}: ' + ('; '.join(dict.fromkeys(notes)) or check.get_tracking_status_display())
            check.save(update_fields=['tracking_status', 'tracking_checked_at', 'tracking_note', 'updated_at'])


def mark_stale_tracking(run) -> None:
    """A tracking job that never reported back: the orders are checked again on the next schedule."""
    apply_tracking_result(run)


# ---- user: Replace / add a tracking number ---------------------------------------------------------------

def replace_tracking(order, user, number: str, carrier: str = '', tracking_id=None):
    """The user types a tracking number: it replaces ``tracking_id`` (kept as history) or is added.
    The Tracking Bot does not change an order that has a number the user entered."""
    from .models import OrderPurchaseCheck, OrderTracking, TrackingStatus

    number = ' '.join(str(number or '').split())[:255]
    carrier = ' '.join(str(carrier or '').split())[:255]
    if not number:
        raise AutoPurchaseError('Enter the tracking number.')
    old = None
    if tracking_id:
        old = current_trackings(order).filter(id=tracking_id).first()
        if old is None:
            raise AutoPurchaseError('That tracking number was already replaced or removed. Reload the page.', 409)
    with transaction.atomic():
        new = OrderTracking.objects.create(
            order=order, tracking_number=number, carrier=carrier or (old.carrier if old else ''),
            vendor_order_number=old.vendor_order_number if old else '', source=OrderTracking.Source.USER,
            replaces=old, created_by=user,
        )
        if old is not None:
            old.replaced_at = timezone.now()
            old.replaced_by = user
            old.save(update_fields=['replaced_at', 'replaced_by'])
        check = OrderPurchaseCheck.objects.filter(order=order).first()
        if check is not None:
            check.tracking_status = TrackingStatus.FOUND
            check.tracking_note = (f"{timezone.localtime().strftime('%d/%m/%Y %H:%M')}: "
                                   + (f'you replaced {old.tracking_number} with {number}' if old
                                      else f'you entered {number}'))
            check.save(update_fields=['tracking_status', 'tracking_note', 'updated_at'])
    return new


def last_tracking_run(store) -> dict | None:
    """The store's latest tracking job for the status line: status, when, orders and numbers read."""
    from stores.models import AutoOrderRun

    run = AutoOrderRun.objects.filter(store=store, kind=AutoOrderRun.Kind.TRACKING).order_by('-started_at').first()
    if run is None:
        return None
    rows = run.done_rows or []
    from .models import OrderPurchaseCheck

    return {
        'id': str(run.id),
        'status': run.status,
        'laptop': run.laptop or '',
        'started_at': run.started_at.isoformat() if run.started_at else None,
        'finished_at': run.finished_at.isoformat() if run.finished_at else None,
        'orders': OrderPurchaseCheck.objects.filter(tracking_run_id=run.id).count(),
        'found': sum(1 for r in rows if r.get('status') == 'found' and r.get('tracking_number')),
        'error': (run.stdout or '').strip().splitlines()[-1][:300] if run.status in ('failed', 'rejected') and
        (run.stdout or '').strip() else '',
    }


def tracking_payload(order, check=None) -> dict:
    """What the Orders page shows: current numbers, replaced ones, and the bot's status for the order."""
    rows = list(order.trackings.all())

    def one(t):
        return {'id': str(t.id), 'tracking_number': t.tracking_number, 'carrier': t.carrier,
                'ship_name': t.ship_name, 'source': t.source, 'vendor_order_number': t.vendor_order_number,
                'created_at': t.created_at.isoformat() if t.created_at else None,
                'replaced_at': t.replaced_at.isoformat() if t.replaced_at else None,
                'replaces': str(t.replaces_id) if t.replaces_id else None}

    return {
        'current': [one(t) for t in rows if t.replaced_at is None],
        'replaced': [one(t) for t in rows if t.replaced_at is not None],
        'status': (check.tracking_status if check else '') or '',
        'note': (check.tracking_note if check else '') or '',
        'checked_at': check.tracking_checked_at.isoformat() if check and check.tracking_checked_at else None,
        'can_get': bool(check and can_get_tracking(check)),
        'pending': bool(check and check.tracking_status == 'queued' and active_tracking_run(check) is not None),
    }

