"""
Catalog services: upload parsing, validation, and value normalization.
"""
import csv
import io
from typing import Any

from django.db import transaction

from .models import CatalogUpload, CatalogUploadRow
from stores.models import Store
from vendor.models import Vendor


# Column names in expected order (case-insensitive match)
EXPECTED_COLUMNS = [
    'vendor name', 'vendor id', 'is variation', 'variation id',
    'marketplace name', 'store name', 'marketplace parent sku',
    'marketplace child sku', 'marketplace id', 'vendor sku',
    'vendor url', 'action',
]


def _parse_xlsx(file_obj) -> list[list[Any]]:
    """Parse XLSX and return list of rows."""
    import openpyxl
    wb = openpyxl.load_workbook(file_obj, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    return rows


def _parse_csv(file_obj) -> list[list[Any]]:
    """Parse CSV and return list of rows."""
    content = file_obj.read()
    if hasattr(content, 'decode'):
        content = content.decode('utf-8-sig')
    reader = csv.reader(io.StringIO(content))
    return list(reader)


def parse_upload_file(file_obj, filename: str) -> list[list[Any]]:
    """Parse XLSX or CSV; return list of rows (first row = header)."""
    name = (filename or '').lower()
    if name.endswith('.csv'):
        return _parse_csv(file_obj)
    if name.endswith('.xlsx') or name.endswith('.xls'):
        return _parse_xlsx(file_obj)
    raise ValueError("File must be CSV or XLSX")


def _col_index(header: list, col_name: str) -> int | None:
    """Find column index by header name (case-insensitive)."""
    col_lower = col_name.lower().replace('_', ' ')
    for i, h in enumerate(header):
        h_clean = (str(h) or '').strip().lower().replace('_', ' ')
        if h_clean == col_lower or col_lower in h_clean or h_clean in col_lower:
            return i
    return None


def _store_raw(val: Any) -> str:
    """Preserve exact value including 'N/A' for raw columns."""
    if val is None:
        return ''
    s = str(val).strip()
    return s


def _normalize(val: Any) -> str | None:
    """For resolution: treat 'N/A' and empty as None."""
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.upper() == 'N/A':
        return None
    return s


def validate_and_create_upload(
    *,
    user,
    store: Store,
    file_obj,
    filename: str,
) -> tuple[CatalogUpload, list[str]]:
    """
    Parse file, validate, create CatalogUpload + CatalogUploadRow.
    Preserves raw values including "N/A".
    Returns (upload, errors).
    """
    errors: list[str] = []

    try:
        rows = parse_upload_file(file_obj, filename)
    except Exception as e:
        return None, [str(e)]

    if not rows:
        return None, ["File is empty"]

    header = rows[0]
    indices = {col: _col_index(header, col) for col in EXPECTED_COLUMNS}

    required = ['vendor name', 'store name', 'action']
    for col in required:
        if indices.get(col) is None:
            return None, [f"Required column missing: {col.title()}"]

    store_name_lower = store.name.lower()
    vendors_by_name = {v.name.lower(): v for v in Vendor.objects.all()}
    vendors_by_name.update({v.code.lower(): v for v in Vendor.objects.all()})

    with transaction.atomic():
        upload = CatalogUpload.objects.create(
            user=user,
            store=store,
            original_filename=filename,
            total_rows=0,
            status=CatalogUpload.Status.PENDING,
        )

        def _val(row: list, col: str, raw: bool = True) -> str:
            i = indices.get(col)
            if i is None or i >= len(row):
                return ''
            v = row[i]
            return _store_raw(v) if raw else (_normalize(v) or '')

        created_rows = 0
        for row_num, row in enumerate(rows[1:], start=2):
            vendor_name_raw = _val(row, 'vendor name')
            vendor_id_raw = _val(row, 'vendor id')
            is_variation_raw = _val(row, 'is variation')
            variation_id_raw = _val(row, 'variation id')
            marketplace_name_raw = _val(row, 'marketplace name')
            store_name_raw = _val(row, 'store name')
            marketplace_parent_sku_raw = _val(row, 'marketplace parent sku')
            marketplace_child_sku_raw = _val(row, 'marketplace child sku')
            marketplace_id_raw = _val(row, 'marketplace id')
            vendor_sku_raw = _val(row, 'vendor sku')
            vendor_url_raw = _val(row, 'vendor url')
            action_raw = (_val(row, 'action') or 'Add').strip()

            # Validation
            action_norm = action_raw.lower() if action_raw else 'add'
            if not action_norm in ('add', 'update', 'delete'):
                action_norm = 'add'

            if _normalize(vendor_name_raw) is None:
                errors.append(f"Row {row_num}: Vendor Name required (cannot be N/A or empty)")
                continue
            if _normalize(store_name_raw) is None:
                errors.append(f"Row {row_num}: Store Name required (cannot be N/A or empty)")
                continue

            if store_name_raw and store_name_raw.lower() != store_name_lower:
                errors.append(f"Row {row_num}: Store Name '{store_name_raw}' does not match upload store '{store.name}' (row still created)")

            if action_norm == 'add':
                sku_val = (
                    _normalize(vendor_sku_raw)
                    or _normalize(marketplace_child_sku_raw)
                    or _normalize(vendor_id_raw)
                    or _normalize(marketplace_parent_sku_raw)
                )
                if not sku_val:
                    errors.append(f"Row {row_num}: Vendor SKU, Marketplace Child SKU, Vendor ID, or Marketplace Parent SKU required for Add")
                    continue
            elif action_norm == 'delete':
                id_val = (
                    _normalize(marketplace_id_raw)
                    or _normalize(vendor_sku_raw)
                    or _normalize(marketplace_child_sku_raw)
                    or _normalize(marketplace_parent_sku_raw)
                )
                if not id_val:
                    errors.append(f"Row {row_num}: Delete requires Marketplace ID, Vendor SKU, Marketplace Child SKU, or Marketplace Parent SKU to find the product")
                    continue

            # Resolve vendor for FK (optional at upload; used in sync)
            vendor = None
            vn = _normalize(vendor_name_raw)
            if vn:
                vendor = vendors_by_name.get(vn.lower())

            CatalogUploadRow.objects.create(
                catalog_upload=upload,
                row_number=row_num,
                vendor_name_raw=vendor_name_raw,
                vendor_id_raw=vendor_id_raw,
                is_variation_raw=is_variation_raw,
                variation_id_raw=variation_id_raw,
                marketplace_name_raw=marketplace_name_raw,
                store_name_raw=store_name_raw,
                marketplace_parent_sku_raw=marketplace_parent_sku_raw,
                marketplace_child_sku_raw=marketplace_child_sku_raw,
                marketplace_id_raw=marketplace_id_raw,
                vendor_sku_raw=vendor_sku_raw,
                vendor_url_raw=vendor_url_raw,
                action_raw=action_raw or 'Add',
                vendor=vendor,
                store=store,
            )
            created_rows += 1

        upload.total_rows = created_rows
        upload.processed_rows = 0
        if errors:
            upload.status = CatalogUpload.Status.VALIDATED
            upload.error_summary = "; ".join(errors[:5])
            if len(errors) > 5:
                upload.error_summary += f" (+{len(errors) - 5} more)"
        else:
            upload.status = CatalogUpload.Status.VALIDATED
        upload.save(update_fields=['total_rows', 'processed_rows', 'status', 'error_summary'])

    return upload, errors
