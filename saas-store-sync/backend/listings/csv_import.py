"""Parse uploaded CSV / Excel listing templates into normalized row dicts.

Uses stdlib csv + openpyxl (no pandas dependency in this project).
Supports Lasoo templates and Reverb-specific templates (by store marketplace).
"""
import csv
import io

# Maps human template headers -> internal field names (Lasoo + Reverb shared).
COLUMN_MAP = {
    "action": "action",
    "product key": "product_key",
    "variant key": "variant_key",
    "title": "title",
    "description": "description",
    "brand": "brand",
    "make": "make",
    "model": "model",
    "finish": "finish",
    "year": "year",
    "category": "category",
    "category uuid": "category_uuid",
    "condition": "condition",
    "condition uuid": "condition_uuid",
    "sku": "sku",
    "barcode": "barcode",
    "upc": "barcode",
    "upc does not apply": "upc_does_not_apply",
    "currency": "currency",
    "price": "sale_price",
    "vendor url": "vendor_url",
    "vendor_url": "vendor_url",
    "source url": "vendor_url",
    "source link": "vendor_url",
    "image urls": "image_urls",
    "image url": "image_urls",
    "photos": "image_urls",
    "photo urls": "image_urls",
    "inventory": "inventory",
    "infinite quantity": "infinite_quantity",
    "original price": "original_price",
    "sale price": "sale_price",
    # Reverb: draft vs live (publish flag); free shipping default true
    "status": "publish_status",
    "publish status": "publish_status",
    "listing status": "publish_status",
    "free_shipping": "free_shipping",
    "free shipping": "free_shipping",
}

LASOO_TEMPLATE_HEADERS = [
    "Action",
    "Product Key",
    "Variant Key",
    "Title",
    "Description",
    "Brand",
    "Category",
    "SKU",
    "Barcode",
    "Vendor URL",
    "Image URLs",
    "Inventory",
    "Infinite Quantity",
    "Original Price",
    "Sale Price",
]

REVERB_TEMPLATE_HEADERS = [
    "Action",
    "SKU",
    "Title",
    "Make",
    "Model",
    "Description",
    "Finish",
    "Year",
    "Condition",
    "Category",
    "Price",
    "Currency",
    "Inventory",
    "UPC",
    "UPC Does Not Apply",
    "Photo URLs",
    "status",
    "free_shipping",
]

# Back-compat alias
TEMPLATE_HEADERS = LASOO_TEMPLATE_HEADERS

# Delete files only need the SKU (enough to remove the listing).
DELETE_TEMPLATE_HEADERS = ["Action", "SKU"]

VALID_ACTIONS = {"create", "mapped", "delete"}

_TRUE_VALUES = {"true", "1", "yes", "y", "t"}


def _is_reverb_store(store) -> bool:
    try:
        code = (getattr(getattr(store, "marketplace", None), "code", None) or "").strip().lower()
    except Exception:  # noqa: BLE001
        code = ""
    return code == "reverb"


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
        normalized["upc_does_not_apply"] = _coerce_bool(normalized.get("upc_does_not_apply", ""))
        # free_shipping defaults to True when blank (usual Reverb practice)
        fs_raw = normalized.get("free_shipping")
        if fs_raw is None or str(fs_raw).strip() == "":
            normalized["free_shipping"] = True
        else:
            normalized["free_shipping"] = _coerce_bool(fs_raw)
        # status: draft | live (blank → draft)
        ps = str(normalized.get("publish_status") or "").strip().lower()
        if ps in ("live", "published", "publish"):
            normalized["publish_status"] = "live"
        else:
            normalized["publish_status"] = "draft"
        # Reverb aliases
        if normalized.get("make") and not normalized.get("brand"):
            normalized["brand"] = normalized["make"]
        if normalized.get("condition") and not normalized.get("condition_uuid"):
            normalized["condition_uuid"] = normalized["condition"]
        if normalized.get("category_uuid") and not normalized.get("category"):
            normalized["category"] = normalized["category_uuid"]
        if normalized.get("category") and not normalized.get("category_uuid"):
            normalized["category_uuid"] = normalized["category"]
        if normalized.get("sale_price") and not normalized.get("original_price"):
            normalized["original_price"] = normalized["sale_price"]
        action = str(normalized.get("action", "")).strip().lower()
        normalized["action"] = action if action in VALID_ACTIONS else ""
        normalized["row_number"] = idx
        rows.append(normalized)
    return rows


def build_template_csv(action: str = "create", store=None) -> str:
    """Template CSV for the given action. Reverb stores get Reverb columns."""
    action = (action or "create").strip().lower()
    if action == "delete":
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=DELETE_TEMPLATE_HEADERS, lineterminator="\n")
        writer.writeheader()
        writer.writerow({"Action": "Delete", "SKU": "AMH-EXAMPLE-001"})
        return out.getvalue()

    if _is_reverb_store(store):
        sample = {
            "Action": "Mapped" if action == "mapped" else "Create",
            "SKU": "AMH-EXAMPLE-001",
            "Title": "Example Guitar Pedal",
            "Make": "Unbranded",
            "Model": "EXAMPLE-001",
            "Description": "Great pedal in excellent condition.",
            "Finish": "",
            "Year": "",
            "Condition": "Brand New",
            "Category": "Accessories / Cables",
            "Price": "49.99",
            "Currency": "USD",
            "Inventory": "1",
            "UPC": "",
            "UPC Does Not Apply": "true",
            "Photo URLs": "https://example.com/photo1.jpg|https://example.com/photo2.jpg",
            "status": "draft",
            "free_shipping": "TRUE",
        }
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=REVERB_TEMPLATE_HEADERS, lineterminator="\n")
        writer.writeheader()
        writer.writerow(sample)
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
        "Vendor URL": "https://www.example-vendor.com/product/tshirt-001",
        "Image URLs": "https://img.example.com/a.jpg|https://img.example.com/b.jpg",
        "Inventory": "10",
        "Infinite Quantity": "false",
        "Original Price": "29.99",
        "Sale Price": "24.99",
    }
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=LASOO_TEMPLATE_HEADERS, lineterminator="\n")
    writer.writeheader()
    writer.writerow(sample)
    return out.getvalue()
