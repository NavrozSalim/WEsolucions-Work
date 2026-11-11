"""Auto Purchasing check: can an imported order be bought, and does it make money?

For each line of the order:
  1. Match: search the line's marketplace SKU in this store's catalog (ProductMapping).
     The mapped product gives the Vendor ID (``vendor_sku``), the vendor and the vendor URL.
     Not found -> "Not mapped"; the user maps the product and clicks Recheck.
  2. Scrape the vendor page for the current price and stock (Costway AU: latest feed price).

Then for the order (the sheet in the Auto Purchasing prompt):
  Total Sale = unit price x quantity
  Fee        = Kogan: Total Sale x the store's Marketplace Fees (%) for that vendor
               BigW:  the Commission Fee from the orders file
  After Fees = Total Sale - Fee
  Cost       = vendor price x quantity
  Earning    = After Fees - Cost

The order is ``buyable`` only when every line is mapped to the store's Auto buying
vendor, has vendor stock >= its quantity, and the order's Earning is above 0.
Nothing here buys anything.
"""
from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone

logger = logging.getLogger(__name__)

# Line results
LINE_OK = 'ok'
LINE_NOT_MAPPED = 'not_mapped'
LINE_OTHER_VENDOR = 'other_vendor'
LINE_OUT_OF_STOCK = 'out_of_stock'
LINE_STOCK_UNKNOWN = 'stock_unknown'
LINE_SCRAPE_FAILED = 'scrape_failed'

AUTO_BUY_LABELS = {
    'amazon': 'Amazon US', 'ebay': 'eBay US', 'amazon_au': 'Amazon AU',
    'ebay_au': 'eBay AU', 'costco_au': 'Costco AU', 'costway_au': 'Costway AU',
}
# Vendor.code values (and older aliases) for each Auto buying vendor.
_AUTO_BUY_CODES = {
    'amazon': {'amazonus', 'amazon', 'amazon_us', 'amazon-us'},
    'ebay': {'ebayus', 'ebay', 'ebay_us', 'ebay-us'},
    'amazon_au': {'amazonau', 'amazon_au', 'amazon-au'},
    'ebay_au': {'ebayau', 'ebay_au', 'ebay-au'},
}


def vendor_matches_auto_buy(vendor_code: str, auto_buy: str) -> bool:
    code = (vendor_code or '').strip().lower()
    if not code or not auto_buy:
        return False
    if auto_buy == 'costco_au':
        from catalog.vendor_url_resolve import is_costco_vendor_code

        return is_costco_vendor_code(code)
    if auto_buy == 'costway_au':
        from scrapers.costway_au_ingest import is_costway_vendor_code

        return is_costway_vendor_code(code)
    return code in _AUTO_BUY_CODES.get(auto_buy, set())


def _cents(value) -> int | None:
    if value is None:
        return None
    return int((Decimal(str(value)) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def _money(cents: int | None) -> str:
    if cents is None:
        return '?'
    sign = '-' if cents < 0 else ''
    return f'{sign}{abs(cents) / 100:.2f}'


# --------------------------------------------------------------------------- matching

def find_catalog_mapping(store, sku: str):
    """The store's catalog listing for a marketplace SKU (exact first, then case-insensitive)."""
    from catalog.models import ProductMapping

    sku = (sku or '').strip()
    if not sku:
        return None
    # Active listings first when the same SKU appears more than once.
    qs = (
        ProductMapping.objects.filter(store=store)
        .select_related('product', 'product__vendor')
        .order_by('-is_active', 'id')
    )
    for lookup in (
        {'marketplace_child_sku': sku},
        {'marketplace_child_sku__iexact': sku},
        {'marketplace_parent_sku__iexact': sku},
    ):
        pm = qs.filter(**lookup).first()
        if pm is not None and pm.product_id:
            return pm
    return None


def _store_fee_percent(store, vendor) -> Decimal | None:
    from stores.models import StoreVendorPriceSettings

    if vendor is None:
        return None
    pct = (
        StoreVendorPriceSettings.objects.filter(store=store, vendor=vendor)
        .values_list('marketplace_fees_percentage', flat=True)
        .first()
    )
    return None if pct is None else Decimal(str(pct))


# --------------------------------------------------------------------------- vendor price

def _feed_price_stock(product) -> dict:
    """Costway AU has no live scraper: use the latest price/stock from the Costway feed."""
    from vendor.models import VendorPrice

    vp = (
        VendorPrice.objects.filter(product=product, error_code__isnull=True)
        .order_by('-scraped_at')
        .values('price', 'stock', 'scraped_at')
        .first()
    )
    if not vp or vp['price'] is None:
        return {'error_code': 'no_feed_price', 'error_message': 'No Costway feed price yet for this product.'}
    return {'price': vp['price'], 'inventory': vp['stock'], 'source': 'feed', 'seen_at': vp['scraped_at']}


def _live_price_stock(url: str, store, vendor_code: str, session: dict) -> dict:
    from scrapers import get_price_and_stock
    from sync.tasks import _inventory_from_scrape_result

    result = get_price_and_stock(url, store.region or '', session, vendor_code=vendor_code or None) or {}
    return {
        'price': result.get('price'),
        'inventory': _inventory_from_scrape_result(result),
        'error_code': result.get('error_code'),
        'error_message': result.get('error_message'),
        'source': 'live',
    }


def _vendor_price_stock(product, url: str, store, session: dict) -> dict:
    from scrapers.costway_au_ingest import is_costway_vendor_code

    vendor_code = (product.vendor.code if product.vendor else '') or ''
    if is_costway_vendor_code(vendor_code.lower()):
        return _feed_price_stock(product)
    try:
        return _live_price_stock(url, store, vendor_code, session)
    except Exception as exc:  # scraper crashed: report it on the line, keep checking others
        logger.exception('Auto purchase check: scrape failed for %s', url[:120])
        return {'error_code': 'scrape_exception', 'error_message': str(exc)[:300]}


# --------------------------------------------------------------------------- one order

def _check_line(store, item: dict, session: dict, *, scrape: bool) -> dict:
    from sync.tasks import resolve_vendor_scrape_url

    sku = (item.get('sku') or '').strip()
    qty = int(item.get('quantity') or 1)
    unit_sale = item.get('priceCents')
    line = {
        'sku': sku,
        'title': item.get('title') or '',
        'quantity': qty,
        'unit_sale_cents': unit_sale,
        'sale_cents': None if unit_sale is None else unit_sale * qty,
        'commission_cents': item.get('commissionCents'),
        'mapping_id': None, 'vendor_code': '', 'vendor_name': '', 'vendor_id': '', 'vendor_url': '',
        'vendor_price_cents': None, 'vendor_stock': None, 'price_source': '',
        'cost_cents': None, 'fee_cents': None, 'fee_percent': None,
        'result': LINE_OK, 'message': '',
    }

    pm = find_catalog_mapping(store, sku)
    if pm is None:
        line.update(result=LINE_NOT_MAPPED, message='SKU not found in the catalog. Map the product, then Recheck.')
        return line
    product = pm.product
    vendor = product.vendor
    url = resolve_vendor_scrape_url(product, store) or ''
    line.update(
        mapping_id=str(pm.id),
        vendor_code=(vendor.code if vendor else '') or '',
        vendor_name=(vendor.name if vendor else '') or '',
        vendor_id=(product.vendor_sku or '').strip(),
        vendor_url=url,
    )

    # Fee: BigW from the file; Kogan from the store's Marketplace Fees (%) for this vendor.
    if line['commission_cents'] is not None:
        line['fee_cents'] = line['commission_cents']
    elif line['sale_cents'] is not None:
        pct = _store_fee_percent(store, vendor)
        if pct is not None:
            line['fee_percent'] = str(pct)
            line['fee_cents'] = int(
                (Decimal(line['sale_cents']) * pct / 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
            )

    if not url:
        line.update(result=LINE_NOT_MAPPED, message='Mapped product has no vendor URL. Fix the mapping, then Recheck.')
        return line
    from stores.auto_buy import auto_buy_vendor_codes, entry_for_vendor_code

    entry = entry_for_vendor_code(store, line['vendor_code'])
    if entry is None:
        buys_from = ', '.join(AUTO_BUY_LABELS.get(v, v) for v in auto_buy_vendor_codes(store)) or 'no vendor'
        line.update(
            result=LINE_OTHER_VENDOR,
            message=(
                f"Sold by {line['vendor_name'] or line['vendor_code'] or 'another vendor'}; this store buys from "
                f"{buys_from}."
            ),
        )
        return line
    line['auto_buy_vendor'] = entry[0]
    if not scrape:
        return line

    got = _vendor_price_stock(product, url, store, session)
    price, stock = got.get('price'), got.get('inventory')
    line['price_source'] = got.get('source') or ''
    line['vendor_stock'] = stock
    line['vendor_price_cents'] = _cents(price) if price is not None else None
    if line['vendor_price_cents'] is not None:
        line['cost_cents'] = line['vendor_price_cents'] * qty

    if got.get('error_code'):
        line.update(result=LINE_SCRAPE_FAILED, message=got.get('error_message') or got['error_code'])
    elif stock is not None and stock < qty:
        line.update(result=LINE_OUT_OF_STOCK, message=f'Vendor stock {stock}, order needs {qty}.')
    elif price is None:
        line.update(result=LINE_SCRAPE_FAILED, message='Vendor page showed no price.')
    elif stock is None:
        line.update(result=LINE_STOCK_UNKNOWN, message='Vendor page did not show a stock level.')
    return line


def _line_reason(line: dict) -> str:
    sku = line['sku']
    return {
        LINE_NOT_MAPPED: f'Not mapped: {sku}',
        LINE_OTHER_VENDOR: f'Other vendor: {sku} ({line["message"]})',
        LINE_OUT_OF_STOCK: f'Out of stock: {sku} ({line["message"]})',
        LINE_STOCK_UNKNOWN: f'Stock unknown: {sku}',
        LINE_SCRAPE_FAILED: f'Scrape failed: {sku} ({line["message"]})',
    }.get(line['result'], f'{sku}: {line["message"]}')


def evaluate_order(order, *, session: dict | None = None, scrape: bool = True) -> dict:
    """Work out the check for one order. Returns the fields to save on OrderPurchaseCheck."""
    store = order.store
    session = session if session is not None else {}
    items = order.line_items_json if isinstance(order.line_items_json, list) else []
    lines = [_check_line(store, it, session, scrape=scrape) for it in items if isinstance(it, dict)]

    reasons = [_line_reason(ln) for ln in lines if ln['result'] != LINE_OK]
    if not lines:
        reasons.append('Order has no line items.')

    def total(key):
        values = [ln[key] for ln in lines]
        return None if not values or any(v is None for v in values) else sum(values)

    sale, fee, cost = total('sale_cents'), total('fee_cents'), total('cost_cents')
    if lines and fee is None and not any(ln['result'] == LINE_NOT_MAPPED for ln in lines):
        missing = sorted({ln['vendor_name'] or ln['vendor_code'] for ln in lines if ln['fee_cents'] is None})
        reasons.append(
            'No marketplace fee: set Marketplace Fees (%) on the Price step for ' + ', '.join(missing or ['the vendor'])
        )
    after_fees = None if sale is None or fee is None else sale - fee
    earning = None if after_fees is None or cost is None else after_fees - cost
    # The laptop buys one vendor per job: an order split across vendors is bought by hand.
    split = sorted({ln.get('auto_buy_vendor') for ln in lines if ln.get('auto_buy_vendor')})
    if len(split) > 1:
        reasons.append(
            'Lines are from different vendors (' + ', '.join(AUTO_BUY_LABELS.get(v, v) for v in split)
            + '); buy this order by hand.'
        )
    if earning is not None and earning <= 0:
        reasons.append(f'Loss: earning {_money(earning)}')

    buyable = bool(lines) and not reasons and earning is not None and earning > 0
    return {
        'lines_json': lines,
        'reasons': reasons,
        'total_sale_cents': sale,
        'fee_cents': fee,
        'after_fees_cents': after_fees,
        'cost_cents': cost,
        'earning_cents': earning,
        'buyable': buyable,
    }


def check_order(order, *, session: dict | None = None):
    """Run the check for one order and save it. Returns the OrderPurchaseCheck."""
    from .models import OrderPurchaseCheck, PurchaseCheckStatus

    result = evaluate_order(order, session=session)
    buyable = result.pop('buyable')
    check, _ = OrderPurchaseCheck.objects.get_or_create(order=order, defaults={'store': order.store})
    for k, v in result.items():
        setattr(check, k, v)
    check.status = PurchaseCheckStatus.BUYABLE if buyable else PurchaseCheckStatus.BLOCKED
    check.checked_at = timezone.now()
    check.save()
    return check


# --------------------------------------------------------------------------- queueing

def mark_checking(store, order_ids) -> list:
    """Set the orders' checks to Checking (creating them) and return the ids to check."""
    from .models import PURCHASE_LOCKED, MarketplaceOrder, OrderPurchaseCheck, PurchaseCheckStatus

    now = timezone.now()
    # Orders being bought or already bought keep their check as it was.
    ids = [
        str(i) for i in MarketplaceOrder.objects.filter(store=store, id__in=list(order_ids))
        .exclude(purchase_check__purchase_status__in=PURCHASE_LOCKED)
        .values_list('id', flat=True)
    ]
    existing = set(
        str(i) for i in OrderPurchaseCheck.objects.filter(order_id__in=ids).values_list('order_id', flat=True)
    )
    OrderPurchaseCheck.objects.filter(order_id__in=ids).update(
        status=PurchaseCheckStatus.CHECKING, requested_at=now,
    )
    OrderPurchaseCheck.objects.bulk_create([
        OrderPurchaseCheck(order_id=i, store=store, status=PurchaseCheckStatus.CHECKING, requested_at=now)
        for i in ids if i not in existing
    ])
    return ids


def queue_purchase_checks(store, order_ids) -> dict:
    """Mark the orders Checking and send them to the scrape worker for the store's region."""
    from django.db import transaction

    from .models import OrderPurchaseCheck, PurchaseCheckStatus

    ids = mark_checking(store, order_ids)
    if not ids:
        return {'queued': 0}

    def _send():
        from .tasks import check_auto_purchase_orders

        try:
            check_auto_purchase_orders.apply_async(args=[str(store.id), ids])
        except Exception:
            logger.exception('Could not queue Auto Purchasing check for store %s', store.id)
            OrderPurchaseCheck.objects.filter(order_id__in=ids, status=PurchaseCheckStatus.CHECKING).update(
                status=PurchaseCheckStatus.BLOCKED,
                reasons=['Could not start the check (worker queue unavailable). Click Recheck.'],
            )

    transaction.on_commit(_send)
    return {'queued': len(ids)}


def run_purchase_checks(store_id: str, order_ids) -> dict:
    """Worker side: check each order in turn with one shared scraper session."""
    from scrapers import close_amazon_session

    from .models import MarketplaceOrder

    orders = list(
        MarketplaceOrder.objects.filter(store_id=store_id, id__in=list(order_ids)).select_related('store')
    )
    session: dict = {}
    if orders and orders[0].store.user_id:
        session['aliexpress_user_id'] = str(orders[0].store.user_id)
    counts = {'checked': 0, 'buyable': 0, 'blocked': 0}
    try:
        for order in orders:
            from django.db import close_old_connections

            close_old_connections()
            try:
                check = check_order(order, session=session)
            except Exception as exc:
                logger.exception('Auto purchase check failed for order %s', order.id)
                _save_failure(order, str(exc))
                counts['blocked'] += 1
                continue
            counts['checked'] += 1
            counts['buyable' if check.status == 'buyable' else 'blocked'] += 1
    finally:
        close_amazon_session(session)
    return counts


def _save_failure(order, message: str) -> None:
    from .models import OrderPurchaseCheck, PurchaseCheckStatus

    OrderPurchaseCheck.objects.update_or_create(
        order=order,
        defaults={
            'store': order.store,
            'status': PurchaseCheckStatus.BLOCKED,
            'reasons': [f'Check failed: {message[:300]}. Click Recheck.'],
            'checked_at': timezone.now(),
        },
    )


def check_payload(check) -> dict | None:
    """What the Orders page shows for an order's check."""
    if check is None:
        return None
    return {
        'status': check.status,
        'reasons': check.reasons or [],
        'lines': check.lines_json or [],
        'total_sale_cents': check.total_sale_cents,
        'fee_cents': check.fee_cents,
        'after_fees_cents': check.after_fees_cents,
        'cost_cents': check.cost_cents,
        'earning_cents': check.earning_cents,
        'requested_at': check.requested_at.isoformat() if check.requested_at else None,
        'checked_at': check.checked_at.isoformat() if check.checked_at else None,
        'purchase_status': check.purchase_status or '',
        'vendor_order_number': check.vendor_order_number or '',
        'purchase_error': check.purchase_error or '',
        'purchase_requested_at': check.purchase_requested_at.isoformat() if check.purchase_requested_at else None,
        'purchased_at': check.purchased_at.isoformat() if check.purchased_at else None,
    }
