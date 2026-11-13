"""Auto Purchasing laptop jobs: Fetch (download the orders file) and Process (buy one order).

Both are ``stores.AutoOrderRun`` rows picked up by the worker laptop through the
order-bot ingest endpoints:

* ``fetch_orders``: the laptop opens the Kogan / BigW seller portal in its signed-in
  Chrome profile, downloads the open-orders file and uploads it; the server imports it
  (``order_file_import``) and checks every order (``auto_purchase_check``).
* ``purchase``: only after the user clicks Process on an order that passed the check.
  The server writes a one-order orders file, a mapping with each line's vendor URL and
  the checked vendor price as ``Max Price``, and the purchase-accounts file; the laptop
  runs the existing order bot with ``--place``. The bot stops before paying when the
  vendor price is above ``Max Price``.

Nothing is bought without a click, and an order is never sent to buy twice.
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import openpyxl
from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

# Order-file formats the server can rebuild for the bot (bot marketplace argument).
SUPPORTED_MARKETPLACES = ('kogan', 'bigw')
JOB_KINDS_FOR_AGENT = ('fetch_orders', 'purchase')


class AutoPurchaseError(Exception):
    """Shown to the user as-is; ``status_code`` is the HTTP status for the API."""

    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def max_check_age() -> timedelta:
    try:
        minutes = int(getattr(settings, 'AUTO_PURCHASE_MAX_CHECK_AGE_MINUTES', 120) or 120)
    except (TypeError, ValueError):
        minutes = 120
    return timedelta(minutes=max(5, minutes))


def _bot():
    from stores import auto_order_bot as bot

    return bot


def bot_marketplace_or_error(store) -> str:
    mp = _bot().bot_marketplace(store)
    if mp not in SUPPORTED_MARKETPLACES:
        raise AutoPurchaseError('Auto Purchasing fetches and buys Kogan and BigW (MyDeal) orders only for now.')
    return mp


def _new_run(store, user, *, kind, marketplace, account_name='', vendor='', order_id=None, place=False):
    from stores.models import AutoOrderRun

    run = AutoOrderRun.objects.create(
        store=store, user=user, kind=kind, marketplace=marketplace,
        account_name=account_name, vendor=vendor, place=place,
        marketplace_order_id=order_id, status=AutoOrderRun.Status.PENDING,
    )
    run_dir = _bot().store_dir(store) / 'runs' / str(run.id)
    (run_dir / 'output').mkdir(parents=True, exist_ok=True)
    run.run_dir = str(run_dir)
    run.save(update_fields=['run_dir'])
    return run


def run_payload(run) -> dict | None:
    if run is None:
        return None
    return {
        'id': str(run.id),
        'kind': run.kind,
        'status': run.status,
        'message': run.message or '',
        'laptop': run.laptop or '',
        'started_at': run.started_at.isoformat() if run.started_at else None,
        'claimed_at': run.claimed_at.isoformat() if run.claimed_at else None,
        'finished_at': run.finished_at.isoformat() if run.finished_at else None,
        'error': (run.stdout or '')[-600:] if run.status in ('failed', 'rejected') else '',
    }


# --------------------------------------------------------------------------- Fetch

def latest_fetch(store):
    from stores.models import AutoOrderRun

    return AutoOrderRun.objects.filter(store=store, kind=AutoOrderRun.Kind.FETCH_ORDERS).order_by('-started_at').first()


def start_fetch(store, user):
    """Queue a Fetch for the laptop (one at a time per store)."""
    from stores.models import AutoOrderRun

    if not store.is_auto_purchase:
        raise AutoPurchaseError("Choose an Auto buying vendor on the store's Auto Purchasing step first.")
    marketplace = bot_marketplace_or_error(store)
    if _bot().runner_mode() == 'local':
        raise AutoPurchaseError('Fetch needs the worker laptop (AUTO_ORDER_BOT_RUNNER is set to local).')
    with transaction.atomic():
        active = (
            AutoOrderRun.objects.select_for_update()
            .filter(store=store, kind=AutoOrderRun.Kind.FETCH_ORDERS, status__in=_bot().ACTIVE)
            .first()
        )
        if active:
            return active, False
        return _new_run(store, user, kind=AutoOrderRun.Kind.FETCH_ORDERS, marketplace=marketplace), True


def import_fetched_file(run, filename: str, data: bytes) -> dict:
    """Laptop uploaded the portal's orders file: import it and check every order."""
    from .auto_purchase_check import queue_purchase_checks
    from .order_file_import import import_order_file

    result = import_order_file(run.store, run.user, filename, data)
    queued = queue_purchase_checks(run.store, result.get('order_ids') or [])
    if queued.get('queued'):
        result['message'] += f" Checking stock and profit for {queued['queued']} order(s)."
    run.message = f"Fetched {filename}. {result['message']}"
    run.save(update_fields=['message'])
    return result


# --------------------------------------------------------------------------- Process

def purchase_account_for(store, vendor=None):
    """The account set for ``vendor`` on the store's Auto Purchasing step; otherwise the owner's first one.

    ``vendor`` defaults to the store's first Auto buying vendor.
    """
    from stores.auto_buy import auto_buy_entries
    from stores.models import PurchaseAccount

    entries = auto_buy_entries(store)
    vendor = vendor or (entries[0][0] if entries else store.auto_buy_vendor)
    for v, linked in entries:
        if v == vendor and linked is not None and linked.vendor == vendor and linked.region == store.region:
            return linked
    return (
        PurchaseAccount.objects.filter(user=store.user, vendor=vendor, region=store.region)
        .order_by('created_at', 'id')
        .first()
    )


def _money(cents) -> str:
    return f'{Decimal(cents) / 100:.2f}'


def write_order_file(order, marketplace: str, path: Path) -> None:
    """The one order, in the marketplace's export layout the order bot reads."""
    raw = order.raw_response_json if isinstance(order.raw_response_json, dict) else {}
    cust = raw.get('customer') or order.customer_info_json or {}
    addr = cust.get('shippingAddress') or {}
    items = order.line_items_json or []
    wb = openpyxl.Workbook()
    ws = wb.active
    if marketplace == 'kogan':
        headers = ['OrderID', 'ProductCode', 'DeliveryName', 'DeliverySuburb', 'Quantity', 'Product Description',
                   'DeliveryAddress1', 'DeliveryAddress2', 'DeliveryState', 'DeliveryPostCode', 'DestCountry',
                   'DeliveryPhone']
        ws.append(headers)
        for it in items:
            ws.append([order.invoice_number, it.get('sku'), cust.get('name'), addr.get('city'), it.get('quantity'),
                       it.get('title'), addr.get('line1'), addr.get('line2'), addr.get('state'), addr.get('postcode'),
                       addr.get('country') or 'AU', cust.get('phone')])
    else:  # bigw
        headers = ['Order No', 'SKU', 'Order Group No', 'Source', 'Quantity', 'Product Name', 'Status', 'Name',
                   'Company Name', 'Address', 'Address 2', 'Suburb', 'Postcode', 'State', 'Phone']
        ws.append(headers)
        for it in items:
            ws.append([order.invoice_number, it.get('sku'), raw.get('orderGroupNo') or '', raw.get('marketplace') or 'BigW',
                       it.get('quantity'), it.get('title'), 'Purchased', cust.get('name'),
                       cust.get('company') or addr.get('company') or '', addr.get('line1'), addr.get('line2'),
                       addr.get('city'), addr.get('postcode'), addr.get('state'), cust.get('phone')])
    wb.save(path)


def write_mapping_file(check, vendor: str, path: Path) -> None:
    """SKU | Vendor | Vendor URL | Vendor SKU | Max Price (checked vendor price per unit)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['SKU', 'Vendor', 'Vendor URL', 'Vendor SKU', 'Max Price'])
    for ln in check.lines_json or []:
        ws.append([ln['sku'], vendor, ln['vendor_url'], ln.get('vendor_id') or '', _money(ln['vendor_price_cents'])])
    wb.save(path)


def start_purchase(order, user, *, force=False):
    """User clicked Process: validate, lock the order and queue the purchase for the laptop.

    ``force`` (Force process) also buys an order whose only problem is a loss.
    """
    from stores.models import AutoOrderRun

    from .auto_purchase_check import is_loss_only
    from .models import OrderPurchaseCheck, PurchaseCheckStatus, PurchaseStatus, PURCHASE_LOCKED

    store = order.store
    if order.is_history:
        raise AutoPurchaseError('This is an old order uploaded for the record; it is not bought.', 409)
    if not store.is_auto_purchase:
        raise AutoPurchaseError("Choose an Auto buying vendor on the store's Auto Purchasing step first.")
    marketplace = bot_marketplace_or_error(store)
    with transaction.atomic():
        check = OrderPurchaseCheck.objects.select_for_update().filter(order=order).first()
        forced = False
        if check is not None and force and check.status != PurchaseCheckStatus.BUYABLE:
            if not is_loss_only(check):
                raise AutoPurchaseError(
                    'Force process only buys an order whose only problem is a loss. Fix the other problems first.',
                    409,
                )
            forced = True
        elif check is None or check.status != PurchaseCheckStatus.BUYABLE:
            raise AutoPurchaseError('This order has not passed the stock and profit check.', 409)
        if check.purchase_status in PURCHASE_LOCKED:
            raise AutoPurchaseError('This order is already being bought or was bought.', 409)
        if not check.checked_at or timezone.now() - check.checked_at > max_check_age():
            raise AutoPurchaseError('The check is too old. Click Recheck, then Process.', 409)
        lines = check.lines_json or []
        if not lines or any(not ln.get('vendor_url') or ln.get('vendor_price_cents') is None for ln in lines):
            raise AutoPurchaseError('A line has no vendor URL or price. Click Recheck.', 409)
        vendors = {ln.get('auto_buy_vendor') or store.auto_buy_vendor for ln in lines}
        if len(vendors) != 1:
            raise AutoPurchaseError('The lines are from different vendors; buy this order by hand.', 409)
        vendor = vendors.pop()
        account = purchase_account_for(store, vendor)
        if account is None:
            from listings.auto_purchase_check import AUTO_BUY_LABELS

            raise AutoPurchaseError(
                f'Add a purchase account for {AUTO_BUY_LABELS.get(vendor, vendor)} '
                f"on the store's Auto Purchasing step first (Store Settings → Edit)."
            )

        run = _new_run(
            store, user, kind=AutoOrderRun.Kind.PURCHASE, marketplace=marketplace,
            account_name=account.name, vendor=account.vendor, order_id=order.id, place=True,
        )
        run_dir = Path(run.run_dir)
        write_order_file(order, marketplace, run_dir / 'orders.xlsx')
        write_mapping_file(check, account.vendor, run_dir / 'mapping.xlsx')
        _bot().write_accounts_file(store.user, run_dir / 'accounts.json')
        run.command = json.dumps(_bot().build_command(
            store, marketplace, account.name, run_dir / 'orders.xlsx', run_dir / 'mapping.xlsx',
            run_dir / 'accounts.json', run_dir / 'output', True,
        ))
        run.save(update_fields=['command'])

        check.purchase_status = PurchaseStatus.QUEUED
        check.purchase_run_id = run.id
        check.purchase_requested_at = timezone.now()
        check.purchase_requested_by = user
        check.purchase_error = ''
        check.vendor_order_number = ''
        check.purchased_at = None
        check.forced_at = timezone.now() if forced else None
        check.forced_loss_cents = check.earning_cents if forced else None
        check.save(update_fields=[
            'purchase_status', 'purchase_run_id', 'purchase_requested_at', 'purchase_requested_by',
            'purchase_error', 'vendor_order_number', 'purchased_at', 'forced_at', 'forced_loss_cents',
            'updated_at',
        ])
    logger.info('Auto purchase queued: order %s run %s account %s', order.id, run.id, account.name)
    return run


def process_all(store, user) -> dict:
    """Process all orders: queue every order that is ready to buy (in profit, no errors, check fresh).

    Loss orders, blocked orders, old (history) orders and orders already queued or bought are skipped.
    The laptop buys the queued orders one at a time.
    """
    from .models import PURCHASE_LOCKED, MarketplaceOrder, PurchaseCheckStatus

    if not store.is_auto_purchase:
        raise AutoPurchaseError("Choose an Auto buying vendor on the store's Auto Purchasing step first.")
    bot_marketplace_or_error(store)
    candidates = list(
        MarketplaceOrder.objects.filter(
            store=store, is_history=False, purchase_check__status=PurchaseCheckStatus.BUYABLE,
        )
        .exclude(purchase_check__purchase_status__in=PURCHASE_LOCKED)
        .exclude(status__in=['cancelled', 'refunded', 'shipping_complete'])
        .select_related('purchase_check')
        .order_by('created_at')
    )
    queued, failed = [], []
    for order in candidates:
        try:
            start_purchase(order, user)
            queued.append(order.invoice_number or str(order.id))
        except AutoPurchaseError as exc:
            failed.append(f'{order.invoice_number or order.id}: {exc}')
    msg = f'{len(queued)} order(s) sent to the laptop to buy.'
    if failed:
        msg += f' {len(failed)} not sent: ' + '; '.join(failed[:5]) + (' ...' if len(failed) > 5 else '')
    return {'queued': len(queued), 'not_sent': failed, 'message': msg}


def mark_purchasing(run) -> None:
    """The laptop claimed the purchase job."""
    from .models import OrderPurchaseCheck, PurchaseStatus

    OrderPurchaseCheck.objects.filter(purchase_run_id=run.id, purchase_status=PurchaseStatus.QUEUED).update(
        purchase_status=PurchaseStatus.PURCHASING,
    )


UNREAD_NOTE = 'order placed but number not read'


def set_vendor_order_number(order, number: str):
    """User typed the vendor's order number on a purchased order (the bot could not read it)."""
    from .models import OrderPurchaseCheck, PurchaseStatus

    number = ' '.join(str(number or '').split())[:255]
    if not number:
        raise AutoPurchaseError('Enter the vendor order number.')
    check = OrderPurchaseCheck.objects.filter(order=order).first()
    if check is None or check.purchase_status != PurchaseStatus.PURCHASED:
        raise AutoPurchaseError('Only a purchased order has a vendor order number.', status_code=409)
    check.vendor_order_number = number
    # Drop the bot's "number not read" notes; keep anything else (e.g. only some items bought).
    parts = [p for p in (check.purchase_error or '').split('; ') if p and UNREAD_NOTE not in p]
    check.purchase_error = '; '.join(parts)
    check.save(update_fields=['vendor_order_number', 'purchase_error', 'updated_at'])
    return check


LOOKUP_NOTE = 'Order ID recheck'


def needs_order_number(check) -> bool:
    """A processed order (bought, or failed/stopped after it was sent) with no real vendor order number."""
    from .models import PurchaseStatus

    if check is None or not check.purchase_run_id:
        return False
    if check.purchase_status not in (PurchaseStatus.PURCHASED, PurchaseStatus.FAILED):
        return False
    number = (check.vendor_order_number or '').strip()
    return not number or 'UNREAD' in number.upper()


def active_lookup(order_id):
    from stores.auto_order_bot import ACTIVE
    from stores.models import AutoOrderRun

    return AutoOrderRun.objects.filter(
        kind=AutoOrderRun.Kind.LOOKUP_ORDER, marketplace_order_id=order_id, status__in=ACTIVE,
    ).order_by('-started_at').first()


def _set_lookup_note(check, text):
    parts = [p for p in (check.purchase_error or '').split('; ') if p and not p.startswith(LOOKUP_NOTE)]
    check.purchase_error = '; '.join(parts + ([f'{LOOKUP_NOTE}: {text}'] if text else []))[:1000]


def start_order_lookup(order, user):
    """Recheck order ID: the laptop opens the vendor's order list in the purchase account and finds this
    order's number (by product title and customer name). Nothing is bought."""
    from stores.models import AutoOrderRun

    from .models import OrderPurchaseCheck

    store = order.store
    check = OrderPurchaseCheck.objects.filter(order=order).first()
    if not needs_order_number(check):
        raise AutoPurchaseError('Recheck order ID is for processed orders that have no vendor order number.', 409)
    if active_lookup(order.id) is not None:
        raise AutoPurchaseError('The laptop is already looking up this order ID.', 409)
    marketplace = bot_marketplace_or_error(store)
    lines = check.lines_json or []
    vendors = {ln.get('auto_buy_vendor') or store.auto_buy_vendor for ln in lines} or {store.auto_buy_vendor}
    vendor = sorted(v for v in vendors if v)[0] if any(vendors) else ''
    account = purchase_account_for(store, vendor) if vendor else None
    if account is None:
        raise AutoPurchaseError("No purchase account for this order's vendor on the store's Auto Purchasing step.")
    run = _new_run(store, user, kind=AutoOrderRun.Kind.LOOKUP_ORDER, marketplace=marketplace,
                   account_name=account.name, vendor=account.vendor, order_id=order.id, place=False)
    run_dir = Path(run.run_dir)
    write_order_file(order, marketplace, run_dir / 'orders.xlsx')
    write_mapping_file(check, account.vendor, run_dir / 'mapping.xlsx')
    _bot().write_accounts_file(store.user, run_dir / 'accounts.json')
    run.command = json.dumps(_bot().build_command(
        store, marketplace, account.name, run_dir / 'orders.xlsx', run_dir / 'mapping.xlsx',
        run_dir / 'accounts.json', run_dir / 'output', False,
    ) + ['--lookup-orders'])
    run.save(update_fields=['command'])
    _set_lookup_note(check, f"sent to the laptop {timezone.localtime().strftime('%d/%m/%Y %H:%M')}")
    check.save(update_fields=['purchase_error', 'updated_at'])
    logger.info('Order ID lookup queued: order %s run %s account %s', order.id, run.id, account.name)
    return run


def apply_lookup_result(run) -> None:
    """Laptop finished Recheck order ID: save the order number it found (the order was placed), or say not found."""
    from .models import MarketplaceOrder, OrderPurchaseCheck, PurchaseStatus

    order = MarketplaceOrder.objects.filter(id=run.marketplace_order_id).first()
    check = OrderPurchaseCheck.objects.filter(order=order).first() if order else None
    if check is None:
        return
    invoice = (order.invoice_number or '').strip()
    rows = [r for r in (run.done_rows or []) + (run.remaining_rows or [])
            if str(r.get('order_id') or '').strip() == invoice]
    numbers = []
    for r in rows:
        n = str(r.get('vendor_order_number') or '').strip()
        if r.get('status') == 'order_found' and n and n not in numbers:
            numbers.append(n)
    now = timezone.localtime().strftime('%d/%m/%Y %H:%M')
    if numbers:
        was_failed = check.purchase_status == PurchaseStatus.FAILED
        check.vendor_order_number = ', '.join(numbers)[:255]
        check.purchase_status = PurchaseStatus.PURCHASED
        check.purchased_at = check.purchased_at or timezone.now()
        keep = [p for p in (check.purchase_error or '').split('; ')
                if p and UNREAD_NOTE not in p and not p.startswith(LOOKUP_NOTE)
                and not p.startswith(STOPPED_PREFIX)]
        check.purchase_error = '; '.join(keep)
        _set_lookup_note(check, f"found {check.vendor_order_number} in the vendor's orders ({now})"
                         + ('; the order had been placed before it was stopped' if was_failed else ''))
    else:
        why = '; '.join(str(r.get('error') or r.get('status') or '') for r in rows if r.get('error'))
        if not rows:
            tail = ' / '.join((run.stdout or '').strip().splitlines()[-2:])
            why = f'the laptop job {run.status}' + (f' ({tail[:200]})' if tail else '')
        why = why or "no matching order in the vendor's order list"
        _set_lookup_note(check, f'not found ({now}): {why}')
    check.save(update_fields=['vendor_order_number', 'purchase_status', 'purchased_at', 'purchase_error',
                              'updated_at'])


STOPPED_PREFIX = 'Stopped by you'


def stop_purchase(order, user):
    """User clicked Stop: cancel this order's laptop run. The laptop kills the bot within a few seconds.

    Anything the bot finished before it stopped is still recorded when the laptop reports back
    (a line already paid for stays Purchased, so it cannot be bought twice).
    """
    from stores.auto_order_bot import ACTIVE
    from stores.models import AutoOrderRun

    from .models import OrderPurchaseCheck, PurchaseStatus

    check = OrderPurchaseCheck.objects.filter(order=order).first()
    if check is None or check.purchase_status not in (PurchaseStatus.QUEUED, PurchaseStatus.PURCHASING):
        raise AutoPurchaseError('This order is not being bought.', status_code=409)
    run = AutoOrderRun.objects.filter(id=check.purchase_run_id).first() if check.purchase_run_id else None
    was_running = bool(run and run.status == AutoOrderRun.Status.RUNNING)
    if run is not None and run.status in ACTIVE:
        run.status = AutoOrderRun.Status.CANCELLED
        run.finished_at = timezone.now()
        run.save(update_fields=['status', 'finished_at'])
    who = (getattr(user, 'get_full_name', lambda: '')() or getattr(user, 'email', '') or '').strip()
    check.purchase_status = PurchaseStatus.FAILED
    check.purchase_error = (
        f"{STOPPED_PREFIX}{' (' + who + ')' if who else ''} at {timezone.localtime().strftime('%d/%m/%Y %H:%M')}. "
        + ('The laptop bot is being stopped; if it had already paid, the order shows as Purchased in a moment '
           'and is not bought again. Check the vendor account before processing again.'
           if was_running else 'It had not started on the laptop yet.')
    )[:1000]
    check.save(update_fields=['purchase_status', 'purchase_error', 'updated_at'])
    return check


def apply_purchase_result(run) -> None:
    """Called when a purchase job finishes: copy the bot's per-line result onto the order."""
    from .models import MarketplaceOrder, OrderPurchaseCheck, PurchaseStatus

    check = OrderPurchaseCheck.objects.filter(purchase_run_id=run.id).first()
    if check is None:
        return
    order = MarketplaceOrder.objects.filter(id=run.marketplace_order_id).first()
    invoice = (order.invoice_number if order else '') or ''
    rows = [
        r for r in (run.done_rows or []) + (run.remaining_rows or [])
        if str(r.get('order_id') or '').strip() == invoice.strip()
    ]
    by_sku = {str(r.get('sku') or '').strip().upper(): r for r in rows}
    wanted = [str(ln.get('sku') or '').strip().upper() for ln in check.lines_json or []]
    bought = [s for s in wanted if (by_sku.get(s) or {}).get('status') == 'purchased']
    numbers = []
    for s in bought:
        n = str(by_sku[s].get('vendor_order_number') or '').strip()
        if n and n not in numbers:
            numbers.append(n)
    problems = []
    tail = ' / '.join((run.stdout or '').strip().splitlines()[-3:])
    for s in wanted:
        r = by_sku.get(s)
        if r is None:
            problems.append(f'{s}: no result from the laptop')
        elif not str(r.get('status') or '').strip():
            # The bot stopped in the middle of this line (crash, closed browser): say so with its last log lines.
            problems.append(f'{s}: the laptop bot stopped before finishing this item'
                            + (f' ({tail[:400]})' if tail else ''))
        elif r.get('status') != 'purchased':
            problems.append(f"{s}: {r.get('status')}" + (f" ({r.get('error')})" if r.get('error') else ''))
        elif r.get('error'):
            problems.append(f"{s}: {r.get('error')}")
    if not rows and run.status in ('failed', 'rejected', 'cancelled'):
        tail = (run.stdout or '').strip().splitlines()[-3:]
        problems.append(f'Laptop job {run.status}' + (f": {' / '.join(tail)}" if tail else ''))

    check.vendor_order_number = ', '.join(numbers)[:255]
    if bought and len(bought) == len(wanted):
        check.purchase_status = PurchaseStatus.PURCHASED
        check.purchased_at = timezone.now()
        check.purchase_error = '; '.join(problems)
    elif bought:
        # Some items were bought: lock the order so the rest is not bought twice by mistake.
        check.purchase_status = PurchaseStatus.PURCHASED
        check.purchased_at = timezone.now()
        check.purchase_error = 'Only some items were bought; buy the rest by hand. ' + '; '.join(problems)
    else:
        stopped = (check.purchase_error or '').startswith(STOPPED_PREFIX)
        check.purchase_status = PurchaseStatus.FAILED
        if stopped:  # keep the user's Stop message; add what the bot reported
            check.purchase_error = (check.purchase_error.split(' Bot: ')[0] + ' Bot: '
                                    + ('; '.join(problems) or 'nothing bought.'))[:1000]
        else:
            check.purchase_error = '; '.join(problems) or 'The laptop did not buy this order.'
    check.save(update_fields=['purchase_status', 'purchased_at', 'purchase_error', 'vendor_order_number', 'updated_at'])
    logger.info('Auto purchase result order %s: %s %s', run.marketplace_order_id, check.purchase_status,
                check.vendor_order_number)


def mark_stale_purchase(run) -> None:
    """A purchase job that never reported back. It may have bought: say so before allowing Process again."""
    from .models import OrderPurchaseCheck, PurchaseStatus

    OrderPurchaseCheck.objects.filter(
        purchase_run_id=run.id, purchase_status__in=[PurchaseStatus.QUEUED, PurchaseStatus.PURCHASING],
    ).update(
        purchase_status=PurchaseStatus.FAILED,
        purchase_error=(
            'No result came back from the laptop. Check the vendor account for this order '
            'before you click Process again.'
        ),
    )
