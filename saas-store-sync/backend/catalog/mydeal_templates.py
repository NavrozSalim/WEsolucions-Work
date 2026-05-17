"""
Mydeal marketplace: upload Price / Inventory CSV templates and export filled files.

RRP(IncGST) = Price(IncGST) / (mydeal_rrp_margin_percentage / 100)
when margin is set and > 0.
"""
from __future__ import annotations

import csv
import io
import zipfile
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from django.db import transaction
from django.http import HttpResponse

from catalog.models import MydealTemplateRow, ProductMapping
from stores.models import Store, StoreVendorPriceSettings

MYDEAL_PRICE_HEADERS = [
    'DealID',
    'VariantID',
    'ExternalID',
    'SKU',
    'Options',
    'DealTitle',
    'Price(IncGST)',
    'RRP(IncGST)',
]

MYDEAL_INVENTORY_HEADERS = [
    'DealID',
    'VariantID',
    'ExternalID',
    'SKU',
    'Options',
    'DealTitle',
    'StockOnHand',
    'Discontinued',
    'MyDealApproved',
]


def store_is_mydeal(store: Store) -> bool:
    m = getattr(store, 'marketplace', None)
    if not m:
        return False
    code = (getattr(m, 'code', '') or '').strip().lower()
    name = (getattr(m, 'name', '') or '').strip().lower()
    return code == 'mydeal' or name == 'mydeal'


def mydeal_store_label(store: Store) -> str:
    """Use store name (e.g. TFS, P&P) for export filenames."""
    name = (getattr(store, 'name', None) or '').strip()
    return name or 'Mydeal Store'


def template_status(store: Store) -> dict[str, Any]:
    kinds = set(
        MydealTemplateRow.objects.filter(store=store)
        .values_list('kind', flat=True)
        .distinct()
    )
    price_rows = MydealTemplateRow.objects.filter(
        store=store, kind=MydealTemplateRow.Kind.PRICE,
    ).count()
    inv_rows = MydealTemplateRow.objects.filter(
        store=store, kind=MydealTemplateRow.Kind.INVENTORY,
    ).count()
    return {
        'store_name': mydeal_store_label(store),
        'price_uploaded': MydealTemplateRow.Kind.PRICE in kinds,
        'inventory_uploaded': MydealTemplateRow.Kind.INVENTORY in kinds,
        'price_row_count': price_rows,
        'inventory_row_count': inv_rows,
        'ready': MydealTemplateRow.Kind.PRICE in kinds and MydealTemplateRow.Kind.INVENTORY in kinds,
    }


def _normalize_sku(raw: Any) -> str:
    if raw is None:
        return ''
    s = str(raw).strip()
    if s.endswith('.0') and s[:-2].isdigit():
        s = s[:-2]
    return s


def _read_csv_text(raw: bytes | str) -> str:
    if isinstance(raw, bytes):
        return raw.decode('utf-8-sig', errors='replace')
    return raw


def _read_csv_rows_from_text(text: str) -> tuple[list[str], list[dict[str, str]]]:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError('CSV has no header row.')
    headers = [h.strip() for h in reader.fieldnames if h]
    rows: list[dict[str, str]] = []
    for i, row in enumerate(reader, start=2):
        if not any((v or '').strip() for v in row.values()):
            continue
        cleaned = {k.strip(): (v or '').strip() for k, v in row.items() if k}
        sku = _normalize_sku(cleaned.get('SKU'))
        if not sku:
            raise ValueError(f'Row {i}: SKU is required.')
        rows.append(cleaned)
    return headers, rows


def _read_csv_rows(file_obj) -> tuple[list[str], list[dict[str, str]]]:
    raw = file_obj.read()
    return _read_csv_rows_from_text(_read_csv_text(raw))


def _classify_mydeal_csv_headers(headers: list[str]) -> str | None:
    if headers == MYDEAL_PRICE_HEADERS:
        return MydealTemplateRow.Kind.PRICE
    if headers == MYDEAL_INVENTORY_HEADERS:
        return MydealTemplateRow.Kind.INVENTORY
    return None


def _validate_headers(headers: list[str], expected: list[str]) -> None:
    if headers != expected:
        raise ValueError(
            f'Invalid template headers. Expected exactly: {", ".join(expected)}'
        )


@transaction.atomic
def ingest_mydeal_template(store: Store, kind: str, file_obj) -> dict[str, Any]:
    if not store_is_mydeal(store):
        raise ValueError('Store is not a Mydeal marketplace store.')
    if kind not in (MydealTemplateRow.Kind.PRICE, MydealTemplateRow.Kind.INVENTORY):
        raise ValueError('kind must be price or inventory.')

    expected = (
        MYDEAL_PRICE_HEADERS
        if kind == MydealTemplateRow.Kind.PRICE
        else MYDEAL_INVENTORY_HEADERS
    )
    headers, rows = _read_csv_rows(file_obj)
    _validate_headers(headers, expected)
    if not rows:
        raise ValueError('Template file has no data rows.')

    previous_count = MydealTemplateRow.objects.filter(store=store, kind=kind).count()
    MydealTemplateRow.objects.filter(store=store, kind=kind).delete()

    bulk: list[MydealTemplateRow] = []
    for idx, row in enumerate(rows, start=1):
        bulk.append(
            MydealTemplateRow(
                store=store,
                kind=kind,
                row_number=idx,
                deal_id=row.get('DealID', ''),
                variant_id=row.get('VariantID', ''),
                external_id=row.get('ExternalID', ''),
                sku=_normalize_sku(row.get('SKU')),
                options=row.get('Options', ''),
                deal_title=row.get('DealTitle', ''),
                discontinued=row.get('Discontinued', ''),
                mydeal_approved=row.get('MyDealApproved', ''),
            )
        )
    MydealTemplateRow.objects.bulk_create(bulk, batch_size=500)

    return {
        'kind': kind,
        'row_count': len(bulk),
        'replaced': previous_count > 0,
        'previous_row_count': previous_count,
        'status': template_status(store),
    }


@transaction.atomic
def ingest_mydeal_template_csv_bytes(
    store: Store,
    kind: str,
    raw: bytes,
    *,
    source_name: str = '',
) -> dict[str, Any]:
    """Ingest from in-memory CSV bytes (used by ZIP upload)."""
    if not store_is_mydeal(store):
        raise ValueError('Store is not a Mydeal marketplace store.')
    if kind not in (MydealTemplateRow.Kind.PRICE, MydealTemplateRow.Kind.INVENTORY):
        raise ValueError('kind must be price or inventory.')

    expected = (
        MYDEAL_PRICE_HEADERS
        if kind == MydealTemplateRow.Kind.PRICE
        else MYDEAL_INVENTORY_HEADERS
    )
    headers, rows = _read_csv_rows_from_text(_read_csv_text(raw))
    _validate_headers(headers, expected)
    if not rows:
        raise ValueError(
            f'Template file has no data rows{f" ({source_name})" if source_name else ""}.'
        )

    previous_count = MydealTemplateRow.objects.filter(store=store, kind=kind).count()
    MydealTemplateRow.objects.filter(store=store, kind=kind).delete()

    bulk: list[MydealTemplateRow] = []
    for idx, row in enumerate(rows, start=1):
        bulk.append(
            MydealTemplateRow(
                store=store,
                kind=kind,
                row_number=idx,
                deal_id=row.get('DealID', ''),
                variant_id=row.get('VariantID', ''),
                external_id=row.get('ExternalID', ''),
                sku=_normalize_sku(row.get('SKU')),
                options=row.get('Options', ''),
                deal_title=row.get('DealTitle', ''),
                discontinued=row.get('Discontinued', ''),
                mydeal_approved=row.get('MyDealApproved', ''),
            )
        )
    MydealTemplateRow.objects.bulk_create(bulk, batch_size=500)

    return {
        'kind': kind,
        'row_count': len(bulk),
        'replaced': previous_count > 0,
        'previous_row_count': previous_count,
        'source_name': source_name,
    }


@transaction.atomic
def ingest_mydeal_templates_zip(store: Store, file_obj) -> dict[str, Any]:
    """Replace price and/or inventory templates from a ZIP of Mydeal CSV files."""
    if not store_is_mydeal(store):
        raise ValueError('Store is not a Mydeal marketplace store.')

    found: dict[str, tuple[bytes, str]] = {}
    with zipfile.ZipFile(file_obj) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename.split('/')[-1]
            if not name.lower().endswith('.csv'):
                continue
            raw = zf.read(info)
            try:
                headers, _ = _read_csv_rows_from_text(_read_csv_text(raw))
            except ValueError:
                continue
            kind = _classify_mydeal_csv_headers(headers)
            if kind and kind not in found:
                found[kind] = (raw, name)

    if not found:
        raise ValueError(
            'ZIP must contain Mydeal Price and/or Inventory CSV files with the correct headers.'
        )

    results: dict[str, Any] = {}
    for kind in (MydealTemplateRow.Kind.PRICE, MydealTemplateRow.Kind.INVENTORY):
        if kind not in found:
            continue
        raw, name = found[kind]
        results[kind] = ingest_mydeal_template_csv_bytes(store, kind, raw, source_name=name)

    return {
        'kinds': list(results.keys()),
        'details': results,
        'status': template_status(store),
    }


def _listing_data_by_sku(store: Store) -> dict[str, dict[str, Any]]:
    """Map normalized SKU -> {price, stock, rrp_margin_pct}."""
    margin_by_vendor: dict[str, Decimal | None] = {}
    for ps in StoreVendorPriceSettings.objects.filter(store=store).select_related('vendor'):
        vid = str(ps.vendor_id)
        m = ps.mydeal_rrp_margin_percentage
        margin_by_vendor[vid] = Decimal(str(m)) if m is not None else None

    out: dict[str, dict[str, Any]] = {}
    qs = (
        ProductMapping.objects.filter(store=store, is_active=True)
        .select_related('product', 'product__vendor')
    )
    for pm in qs.iterator(chunk_size=500):
        sku_keys = []
        for raw in (
            pm.marketplace_child_sku,
            pm.marketplace_parent_sku,
            getattr(pm.product, 'vendor_sku', None),
        ):
            s = _normalize_sku(raw)
            if s:
                sku_keys.append(s)
        if not sku_keys:
            continue
        vid = str(pm.product.vendor_id) if pm.product_id else ''
        margin_pct = margin_by_vendor.get(vid)
        entry = {
            'price': pm.store_price,
            'stock': pm.store_stock,
            'margin_pct': margin_pct,
        }
        for sk in sku_keys:
            out[sk] = entry
    return out


def _compute_rrp(price: Decimal, margin_pct: Decimal | None) -> Decimal | None:
    if price is None or margin_pct is None:
        return None
    try:
        m = Decimal(str(margin_pct))
    except Exception:
        return None
    if m <= 0:
        return None
    divisor = m / Decimal('100')
    if divisor <= 0:
        return None
    return (price / divisor).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _export_price_csv(store: Store) -> bytes:
    rows = list(
        MydealTemplateRow.objects.filter(
            store=store, kind=MydealTemplateRow.Kind.PRICE,
        ).order_by('row_number')
    )
    if not rows:
        raise ValueError('Price template not uploaded yet.')

    by_sku = _listing_data_by_sku(store)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(MYDEAL_PRICE_HEADERS)
    for tr in rows:
        sku = _normalize_sku(tr.sku)
        data = by_sku.get(sku, {})
        price = data.get('price')
        margin_pct = data.get('margin_pct')
        price_str = ''
        rrp_str = ''
        if price is not None:
            try:
                p = Decimal(str(price)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                price_str = f'{p:.2f}'
                rrp = _compute_rrp(p, margin_pct)
                if rrp is not None:
                    rrp_str = f'{rrp:.2f}'
            except Exception:
                pass
        writer.writerow([
            tr.deal_id,
            tr.variant_id,
            tr.external_id,
            sku,
            tr.options,
            tr.deal_title,
            price_str,
            rrp_str,
        ])
    return buf.getvalue().encode('utf-8')


def _export_inventory_csv(store: Store) -> bytes:
    rows = list(
        MydealTemplateRow.objects.filter(
            store=store, kind=MydealTemplateRow.Kind.INVENTORY,
        ).order_by('row_number')
    )
    if not rows:
        raise ValueError('Inventory template not uploaded yet.')

    by_sku = _listing_data_by_sku(store)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(MYDEAL_INVENTORY_HEADERS)
    for tr in rows:
        sku = _normalize_sku(tr.sku)
        data = by_sku.get(sku, {})
        stock = data.get('stock')
        stock_str = ''
        if stock is not None:
            try:
                stock_str = str(int(stock))
            except (TypeError, ValueError):
                stock_str = ''
        writer.writerow([
            tr.deal_id,
            tr.variant_id,
            tr.external_id,
            sku,
            tr.options,
            tr.deal_title,
            stock_str,
            tr.discontinued or 'FALSE',
            tr.mydeal_approved or 'Approved',
        ])
    return buf.getvalue().encode('utf-8')


def export_filename(store: Store, kind: str) -> str:
    label = mydeal_store_label(store)
    if kind == 'price':
        return f'Mydeal - {label} - Price Template.csv'
    if kind == 'inventory':
        return f'Mydeal - {label} - Inventory Template.csv'
    return f'Mydeal - {label} - Templates.zip'


def build_export_response(store: Store, export_type: str) -> HttpResponse:
    label = mydeal_store_label(store)
    if export_type == 'price':
        content = _export_price_csv(store)
        resp = HttpResponse(content, content_type='text/csv; charset=utf-8')
        resp['Content-Disposition'] = (
            f'attachment; filename="{export_filename(store, "price")}"'
        )
        return resp
    if export_type == 'inventory':
        content = _export_inventory_csv(store)
        resp = HttpResponse(content, content_type='text/csv; charset=utf-8')
        resp['Content-Disposition'] = (
            f'attachment; filename="{export_filename(store, "inventory")}"'
        )
        return resp
    if export_type == 'both':
        zbuf = io.BytesIO()
        with zipfile.ZipFile(zbuf, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                f'Mydeal - {label} - Price Template.csv',
                _export_price_csv(store),
            )
            zf.writestr(
                f'Mydeal - {label} - Inventory Template.csv',
                _export_inventory_csv(store),
            )
        resp = HttpResponse(zbuf.getvalue(), content_type='application/zip')
        resp['Content-Disposition'] = (
            f'attachment; filename="Mydeal - {label} - Templates.zip"'
        )
        return resp
    raise ValueError('export_type must be price, inventory, or both.')
