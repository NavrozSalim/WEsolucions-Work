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

def purchase_account_for(store):
    from stores.models import PurchaseAccount

    return (
        PurchaseAccount.objects.filter(user=store.user, vendor=store.auto_buy_vendor, region=store.region)
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


def start_purchase(order, user):
    """User clicked Process: validate, lock the order and queue the purchase for the laptop."""
    from stores.models import AutoOrderRun

    from .models import OrderPurchaseCheck, PurchaseCheckStatus, PurchaseStatus, PURCHASE_LOCKED

    store = order.store
    if not store.is_auto_purchase:
        raise AutoPurchaseError("Choose an Auto buying vendor on the store's Auto Purchasing step first.")
    marketplace = bot_marketplace_or_error(store)
    account = purchase_account_for(store)
    if account is None:
        from listings.auto_purchase_check import AUTO_BUY_LABELS

        raise AutoPurchaseError(
            f'Add a purchase account for {AUTO_BUY_LABELS.get(store.auto_buy_vendor, store.auto_buy_vendor)} '
            f'on the Auto Order Bot page first.'
        )
    with transaction.atomic():
        check = OrderPurchaseCheck.objects.select_for_update().filter(order=order).first()
        if check is None or check.status != PurchaseCheckStatus.BUYABLE:
            raise AutoPurchaseError('This order has not passed the stock and profit check.', 409)
        if check.purchase_status in PURCHASE_LOCKED:
            raise AutoPurchaseError('This order is already being bought or was bought.', 409)
        if not check.checked_at or timezone.now() - check.checked_at > max_check_age():
            raise AutoPurchaseError('The check is too old. Click Recheck, then Process.', 409)
        lines = check.lines_json or []
        if not lines or any(not ln.get('vendor_url') or ln.get('vendor_price_cents') is None for ln in lines):
            raise AutoPurchaseError('A line has no vendor URL or price. Click Recheck.', 409)

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
        check.save(update_fields=[
            'purchase_status', 'purchase_run_id', 'purchase_requested_at', 'purchase_requested_by',
            'purchase_error', 'vendor_order_number', 'purchased_at', 'updated_at',
        ])
    logger.info('Auto purchase queued: order %s run %s account %s', order.id, run.id, account.name)
    return run


def mark_purchasing(run) -> None:
    """The laptop claimed the purchase job."""
    from .models import OrderPurchaseCheck, PurchaseStatus

    OrderPurchaseCheck.objects.filter(purchase_run_id=run.id, purchase_status=PurchaseStatus.QUEUED).update(
        purchase_status=PurchaseStatus.PURCHASING,
    )


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
    for s in wanted:
        r = by_sku.get(s)
        if r is None:
            problems.append(f'{s}: no result from the laptop')
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
        check.purchase_status = PurchaseStatus.FAILED
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
