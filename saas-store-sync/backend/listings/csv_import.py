"""Parse uploaded CSV / Excel listing templates into normalized row dicts.

Uses stdlib csv + openpyxl (no pandas dependency in this project).
"""
import csv
import io

# Maps human template headers -> internal field names.
COLUMN_MAP = {
    "action": "action",
    "product key": "product_key",
    "variant key": "variant_key",
    "title": "title",
    "description": "description",
    "brand": "brand",
    "category": "category",
    "sku": "sku",
    "barcode": "barcode",
    "image urls": "image_urls",
    "image url": "image_urls",
    "inventory": "inventory",
    "infinite quantity": "infinite_quantity",
    "original price": "original_price",
    "sale price": "sale_price",
}

TEMPLATE_HEADERS = [
    "Action",
    "Product Key",
    "Variant Key",
    "Title",
    "Description",
    "Brand",
    "Category",
    "SKU",
    "Barcode",
    "Image URLs",
    "Inventory",
    "Infinite Quantity",
    "Original Price",
    "Sale Price",
]

# Delete files only need the SKU (enough to remove the listing).
DELETE_TEMPLATE_HEADERS = ["Action", "SKU"]

VALID_ACTIONS = {"create", "mapped", "delete"}

_TRUE_VALUES = {"true", "1", "yes", "y", "t"}


def _coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUE_VALUES


def _cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _read_xlsx_rows(content: bytes) -> list[dict]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    try:
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)
        header = next(rows_iter, None) or ()
        headers = [_cell_text(h) for h in header]
        records = []
        for row in rows_iter:
            records.append({headers[i]: _cell_text(cell) for i, cell in enumerate(row) if i < len(headers)})
        return records
    finally:
        wb.close()


def _read_csv_rows(content: bytes) -> list[dict]:
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    return [
        {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
        for raw in reader
    ]


def parse_upload(filename: str, content: bytes) -> list[dict]:
    """Return a list of row dicts (1-based 'row_number' included; row 1 = header)."""
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        records = _read_xlsx_rows(content)
    else:
        records = _read_csv_rows(content)

    rows = []
    for idx, raw in enumerate(records, start=2):
        normalized = {}
        for header, value in raw.items():
            key = COLUMN_MAP.get(str(header).strip().lower())
            if key:
                normalized[key] = str(value).strip()
        if not any(normalized.values()):
            continue
        normalized["infinite_quantity"] = _coerce_bool(normalized.get("infinite_quantity", ""))
        action = str(normalized.get("action", "")).strip().lower()
        normalized["action"] = action if action in VALID_ACTIONS else ""
        normalized["row_number"] = idx
        rows.append(normalized)
    return rows


def build_template_csv(action: str = "create") -> str:
    """Template CSV for the given action. Delete only needs Action + SKU."""
    action = (action or "create").strip().lower()
    if action == "delete":
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=DELETE_TEMPLATE_HEADERS, lineterminator="\n")
        writer.writeheader()
        writer.writerow({"Action": "Delete", "SKU": "TSHIRT-001-BLACK-M"})
        return out.getvalue()

    sample = {
        "Action": "Mapped" if action == "mapped" else "Create",
        "Product Key": "TSHIRT-001",
        "Variant Key": "TSHIRT-001-BLACK-M",
        "Title": "Black T-Shirt (M)",
        "Description": "Soft 100% cotton tee.",
        "Brand": "MyBrand",
        "Category": "Apparel > T-Shirts",
        "SKU": "TSHIRT-001-BLACK-M",
        "Barcode": "123456789012",
        "Image URLs": "https://img.example.com/a.jpg|https://img.example.com/b.jpg",
        "Inventory": "10",
        "Infinite Quantity": "false",
        "Original Price": "29.99",
        "Sale Price": "24.99",
    }
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=TEMPLATE_HEADERS, lineterminator="\n")
    writer.writeheader()
    writer.writerow(sample)
    return out.getvalue()
