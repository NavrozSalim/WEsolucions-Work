"""Auto Bot Excel jobs: the Reverb and Etsy bots that run on a laptop.

A job is a ``HebScrapeJob`` row with ``vendor_code`` 'reverb' or 'etsy' and a
``job_type``. The user uploads the Excel on the Auto Bot page, a laptop claims
it through ``/api/v1/ingest/<vendor>/next-job/``, runs the matching bot and
posts back the finished and remaining Excel files.

Kept apart from ``ingest_views.SUPPORTED_VENDORS`` on purpose: the catalog
scrape, scheduled update and progress code loop over that registry and would
otherwise queue URL-scrape jobs for these vendors.
"""
from __future__ import annotations

import io

from openpyxl import Workbook, load_workbook

EXCEL_JOB_VENDORS: dict[str, dict] = {
    'reverb': {'scope': 'reverb', 'label': 'Reverb'},
    'etsy': {'scope': 'etsy', 'label': 'Etsy'},
}

_STOCK_COLUMNS = {'required': ['store_name', 'sku'], 'one_of': ['price', 'quantity']}
_SKU_COLUMNS = {'required': ['store_name', 'sku', 'new_sku'], 'one_of': []}
_CREATE_COLUMNS = {'required': ['store_name'], 'one_of': []}

JOB_TYPES: dict[str, dict] = {
    'reverb-create': {'vendor': 'reverb', 'label': 'Reverb - create listings', **_CREATE_COLUMNS},
    'reverb-stock': {'vendor': 'reverb', 'label': 'Reverb - price and stock', **_STOCK_COLUMNS},
    'reverb-sku': {'vendor': 'reverb', 'label': 'Reverb - change SKU', **_SKU_COLUMNS},
    'etsy-create': {'vendor': 'etsy', 'label': 'Etsy - create listings', **_CREATE_COLUMNS},
    'etsy-stock': {'vendor': 'etsy', 'label': 'Etsy - price and stock', **_STOCK_COLUMNS},
    'etsy-sku': {'vendor': 'etsy', 'label': 'Etsy - change SKU', **_SKU_COLUMNS},
}

MAX_EXCEL_BYTES = 20 * 1024 * 1024

# Same header spellings the bots accept for the store column.
_STORE_ALIASES = {'store_name', 'store name', 'storename', 'store'}


class ExcelJobError(ValueError):
    """The uploaded file is not a usable Excel for this job type."""


def _normalize(header) -> str:
    text = str(header or '').strip().lower()
    return 'store_name' if text in _STORE_ALIASES else text


def _open(file):
    if getattr(file, 'size', 0) and file.size > MAX_EXCEL_BYTES:
        raise ExcelJobError('The Excel file is larger than 20 MB.')
    name = (getattr(file, 'name', '') or '').lower()
    if not name.endswith('.xlsx'):
        raise ExcelJobError('Upload an .xlsx file.')
    try:
        file.seek(0)
        return load_workbook(file, read_only=True, data_only=True)
    except Exception as exc:
        raise ExcelJobError(f'Could not read the Excel file: {exc}') from exc


def read_sheet(file) -> tuple[list[str], int]:
    """Header names (normalized) and the number of non-blank data rows."""
    wb = _open(file)
    try:
        lines = wb.active.iter_rows(values_only=True)
        header = [_normalize(h) for h in (next(lines, None) or [])]
        rows = sum(
            1 for values in lines
            if values and any(v is not None and str(v).strip() for v in values)
        )
    finally:
        wb.close()
        file.seek(0)
    return header, rows


def validate_upload(job_type: str, file) -> int:
    """Checks the Excel has the columns the bot needs. Returns its data row count."""
    spec = JOB_TYPES.get(job_type)
    if spec is None:
        raise ExcelJobError(f'Unknown job type: {job_type}')
    header, rows = read_sheet(file)
    missing = [c for c in spec['required'] if c not in header]
    if missing:
        raise ExcelJobError(f'The Excel is missing columns: {", ".join(missing)}')
    if spec['one_of'] and not any(c in header for c in spec['one_of']):
        raise ExcelJobError(f'The Excel needs a {" or ".join(spec["one_of"])} column')
    if rows == 0:
        raise ExcelJobError('The Excel has no rows.')
    return rows


def split_by_store(file, only_store: str = '') -> dict[str, bytes]:
    """One .xlsx per store_name, each with the uploaded header and that store's rows.

    Each laptop is assigned to a store, so a file with IQ and AMH rows becomes
    two jobs that run on two laptops. ``only_store`` keeps just that store.
    """
    wb = _open(file)
    try:
        lines = wb.active.iter_rows(values_only=True)
        header = list(next(lines, None) or [])
        while header and (header[-1] is None or not str(header[-1]).strip()):
            header.pop()
        normalized = [_normalize(h) for h in header]
        if 'store_name' not in normalized:
            raise ExcelJobError('The Excel is missing columns: store_name')
        store_col = normalized.index('store_name')
        groups: dict[str, list[list]] = {}
        for number, values in enumerate(lines, start=2):
            values = list(values or [])[: len(header)]
            if not any(v is not None and str(v).strip() for v in values):
                continue
            raw = values[store_col] if store_col < len(values) else None
            store = str(raw if raw is not None else '').strip().upper()
            if store.endswith('.0'):
                store = store[:-2]
            if not store:
                raise ExcelJobError(f'Row {number} has no store_name.')
            groups.setdefault(store, []).append(values)
    finally:
        wb.close()
        file.seek(0)

    if only_store:
        if only_store not in groups:
            raise ExcelJobError(f'The Excel has no rows for store {only_store}.')
        groups = {only_store: groups[only_store]}

    out: dict[str, bytes] = {}
    for store, rows in groups.items():
        book = Workbook()
        sheet = book.active
        sheet.title = 'input'
        sheet.append(header)
        for row in rows:
            sheet.append(row)
        buf = io.BytesIO()
        book.save(buf)
        out[store] = buf.getvalue()
    return out


def delete_job_files(job) -> None:
    for field in (job.input_file, job.done_file, job.remaining_file):
        if field:
            field.delete(save=False)
