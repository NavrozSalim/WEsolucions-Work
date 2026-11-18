"""
Mydeal marketplace: upload Price / Inventory CSV templates and export filled files.

RRP(IncGST) = Price(IncGST) / ((100 - mydeal_rrp_margin_percentage) / 100)
when discount % is set and > 0 (e.g. 26 means price is 74% of RRP).
"""
from __future__ import annotations

import csv
import io
import uuid
import zipfile
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from django.db import transaction
from django.http import HttpResponse

from catalog.marketplace_rrp import compute_marketplace_rrp
from catalog.models import MydealTemplateRow, ProductMapping
from stores.models import Store, StoreVendorPriceSettings
from vendor.models import Vendor

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
    'WMPApproved',
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
                mydeal_approved=row.get('WMPApproved', ''),
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
                mydeal_approved=row.get('WMPApproved', ''),
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


def _sku_key(raw: Any) -> str:
    return _normalize_sku(raw).lower()


def _is_discontinued(value: Any) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes", "y"}


def mapping_sku_keys(mapping) -> list[str]:
    """Marketplace and vendor SKUs that can match an inventory-template row."""
    product = getattr(mapping, "product", None)
    raws = [
        getattr(mapping, "marketplace_child_sku", None),
        getattr(mapping, "marketplace_parent_sku", None),
    ]
    if product is not None:
        raws.append(getattr(product, "vendor_sku", None))
    out: list[str] = []
    seen: set[str] = set()
    for raw in raws:
        key = _sku_key(raw)
        if key and key not in seen:
            seen.add(key)
            out.append(_normalize_sku(raw))
    return out


def mark_inventory_discontinued(store: Store, skus, *, discontinued: bool) -> int:
    """Set Discontinued on inventory-template rows whose SKU is in ``skus``.

    Non-MyDeal stores are ignored. Matching is case-insensitive.
    """
    if store is None or not store_is_mydeal(store):
        return 0
    wanted: set[str] = set()
    for raw in skus or []:
        key = _sku_key(raw)
        if key:
            wanted.add(key)
    if not wanted:
        return 0
    value = "TRUE" if discontinued else "FALSE"
    ids: list = []
    rows = MydealTemplateRow.objects.filter(
        store=store,
        kind=MydealTemplateRow.Kind.INVENTORY,
    ).only("id", "sku", "discontinued")
    for row in rows.iterator(chunk_size=1000):
        if _sku_key(row.sku) not in wanted:
            continue
        current = str(row.discontinued or "").strip().upper()
        if current == value:
            continue
        ids.append(row.id)
    updated = 0
    for start in range(0, len(ids), 500):
        updated += MydealTemplateRow.objects.filter(
            id__in=ids[start:start + 500],
        ).update(discontinued=value)
    return updated


class MydealDeleteTemplateError(ValueError):
    """The MyDeal delete file cannot be built for this store."""


MYDEAL_CATALOG_DELETE_HEADERS = [
    'Vendor Name',
    'Vendor ID',
    'Marketplace Name',
    'Store Name',
    'SKU',
    'Vendor URL',
    'Action',
]


def normalize_mydeal_delete_scope(raw: str) -> str:
    scope = (raw or '').strip().lower()
    if scope in ('mapped', 'unmapped'):
        return scope
    return ''


def mydeal_delete_template_filename(scope: str) -> str:
    scope = normalize_mydeal_delete_scope(scope) or 'mapped'
    return f'catalog_delete_template_mydeal_{scope}.csv'


def inventory_template_sku_keys(store: Store) -> set[str]:
    """Normalized SKUs on the uploaded MyDeal inventory template."""
    keys: set[str] = set()
    rows = MydealTemplateRow.objects.filter(
        store=store,
        kind=MydealTemplateRow.Kind.INVENTORY,
    ).only('sku')
    for row in rows.iterator(chunk_size=1000):
        key = _sku_key(row.sku)
        if key:
            keys.add(key)
    return keys


def _mapping_listing_sku(pm) -> str:
    product = getattr(pm, 'product', None)
    for raw in (
        getattr(pm, 'marketplace_parent_sku', None),
        getattr(pm, 'marketplace_child_sku', None),
        getattr(product, 'vendor_sku', None) if product is not None else None,
    ):
        sku = _normalize_sku(raw)
        if sku:
            return sku
    return ''


def _all_mapping_sku_keys(store: Store) -> set[str]:
    keys: set[str] = set()
    qs = (
        ProductMapping.objects.filter(store=store)
        .select_related('product')
        .only(
            'marketplace_child_sku',
            'marketplace_parent_sku',
            'product__vendor_sku',
        )
    )
    for pm in qs.iterator(chunk_size=500):
        for sku in mapping_sku_keys(pm):
            key = _sku_key(sku)
            if key:
                keys.add(key)
    return keys


def mydeal_delete_template_rows(store: Store, scope: str) -> tuple[list[str], list[list[str]]]:
    """Catalog delete rows for mapped products, or MyDeal SKUs with no mapping.

    Mapped rows deactivate the catalog product and zero inventory only.
    Unmapped rows are SKUs on the inventory template that were never mapped;
    uploading them sets Discontinued to TRUE and inventory to 0.
    """
    if not store_is_mydeal(store):
        raise MydealDeleteTemplateError('Store is not a MyDeal store.')
    scope = normalize_mydeal_delete_scope(scope)
    if not scope:
        raise MydealDeleteTemplateError('Choose mapped or not mapped products.')

    store_name = (getattr(store, 'name', None) or '').strip()
    headers = list(MYDEAL_CATALOG_DELETE_HEADERS)
    if scope == 'mapped':
        return headers, _mapped_delete_rows(store, store_name)
    return headers, _unmapped_delete_rows(store, store_name)


def _mapped_delete_rows(store: Store, store_name: str) -> list[list[str]]:
    qs = (
        ProductMapping.objects.filter(store=store, is_active=True)
        .select_related('product', 'product__vendor')
        .order_by('product__vendor_sku', 'marketplace_parent_sku', 'marketplace_child_sku')
    )
    rows: list[list[str]] = []
    seen: set[str] = set()
    for pm in qs.iterator(chunk_size=500):
        sku = _mapping_listing_sku(pm)
        key = _sku_key(sku)
        if not key or key in seen:
            continue
        seen.add(key)
        product = pm.product
        vendor = product.vendor if product is not None else None
        rows.append([
            (vendor.name if vendor is not None else '') or '',
            _normalize_sku(getattr(product, 'vendor_sku', None)) if product is not None else '',
            'MyDeal',
            store_name,
            sku,
            (getattr(product, 'vendor_url', None) or '') if product is not None else '',
            'Delete',
        ])
    return rows


def _unmapped_delete_rows(store: Store, store_name: str) -> list[list[str]]:
    has_inventory = MydealTemplateRow.objects.filter(
        store=store,
        kind=MydealTemplateRow.Kind.INVENTORY,
    ).exists()
    if not has_inventory:
        raise MydealDeleteTemplateError(
            'Upload the MyDeal inventory template before downloading products '
            'that are on MyDeal but not mapped.'
        )
    mapped = _all_mapping_sku_keys(store)
    rows: list[list[str]] = []
    seen: set[str] = set()
    inv_rows = MydealTemplateRow.objects.filter(
        store=store,
        kind=MydealTemplateRow.Kind.INVENTORY,
    ).order_by('row_number')
    for tr in inv_rows.iterator(chunk_size=1000):
        sku = _normalize_sku(tr.sku)
        key = _sku_key(sku)
        if not key or key in mapped or key in seen:
            continue
        seen.add(key)
        rows.append(['', '', 'MyDeal', store_name, sku, '', 'Delete'])
    return rows


UNMAPPED_VENDOR = 'unmapped'
UNMAPPED_VENDOR_LABEL = 'Unmapped'


def _mapping_vendor(pm) -> dict[str, str]:
    product = getattr(pm, 'product', None)
    vendor = getattr(product, 'vendor', None) if product is not None else None
    if vendor is None:
        return {'id': UNMAPPED_VENDOR, 'name': UNMAPPED_VENDOR_LABEL}
    name = (vendor.name or vendor.code or '').strip() or 'Vendor'
    return {'id': str(vendor.id), 'name': name}


def _catalog_removed_sku_vendors(store: Store) -> dict[str, dict[str, str]]:
    """SKUs that exist only on inactive mappings (deleted from the catalog).

    Maps normalized SKU -> {'id', 'name'} of the vendor that mapping belonged to.
    """
    active: set[str] = set()
    inactive: dict[str, dict[str, str]] = {}
    qs = (
        ProductMapping.objects.filter(store=store)
        .select_related('product', 'product__vendor')
    )
    for pm in qs.iterator(chunk_size=500):
        keys = {_sku_key(sku) for sku in mapping_sku_keys(pm)}
        if pm.is_active:
            active.update(keys)
            continue
        vendor = _mapping_vendor(pm)
        for key in keys:
            inactive[key] = vendor
    return {k: v for k, v in inactive.items() if k not in active}


def _catalog_removed_sku_keys(store: Store) -> set[str]:
    """SKUs that exist only on inactive mappings (deleted from the catalog)."""
    return set(_catalog_removed_sku_vendors(store))


def _listing_data_by_sku(store: Store) -> dict[str, dict[str, Any]]:
    """Map normalized SKU -> {price, stock, margin_pct, vendor_id, vendor_name}."""
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
        vendor = _mapping_vendor(pm)
        entry = {
            'price': pm.store_price,
            'stock': pm.store_stock,
            'margin_pct': margin_pct,
            'vendor_id': vendor['id'],
            'vendor_name': vendor['name'],
        }
        for sk in sku_keys:
            out[sk] = entry
    return out


class _SkuLookup:
    """Catalog data and vendor for each MyDeal template SKU."""

    def __init__(self, store: Store):
        self.by_sku = _listing_data_by_sku(store)
        self.by_key = {_sku_key(sku): data for sku, data in self.by_sku.items()}
        self.removed = _catalog_removed_sku_vendors(store)

    def data(self, sku: str) -> dict[str, Any]:
        return self.by_sku.get(sku) or self.by_key.get(_sku_key(sku)) or {}

    def vendor(self, sku: str) -> dict[str, str]:
        """The one vendor that owns this SKU, or the Unmapped group.

        Each SKU gets exactly one vendor, so the vendor files add up to the
        all-vendor file.
        """
        data = self.data(sku)
        if data.get('vendor_id'):
            return {'id': data['vendor_id'], 'name': data['vendor_name']}
        removed = self.removed.get(_sku_key(sku))
        if removed:
            return removed
        return {'id': UNMAPPED_VENDOR, 'name': UNMAPPED_VENDOR_LABEL}


def _kind_title(kind: str) -> str:
    return 'Price' if kind == MydealTemplateRow.Kind.PRICE else 'Inventory'


def _headers_for(kind: str) -> list[str]:
    if kind == MydealTemplateRow.Kind.PRICE:
        return MYDEAL_PRICE_HEADERS
    return MYDEAL_INVENTORY_HEADERS


def _template_rows(store: Store, kind: str) -> list[MydealTemplateRow]:
    rows = list(
        MydealTemplateRow.objects.filter(store=store, kind=kind).order_by('row_number')
    )
    if not rows:
        raise ValueError(f'{_kind_title(kind)} template not uploaded yet.')
    return rows


def _price_line(tr: MydealTemplateRow, lookup: _SkuLookup) -> list[str]:
    sku = _normalize_sku(tr.sku)
    data = lookup.by_sku.get(sku, {})
    price = data.get('price')
    margin_pct = data.get('margin_pct')
    price_str = ''
    rrp_str = ''
    if price is not None:
        try:
            p = Decimal(str(price)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            price_str = f'{p:.2f}'
            rrp = compute_marketplace_rrp(p, margin_pct)
            if rrp is not None:
                rrp_str = f'{rrp:.2f}'
        except Exception:
            pass
    return [
        tr.deal_id,
        tr.variant_id,
        tr.external_id,
        sku,
        tr.options,
        tr.deal_title,
        price_str,
        rrp_str,
    ]


def _inventory_line(tr: MydealTemplateRow, lookup: _SkuLookup) -> list[str]:
    sku = _normalize_sku(tr.sku)
    key = _sku_key(sku)
    # A blank stock cell leaves the old quantity on MyDeal.
    # Discontinued TRUE is only for products that are on MyDeal and not
    # mapped. Mapped products that leave the catalog keep Discontinued
    # FALSE and send stock 0.
    if _is_discontinued(tr.discontinued):
        stock_str = '0'
        discontinued = 'TRUE'
    elif key in lookup.removed:
        stock_str = '0'
        discontinued = 'FALSE'
    else:
        stock = lookup.data(sku).get('stock')
        stock_str = ''
        if stock is not None:
            try:
                stock_str = str(int(stock))
            except (TypeError, ValueError):
                stock_str = ''
        discontinued = tr.discontinued or 'FALSE'
    return [
        tr.deal_id,
        tr.variant_id,
        tr.external_id,
        sku,
        tr.options,
        tr.deal_title,
        stock_str,
        discontinued,
        tr.mydeal_approved or 'Approved',
    ]


def _build_lines(
    store: Store,
    kind: str,
    lookup: _SkuLookup,
) -> list[tuple[dict[str, str], list[str]]]:
    """(vendor, csv line) for every template row of ``kind``, in template order."""
    line_fn = _price_line if kind == MydealTemplateRow.Kind.PRICE else _inventory_line
    return [
        (lookup.vendor(_normalize_sku(tr.sku)), line_fn(tr, lookup))
        for tr in _template_rows(store, kind)
    ]


def _write_csv(headers: list[str], lines: list[list[str]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(lines)
    return buf.getvalue().encode('utf-8')


def _kind_lines(
    store: Store,
    kind: str,
    vendor_id: str | None = None,
    lookup: _SkuLookup | None = None,
) -> list[list[str]]:
    lookup = lookup or _SkuLookup(store)
    return [
        line for vendor, line in _build_lines(store, kind, lookup)
        if vendor_id is None or vendor['id'] == vendor_id
    ]


def _export_price_csv(store: Store, vendor_id: str | None = None) -> bytes:
    kind = MydealTemplateRow.Kind.PRICE
    return _write_csv(_headers_for(kind), _kind_lines(store, kind, vendor_id))


def _export_inventory_csv(store: Store, vendor_id: str | None = None) -> bytes:
    kind = MydealTemplateRow.Kind.INVENTORY
    return _write_csv(_headers_for(kind), _kind_lines(store, kind, vendor_id))


def _sorted_groups(groups: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Vendors by name, with the Unmapped group last."""
    return sorted(
        groups.values(),
        key=lambda g: (g['id'] == UNMAPPED_VENDOR, g['name'].lower()),
    )


def vendor_template_summary(store: Store) -> list[dict[str, Any]]:
    """Each vendor with its Price and Inventory template row counts.

    Includes an ``unmapped`` entry for MyDeal SKUs with no catalog mapping.
    """
    lookup = _SkuLookup(store)
    groups: dict[str, dict[str, Any]] = {}
    for kind, field in (
        (MydealTemplateRow.Kind.PRICE, 'price_rows'),
        (MydealTemplateRow.Kind.INVENTORY, 'inventory_rows'),
    ):
        skus = (
            MydealTemplateRow.objects.filter(store=store, kind=kind)
            .values_list('sku', flat=True)
        )
        for raw in skus.iterator(chunk_size=2000):
            vendor = lookup.vendor(_normalize_sku(raw))
            group = groups.setdefault(
                vendor['id'],
                {
                    'id': vendor['id'],
                    'name': vendor['name'],
                    'price_rows': 0,
                    'inventory_rows': 0,
                },
            )
            group[field] += 1
    return _sorted_groups(groups)


def _safe_name(raw: str) -> str:
    cleaned = ''.join('-' if c in '\\/:*?"<>|' else c for c in (raw or ''))
    return cleaned.strip(' .') or 'Vendor'


def vendor_file_name(store: Store, vendor_name: str, kind: str) -> str:
    label = mydeal_store_label(store)
    return f'Mydeal - {label} - {_safe_name(vendor_name)} - {_kind_title(kind)} Template.csv'


_KINDS_BY_TYPE = {
    'price': (MydealTemplateRow.Kind.PRICE,),
    'inventory': (MydealTemplateRow.Kind.INVENTORY,),
    'both': (MydealTemplateRow.Kind.PRICE, MydealTemplateRow.Kind.INVENTORY),
}


def _vendor_files(
    store: Store,
    kinds: tuple[str, ...],
    vendor_id: str | None = None,
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, list[list[str]]]]]:
    """Group template lines by vendor.

    Returns (vendors by id, {vendor_id: {kind: lines}}). A kind whose template
    is not uploaded is skipped; when none of ``kinds`` is uploaded, raises
    ValueError.
    """
    lookup = _SkuLookup(store)
    vendors: dict[str, dict[str, str]] = {}
    files: dict[str, dict[str, list[list[str]]]] = {}
    errors: list[str] = []
    uploaded = 0
    for kind in kinds:
        try:
            lines = _build_lines(store, kind, lookup)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        uploaded += 1
        for vendor, line in lines:
            if vendor_id is not None and vendor['id'] != vendor_id:
                continue
            vendors.setdefault(vendor['id'], vendor)
            files.setdefault(vendor['id'], {}).setdefault(kind, []).append(line)
    if not uploaded:
        raise ValueError(' '.join(errors) or 'No templates uploaded yet.')
    return vendors, files


def _vendor_label(vendor_id: str) -> str:
    if vendor_id == UNMAPPED_VENDOR:
        return UNMAPPED_VENDOR_LABEL
    vendor = Vendor.objects.filter(id=vendor_id).only('name', 'code').first()
    if vendor is None:
        raise ValueError('Vendor not found.')
    return (vendor.name or vendor.code or '').strip() or 'Vendor'


def _normalize_vendor_param(raw: str | None) -> str | None:
    value = (raw or '').strip()
    if not value or value.lower() == 'all':
        return None
    if value.lower() == UNMAPPED_VENDOR:
        return UNMAPPED_VENDOR
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise ValueError('vendor must be a vendor id or "unmapped".') from exc


def _csv_response(content: bytes, filename: str) -> HttpResponse:
    resp = HttpResponse(content, content_type='text/csv; charset=utf-8')
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp


def _zip_response(content: bytes, filename: str) -> HttpResponse:
    resp = HttpResponse(content, content_type='application/zip')
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp


def _build_vendor_export(store: Store, export_type: str, vendor_id: str) -> HttpResponse:
    """One vendor's Price and/or Inventory file."""
    label = mydeal_store_label(store)
    vendor_name = _vendor_label(vendor_id)
    kinds = _KINDS_BY_TYPE[export_type]
    if len(kinds) == 1:
        kind = kinds[0]
        lines = _kind_lines(store, kind, vendor_id)
        if not lines:
            raise ValueError(
                f'No {_kind_title(kind).lower()} template rows for {vendor_name}.'
            )
        return _csv_response(
            _write_csv(_headers_for(kind), lines),
            vendor_file_name(store, vendor_name, kind),
        )

    _, files = _vendor_files(store, kinds, vendor_id)
    by_kind = files.get(vendor_id) or {}
    if not by_kind:
        raise ValueError(f'No template rows for {vendor_name}.')
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for kind in kinds:
            if kind in by_kind:
                zf.writestr(
                    vendor_file_name(store, vendor_name, kind),
                    _write_csv(_headers_for(kind), by_kind[kind]),
                )
    return _zip_response(
        zbuf.getvalue(),
        f'Mydeal - {label} - {_safe_name(vendor_name)} - Templates.zip',
    )


def build_vendor_zip_bytes(
    store: Store,
    vendor_ids: list[str] | None = None,
    kinds: tuple[str, ...] = _KINDS_BY_TYPE['both'],
) -> tuple[bytes, int]:
    """ZIP with a folder per vendor holding that vendor's Price and/or Inventory files.

    ``vendor_ids`` limits it to those vendors (ids or ``unmapped``); None means all.
    Returns (zip bytes, number of CSV files in it).
    """
    vendors, files = _vendor_files(store, kinds)
    wanted = {str(v) for v in vendor_ids} if vendor_ids is not None else None
    zbuf = io.BytesIO()
    used_folders: set[str] = set()
    count = 0
    with zipfile.ZipFile(zbuf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for vendor in _sorted_groups(vendors):
            if wanted is not None and vendor['id'] not in wanted:
                continue
            base = _safe_name(vendor['name'])
            folder = base
            n = 2
            while folder.lower() in used_folders:
                folder = f'{base} ({n})'
                n += 1
            used_folders.add(folder.lower())
            for kind in kinds:
                lines = files[vendor['id']].get(kind)
                if not lines:
                    continue
                zf.writestr(
                    f'{folder}/{vendor_file_name(store, vendor["name"], kind)}',
                    _write_csv(_headers_for(kind), lines),
                )
                count += 1
    return zbuf.getvalue(), count


def _build_by_vendor_zip(store: Store) -> HttpResponse:
    """ZIP with a folder per vendor holding that vendor's Price and Inventory files."""
    content, _ = build_vendor_zip_bytes(store)
    return _zip_response(content, export_filename(store, 'by_vendor'))


def export_filename(store: Store, kind: str) -> str:
    label = mydeal_store_label(store)
    if kind == 'price':
        return f'Mydeal - {label} - Price Template.csv'
    if kind == 'inventory':
        return f'Mydeal - {label} - Inventory Template.csv'
    if kind == 'by_vendor':
        return f'Mydeal - {label} - Templates by Vendor.zip'
    return f'Mydeal - {label} - Templates.zip'


def build_export_response(
    store: Store,
    export_type: str,
    vendor: str | None = None,
) -> HttpResponse:
    """Filled MyDeal template download.

    export_type: price | inventory | both | by_vendor.
    vendor: a vendor id, or ``unmapped``, to get only that vendor's rows
    (with price, inventory or both). Omitted means all vendors in one file.
    """
    if export_type == 'by_vendor':
        return _build_by_vendor_zip(store)
    if export_type not in _KINDS_BY_TYPE:
        raise ValueError('export_type must be price, inventory, both, or by_vendor.')
    vendor_id = _normalize_vendor_param(vendor)
    if vendor_id is not None:
        return _build_vendor_export(store, export_type, vendor_id)

    label = mydeal_store_label(store)
    if export_type == 'price':
        return _csv_response(_export_price_csv(store), export_filename(store, 'price'))
    if export_type == 'inventory':
        return _csv_response(_export_inventory_csv(store), export_filename(store, 'inventory'))
    lookup = _SkuLookup(store)
    price_kind = MydealTemplateRow.Kind.PRICE
    inv_kind = MydealTemplateRow.Kind.INVENTORY
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            f'Mydeal - {label} - Price Template.csv',
            _write_csv(_headers_for(price_kind), _kind_lines(store, price_kind, lookup=lookup)),
        )
        zf.writestr(
            f'Mydeal - {label} - Inventory Template.csv',
            _write_csv(_headers_for(inv_kind), _kind_lines(store, inv_kind, lookup=lookup)),
        )
    return _zip_response(zbuf.getvalue(), export_filename(store, 'both'))
