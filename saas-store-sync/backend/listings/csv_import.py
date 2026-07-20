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
    "options": "options",
    "option": "options",
    "variant options": "options",
    "currency": "currency",
    "price": "sale_price",
    "vendor url": "vendor_url",
    "vendor_url": "vendor_url",
    "source url": "vendor_url",
    "source link": "vendor_url",
    "vendor link": "vendor_url",
    "vendor id": "vendor_id",
    "vendor_id": "vendor_id",
    "vendor name": "vendor_name",
    "vendor_name": "vendor_name",
    "source vendor": "vendor_name",
    "marketplace name": "marketplace_name",
    "marketplace_name": "marketplace_name",
    "marketplace": "marketplace_name",
    "store name": "store_name",
    "store_name": "store_name",
    "store": "store_name",
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

# System routing columns first (vendor identity together), then marketplace payload.
LASOO_TEMPLATE_HEADERS = [
    "Vendor Name",
    "Vendor URL",
    "Vendor ID",
    "Marketplace Name",
    "Store Name",
    "Action",
    "Product Key",
    "Variant Key",
    "SKU",
    "Options",
    "Title",
    "Description",
    "Brand",
    "Category",
    "Barcode",
    "Image URLs",
    "Inventory",
    "Infinite Quantity",
    "Original Price",
    "Sale Price",
]

# Internal field -> template header for export (Lasoo Create/Mapped files).
LASOO_EXPORT_FIELDS = [
    ("vendor_name", "Vendor Name"),
    ("vendor_url", "Vendor URL"),
    ("vendor_id", "Vendor ID"),
    ("marketplace_name", "Marketplace Name"),
    ("store_name", "Store Name"),
    ("action", "Action"),
    ("product_key", "Product Key"),
    ("variant_key", "Variant Key"),
    ("sku", "SKU"),
    ("options", "Options"),
    ("title", "Title"),
    ("description", "Description"),
    ("brand", "Brand"),
    ("category", "Category"),
    ("barcode", "Barcode"),
    ("image_urls", "Image URLs"),
    ("inventory", "Inventory"),
    ("infinite_quantity", "Infinite Quantity"),
    ("original_price", "Original Price"),
    ("sale_price", "Sale Price"),
]

REVERB_EXPORT_FIELDS = [
    ("vendor_name", "Vendor Name"),
    ("vendor_url", "Vendor URL"),
    ("marketplace_name", "Marketplace Name"),
    ("store_name", "Store Name"),
    ("action", "Action"),
    ("sku", "SKU"),
    ("title", "Title"),
    ("make", "Make"),
    ("model", "Model"),
    ("description", "Description"),
    ("finish", "Finish"),
    ("year", "Year"),
    ("condition", "Condition"),
    ("category", "Category"),
    ("sale_price", "Price"),
    ("currency", "Currency"),
    ("inventory", "Inventory"),
    ("barcode", "UPC"),
    ("upc_does_not_apply", "UPC Does Not Apply"),
    ("image_urls", "Photo URLs"),
    ("publish_status", "status"),
    ("free_shipping", "free_shipping"),
]

REVERB_TEMPLATE_HEADERS = [
    "Vendor Name",
    "Vendor URL",
    "Marketplace Name",
    "Store Name",
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

# Headers that strongly indicate the real column row (vs a banner like
# "System Required Headers").
_HEADER_MARKERS = {
    "action",
    "sku",
    "product key",
    "variant key",
    "vendor name",
    "marketplace name",
    "store name",
    "title",
    "sale price",
    "original price",
}


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


def _normalize_action(raw: str) -> str:
    """Accept plain Create/Mapped/Delete or instructional cells like 'Create - …'."""
    action = str(raw or "").strip().lower()
    if action in VALID_ACTIONS:
        return action
    for valid in ("create", "mapped", "delete"):
        if action.startswith(valid):
            return valid
    return ""


def _header_score(cells: list[str]) -> int:
    score = 0
    for cell in cells:
        key = str(cell or "").strip().lower()
        if key in _HEADER_MARKERS or key in COLUMN_MAP:
            score += 1
    return score


def _pick_header_row(raw_rows: list[tuple]) -> tuple[list[str], list[tuple]]:
    """Return (headers, data_rows). Skips banner rows used in Excel templates."""
    if not raw_rows:
        return [], []
    best_idx, best_score = 0, -1
    scan_limit = min(5, len(raw_rows))
    for idx in range(scan_limit):
        cells = [_cell_text(c) for c in raw_rows[idx]]
        score = _header_score(cells)
        if score > best_score:
            best_idx, best_score = idx, score
    if best_score < 2:
        best_idx = 0
    headers = [_cell_text(c) for c in raw_rows[best_idx]]
    return headers, raw_rows[best_idx + 1 :]


def _records_from_header_and_rows(headers: list[str], data_rows: list[tuple]) -> list[dict]:
    """Build row dicts. Duplicate headers keep the last non-empty value."""
    records = []
    for row in data_rows:
        record: dict[str, str] = {}
        for i, header in enumerate(headers):
            if not header:
                continue
            value = _cell_text(row[i]) if i < len(row) else ""
            if header in record and not value:
                continue
            record[header] = value
        records.append(record)
    return records


def _read_xlsx_rows(content: bytes) -> list[dict]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    try:
        ws = wb.active
        raw_rows = [tuple(row) for row in ws.iter_rows(values_only=True)]
        headers, data_rows = _pick_header_row(raw_rows)
        return _records_from_header_and_rows(headers, data_rows)
    finally:
        wb.close()


def _read_csv_rows(content: bytes) -> list[dict]:
    text = content.decode("utf-8-sig", errors="replace")
    # Detect banner: if first line isn't a real header, use the highest-scoring line.
    sample = text.splitlines()[:5]
    if len(sample) >= 2:
        scored = []
        for i, line in enumerate(sample):
            try:
                cells = next(csv.reader([line]))
            except Exception:  # noqa: BLE001
                cells = [line]
            scored.append((_header_score([_cell_text(c) for c in cells]), i))
        scored.sort(reverse=True)
        best_score, best_idx = scored[0]
        if best_score >= 2 and best_idx > 0:
            # Rebuild CSV without the banner lines.
            lines = text.splitlines(keepends=True)
            text = "".join(lines[best_idx:])
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
                # Prefer first non-empty when duplicate logical columns collide.
                existing = normalized.get(key)
                if existing and not str(value).strip():
                    continue
                if existing and str(value).strip():
                    normalized[key] = str(value).strip()
                    continue
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
        normalized["action"] = _normalize_action(normalized.get("action", ""))
        normalized["row_number"] = idx
        rows.append(normalized)
    return rows


def _marketplace_label(store) -> str:
    mp = getattr(store, "marketplace", None)
    return (getattr(mp, "name", None) or getattr(mp, "code", None) or "").strip()


def build_template_csv(action: str = "create", store=None) -> str:
    """Template CSV for the given action. Reverb stores get Reverb columns."""
    action = (action or "create").strip().lower()
    if action == "delete":
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=DELETE_TEMPLATE_HEADERS, lineterminator="\n")
        writer.writeheader()
        writer.writerow({"Action": "Delete", "SKU": "AMH-EXAMPLE-001"})
        return out.getvalue()

    store_name = (getattr(store, "name", None) or "").strip()
    marketplace_name = _marketplace_label(store) if store is not None else ""

    if _is_reverb_store(store):
        sample = {
            "Vendor Name": "Amazon US",
            "Marketplace Name": marketplace_name or "Reverb",
            "Store Name": store_name,
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
            "Vendor URL": "https://www.amazon.com/dp/EXAMPLE",
            "Photo URLs": "https://example.com/photo1.jpg|https://example.com/photo2.jpg",
            "status": "draft",
            "free_shipping": "TRUE",
        }
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=REVERB_TEMPLATE_HEADERS, lineterminator="\n")
        writer.writeheader()
        writer.writerow(sample)
        return out.getvalue()

    sample_black = {
        "Vendor Name": "Nora Inventory",
        "Marketplace Name": marketplace_name or "Lasoo",
        "Store Name": store_name,
        "Action": "Mapped" if action == "mapped" else "Create",
        "Product Key": "JJ-XZ216",
        "Variant Key": "JJ-XZ216-BK",
        "SKU": "JJ-XZ216-BK",
        "Options": "Colour=Black",
        "Title": "Example Product — Black",
        "Description": "Soft 100% cotton tee. Same Product Key groups colour variants on Lasoo.",
        "Brand": "MyBrand",
        "Category": "Apparel > T-Shirts",
        "Barcode": "123456789012",
        "Vendor URL": "https://www.example-vendor.com/product/jj-xz216-bk",
        "Image URLs": "https://img.example.com/a.jpg|https://img.example.com/b.jpg",
        "Inventory": "10",
        "Infinite Quantity": "false",
        "Original Price": "29.99",
        "Sale Price": "24.99",
        "Vendor ID": "8FNZ100-DL-G1",
    }
    sample_gold = {
        **sample_black,
        "Variant Key": "JJ-XZ216-GD",
        "SKU": "JJ-XZ216-GD",
        "Options": "Colour=Gold",
        "Title": "Example Product — Gold",
        "Vendor URL": "https://www.example-vendor.com/product/jj-xz216-gd",
        "Vendor ID": "8FNZ100-DL-G2",
        "Barcode": "123456789013",
    }
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=LASOO_TEMPLATE_HEADERS, lineterminator="\n")
    writer.writeheader()
    writer.writerow(sample_black)
    writer.writerow(sample_gold)
    return out.getvalue()


def snapshot_row_fields(row: dict) -> dict:
    """Copy template-relevant fields for later export (JSON-safe scalars)."""
    out = {}
    for key, value in (row or {}).items():
        if key in ("row_number", "errors", "valid", "imported", "fields"):
            continue
        if isinstance(value, bool):
            out[key] = "true" if value else "false"
        elif value is None:
            out[key] = ""
        else:
            out[key] = str(value).strip() if not isinstance(value, (int, float)) else value
    # Prefer human Action label in export snapshot
    action = str(out.get("action") or "").strip().lower()
    if action in VALID_ACTIONS:
        out["action"] = action.capitalize()
    return out


def export_field_specs(store=None) -> list[tuple[str, str]]:
    """Return (internal_key, header) pairs for the store's marketplace template."""
    if _is_reverb_store(store):
        return list(REVERB_EXPORT_FIELDS)
    return list(LASOO_EXPORT_FIELDS)
