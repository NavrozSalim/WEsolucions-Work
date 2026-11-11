"""Read Kogan / BigW order exports into MarketplaceOrder rows (Auto Purchasing).

Auto-purchase stores get their orders from the marketplace's order file, not an API:
the worker laptop downloads it (Fetch) or the user uploads it. This module reads the
file, groups rows into one order per order ID, and upserts the orders so the Orders
page shows them in the same layout as API orders.

Headers are detected the same way the order bot does (``core/order_files.py``).
Kogan ``ItemPrice`` is read as the price of one unit.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

DETECT = {
    'kogan': ['OrderID', 'ProductCode', 'DeliveryName', 'DeliverySuburb'],
    'bigw': ['Order No', 'SKU', 'Order Group No', 'Source'],
}
REQUIRED = {
    'kogan': DETECT['kogan'] + [
        'Quantity', 'Product Description', 'DeliveryAddress1', 'DeliveryAddress2',
        'DeliveryState', 'DeliveryPostCode', 'DeliveryPhone', 'ItemPrice',
    ],
    'bigw': DETECT['bigw'] + [
        'Quantity', 'Product Name', 'Status', 'Name', 'Company Name', 'Address', 'Address 2',
        'Suburb', 'Postcode', 'State', 'Phone', 'Price(Per Unit)', 'Commission Fee',
    ],
}
FORMAT_LABEL = {'kogan': 'Kogan', 'bigw': 'BigW'}
# Marketplace kinds (stores.credentials.marketplace_kind) each file format belongs to.
# BigW orders come from MyDeal stores (MyDeal lists on BigW and Everyday Market).
FORMAT_MARKETPLACES = {'kogan': ('kogan',), 'bigw': ('mydeal', 'bigw')}
# BigW lines in any other status are not open orders and are skipped.
BIGW_OPEN_STATUS = 'purchased'
MAX_FILE_BYTES = 10 * 1024 * 1024
SOURCE = 'order_file'


class OrderFileError(ValueError):
    """The file cannot be imported; the message is shown to the user."""


@dataclass
class ParsedOrder:
    order_id: str
    marketplace: str
    customer: dict
    lines: list = field(default_factory=list)
    ordered_at: str | None = None
    status_raw: str = ''
    extra: dict = field(default_factory=dict)

    @property
    def subtotal(self) -> Decimal:
        return sum((ln['unit_price'] * ln['quantity'] for ln in self.lines), Decimal('0'))

    @property
    def shipping(self) -> Decimal:
        return sum((ln.get('shipping') or Decimal('0') for ln in self.lines), Decimal('0'))

    @property
    def total(self) -> Decimal:
        return self.subtotal + self.shipping

    @property
    def commission(self) -> Decimal | None:
        fees = [ln['commission'] for ln in self.lines if ln.get('commission') is not None]
        return sum(fees, Decimal('0')) if fees else None


@dataclass
class ParsedFile:
    file_format: str
    orders: list
    line_count: int
    skipped: list  # (row number, reason)


# --------------------------------------------------------------------------- cells

def _cell(v) -> str:
    if v is None:
        return ''
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v).strip()


def _money(raw: str, what: str, rownum: int, *, required: bool = True) -> Decimal | None:
    s = (raw or '').replace('$', '').replace(',', '').strip()
    if not s:
        if required:
            raise OrderFileError(f'Row {rownum}: {what} is blank.')
        return None
    try:
        value = Decimal(s)
    except InvalidOperation:
        raise OrderFileError(f'Row {rownum}: {what} "{raw}" is not a number.') from None
    if value < 0:
        raise OrderFileError(f'Row {rownum}: {what} "{raw}" is negative.')
    return value.quantize(Decimal('0.01'))


def _qty(raw: str, rownum: int) -> int:
    try:
        f = float((raw or '').strip())
    except ValueError:
        raise OrderFileError(f'Row {rownum}: Quantity "{raw}" is not a number.') from None
    if f != int(f) or f < 1:
        raise OrderFileError(f'Row {rownum}: Quantity "{raw}" must be a whole number of at least 1.')
    return int(f)


def _day(raw: str) -> str | None:
    """Order dates are day-first in both exports (Kogan 30-10-2026, BigW 09/11/2026)."""
    s = (raw or '').strip()
    if not s:
        return None
    for fmt in ('%d-%m-%Y', '%d/%m/%Y', '%Y-%m-%d', '%Y-%m-%dT%H:%M:%S', '%d/%m/%Y %H:%M', '%d-%m-%Y %H:%M'):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _split_name(name: str) -> tuple[str, str]:
    parts = ' '.join((name or '').split()).split(' ')
    if len(parts) < 2:
        return (parts[0] if parts and parts[0] else ''), ''
    return ' '.join(parts[:-1]), parts[-1]


# --------------------------------------------------------------------------- reading

def _read_rows(filename: str, data: bytes) -> tuple[list[str], list[list[str]]]:
    name = (filename or '').lower()
    if name.endswith('.csv'):
        text = None
        for enc in ('utf-8-sig', 'cp1252'):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise OrderFileError('The CSV file could not be read as text.')
        rows = [[_cell(c) for c in r] for r in csv.reader(io.StringIO(text))]
    elif name.endswith('.xlsx'):
        import openpyxl

        try:
            wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
        except Exception:
            raise OrderFileError('The Excel file could not be opened.') from None
        ws = wb.worksheets[0]
        rows = [[_cell(c) for c in r] for r in ws.iter_rows(values_only=True)]
    else:
        raise OrderFileError('Upload the orders file as .xlsx or .csv.')
    rows = [r for r in rows if any(c for c in r)]
    if not rows:
        raise OrderFileError('The file is empty.')
    return rows[0], rows[1:]


def detect_format(headers) -> str | None:
    have = {h.casefold() for h in headers if h}
    for fmt, cols in DETECT.items():
        if all(c.casefold() in have for c in cols):
            return fmt
    return None


def parse_order_file(filename: str, data: bytes) -> ParsedFile:
    if len(data) > MAX_FILE_BYTES:
        raise OrderFileError('The file is larger than 10 MB.')
    headers, rows = _read_rows(filename, data)
    fmt = detect_format(headers)
    if fmt is None:
        raise OrderFileError(
            'This is not a Kogan or BigW orders file (expected the OrderID / ProductCode or '
            'Order No / SKU columns).'
        )
    index = {h.casefold(): i for i, h in enumerate(headers) if h}
    missing = [c for c in REQUIRED[fmt] if c.casefold() not in index]
    if missing:
        raise OrderFileError(f'{FORMAT_LABEL[fmt]} orders file is missing columns: {", ".join(missing)}.')

    orders: dict[str, ParsedOrder] = {}
    skipped: list = []
    line_count = 0
    for offset, row in enumerate(rows):
        rownum = offset + 2  # 1-based, after the header row

        def g(col, _row=row):
            i = index.get(col.casefold())
            return _row[i].strip() if i is not None and i < len(_row) else ''

        parsed = (_kogan_row if fmt == 'kogan' else _bigw_row)(g, rownum, skipped)
        if parsed is None:
            continue
        order_id, base, line = parsed
        order = orders.get(order_id)
        if order is None:
            order = orders[order_id] = ParsedOrder(order_id=order_id, **base)
        order.lines.append(line)
        line_count += 1
    return ParsedFile(file_format=fmt, orders=list(orders.values()), line_count=line_count, skipped=skipped)


def _kogan_row(g, rownum, skipped):
    order_id, sku = g('OrderID'), g('ProductCode')
    if not order_id or not sku:
        raise OrderFileError(f'Row {rownum}: OrderID or ProductCode is blank.')
    name = g('DeliveryName')
    first, last = _split_name(name)
    customer = {
        'firstName': first, 'lastName': last, 'name': name,
        'email': g('DeliveryEmailAddress'), 'phone': g('DeliveryPhone'),
        'shippingAddress': {
            'line1': g('DeliveryAddress1'), 'line2': g('DeliveryAddress2'),
            'city': g('DeliverySuburb'), 'state': g('DeliveryState'),
            'postcode': g('DeliveryPostCode'), 'country': g('DestCountry') or 'AU', 'company': '',
        },
    }
    line = {
        'sku': sku,
        'title': g('Product Description'),
        'quantity': _qty(g('Quantity'), rownum),
        'unit_price': _money(g('ItemPrice'), 'ItemPrice', rownum),
        'commission': None,
        'shipping': None,
        'line_ref': g('OrderRow'),
    }
    base = {
        'marketplace': 'Kogan',
        'customer': customer,
        'ordered_at': _day(g('OrderDate')),
        'status_raw': 'Open',
        'extra': {'dispatchEtd': (_day(g('Dispatch ETD')) or g('Dispatch ETD')) or None,
                  'webReference': g('WebReference'), 'originWarehouse': g('OriginWarehouse')},
    }
    return order_id, base, line


def _bigw_row(g, rownum, skipped):
    order_id, sku = g('Order No'), g('SKU')
    if not order_id or not sku:
        raise OrderFileError(f'Row {rownum}: Order No or SKU is blank.')
    status = g('Status')
    if status.casefold() != BIGW_OPEN_STATUS:
        skipped.append((rownum, f'Order {order_id} status is "{status or "blank"}", not Purchased'))
        return None
    name = g('Name')
    first, last = _split_name(name)
    company = g('Company Name')
    customer = {
        'firstName': first, 'lastName': last, 'name': name,
        'email': '', 'phone': g('Phone'), 'company': company,
        'shippingAddress': {
            'line1': g('Address'), 'line2': g('Address 2'), 'city': g('Suburb'),
            'state': g('State'), 'postcode': g('Postcode'), 'country': 'AU', 'company': company,
        },
    }
    line = {
        'sku': sku,
        'title': g('Product Name'),
        'quantity': _qty(g('Quantity'), rownum),
        'unit_price': _money(g('Price(Per Unit)'), 'Price(Per Unit)', rownum),
        'commission': _money(g('Commission Fee'), 'Commission Fee', rownum),
        'shipping': _money(g('Total Shipping'), 'Total Shipping', rownum, required=False),
        'line_ref': g('Option'),
    }
    base = {
        'marketplace': g('Source') or 'BigW',
        'customer': customer,
        'ordered_at': _day(g('Purchased Date')),
        'status_raw': status,
        'extra': {'orderGroupNo': g('Order Group No'), 'combinedOrder': g('Combined Order'),
                  'shippingAreaPaid': g('Shipping Area Paid')},
    }
    return order_id, base, line


# --------------------------------------------------------------------------- saving

def _cents(value: Decimal | None) -> int | None:
    return None if value is None else int((value * 100).quantize(Decimal('1')))


def _raw_order(order: ParsedOrder, fmt: str, filename: str) -> dict:
    """Raw blob in the shape ``order_service.build_order_details`` already reads."""
    return {
        'source': SOURCE,
        'fileFormat': fmt,
        'fileName': filename,
        'orderId': order.order_id,
        'marketplace': order.marketplace,
        'status': order.status_raw,
        'orderDate': order.ordered_at,
        'currency': 'AUD',
        'customer': order.customer,
        'lineItems': [
            {
                'title': ln['title'],
                'sku': ln['sku'],
                'quantity': ln['quantity'],
                'unitPrice': str(ln['unit_price']),
                'commissionFee': None if ln['commission'] is None else str(ln['commission']),
                'shipping': None if ln['shipping'] is None else str(ln['shipping']),
                'lineRef': ln.get('line_ref') or '',
            }
            for ln in order.lines
        ],
        # Cents keys come first in order_service._normalize_totals, so no decimal guessing.
        'subtotalCents': _cents(order.subtotal),
        'shippingCents': _cents(order.shipping),
        'totalCents': _cents(order.total),
        'commissionFeeCents': _cents(order.commission),
        'subtotal': str(order.subtotal),
        'shippingCost': str(order.shipping),
        'total': str(order.total),
        'commissionFee': None if order.commission is None else str(order.commission),
        **order.extra,
    }


def _line_items(order: ParsedOrder) -> list:
    return [
        {
            'title': ln['title'],
            'sku': ln['sku'],
            'externalVariantKey': ln['sku'],
            'externalProductKey': '',
            'quantity': ln['quantity'],
            'priceCents': _cents(ln['unit_price']),
            'commissionCents': _cents(ln['commission']),
            'shippingCents': _cents(ln['shipping']),
            'lineItemId': ln.get('line_ref') or None,
            'imageUrl': None,
        }
        for ln in order.lines
    ]


def check_file_fits_store(fmt: str, store) -> None:
    from stores.credentials import marketplace_kind

    kind = marketplace_kind(store.marketplace)
    if kind not in FORMAT_MARKETPLACES[fmt]:
        label = store.marketplace.name if store.marketplace else 'no marketplace'
        raise OrderFileError(
            f'This is a {FORMAT_LABEL[fmt]} orders file, but {store.name} is a {label} store.'
        )


def import_order_file(store, user, filename: str, data: bytes) -> dict:
    """Parse the file and upsert one MarketplaceOrder per order ID. Returns counts for the UI."""
    from django.db import transaction

    from .models import Environment, MarketplaceOrder, OrderStatus

    parsed = parse_order_file(filename, data)
    check_file_fits_store(parsed.file_format, store)

    created = updated = 0
    order_ids: list[str] = []
    with transaction.atomic():
        existing = {
            o.external_order_key: o
            for o in MarketplaceOrder.objects.select_for_update().filter(
                store=store,
                environment=Environment.PRODUCTION,
                external_order_key__in=[o.order_id for o in parsed.orders],
            )
        }
        for order in parsed.orders:
            fields = {
                'invoice_number': order.order_id,
                'customer_info_json': order.customer,
                'line_items_json': _line_items(order),
                'total_amount_cents': _cents(order.total),
                'raw_response_json': _raw_order(order, parsed.file_format, filename),
            }
            row = existing.get(order.order_id)
            if row is None:
                row = MarketplaceOrder.objects.create(
                    store=store,
                    user=user,
                    external_order_key=order.order_id,
                    environment=Environment.PRODUCTION,
                    # Customer has paid; the order waits to be bought and shipped.
                    status=OrderStatus.PAID,
                    **fields,
                )
                created += 1
            else:
                # Re-import refreshes order details but never moves the order's status.
                for k, v in fields.items():
                    setattr(row, k, v)
                row.save(update_fields=[*fields.keys(), 'updated_at'])
                updated += 1
            order_ids.append(str(row.id))

    label = FORMAT_LABEL[parsed.file_format]
    msg = (
        f'{label} orders file: {len(parsed.orders)} order(s), {parsed.line_count} line(s). '
        f'{created} new, {updated} updated.'
    )
    if parsed.skipped:
        msg += f' {len(parsed.skipped)} row(s) skipped (not open orders).'
    return {
        'ok': True,
        'format': parsed.file_format,
        'orders': len(parsed.orders),
        'lines': parsed.line_count,
        'created': created,
        'updated': updated,
        'skipped': [{'row': r, 'reason': why} for r, why in parsed.skipped],
        'order_ids': order_ids,
        'message': msg,
    }
