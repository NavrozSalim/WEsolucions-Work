"""
Catalog services: upload parsing, validation, and value normalization.
"""
import csv
import io
from typing import Any

from django.db import transaction

from .models import CatalogUpload, CatalogUploadRow
from .marketplace_templates import INTERNAL_FIELDS, build_field_indices, validate_marketplace_headers
from .reverb_catalog import store_is_reverb, vendor_is_ebay
from stores.models import Store
from vendor.models import Vendor

# Backward-compatible alias (canonical list lives in marketplace_templates)
EXPECTED_COLUMNS = INTERNAL_FIELDS


# Canonical vendor codes the catalog upload accepts. The user-facing vendor
# labels in the sample templates and dropdowns must match these names.
#
# Kogan is deliberately excluded — Kogan is a *marketplace* (see the Kogan
# Google Sheets adapter in ``store_adapters/kogan_adapter.py`` and the
# ``kogan`` row in the ``Marketplace`` table), not a product vendor.
CANONICAL_VENDOR_NAMES = (
    'AmazonUS', 'AmazonAU', 'EbayUS', 'EbayAU',
    'VevorAU', 'CostcoAU', 'HebUS',
)

# Map of normalized user input -> canonical Vendor.code. Anything not in this
# map still falls back to a direct Vendor.name/Vendor.code lookup, so legacy
# data keeps working, but the recommended / enforced list is the canonical one.
_VENDOR_ALIAS_TO_CODE: dict[str, str] = {
    'amazonus': 'amazonus', 'amazonusa': 'amazonus', 'amazon us': 'amazonus',
    'amazon-us': 'amazonus', 'amazon_us': 'amazonus', 'amazon': 'amazonus',
    'amazonau': 'amazonau', 'amazon au': 'amazonau',
    'amazon-au': 'amazonau', 'amazon_au': 'amazonau',
    'ebayus': 'ebayus', 'ebay us': 'ebayus', 'ebay-us': 'ebayus',
    'ebay_us': 'ebayus', 'ebay': 'ebayus',
    'ebayau': 'ebayau', 'ebay au': 'ebayau', 'ebay-au': 'ebayau', 'ebay_au': 'ebayau',
    'vevorau': 'vevorau', 'vevor au': 'vevorau', 'vevor-au': 'vevorau',
    'vevor_au': 'vevorau', 'vevor': 'vevorau',
    'costcoau': 'costcoau', 'costco au': 'costcoau', 'costco-au': 'costcoau',
    'costco_au': 'costcoau', 'costco': 'costcoau',
    'hebus': 'hebus', 'heb us': 'hebus', 'heb-us': 'hebus',
    'heb_us': 'hebus', 'heb': 'hebus',
}


def resolve_canonical_vendor_code(raw: str) -> str | None:
    """Normalize user-typed vendor text to a canonical Vendor.code."""
    if not raw:
        return None
    key = str(raw).strip().lower()
    return _VENDOR_ALIAS_TO_CODE.get(key)


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

    store = Store.objects.select_related('marketplace').get(pk=store.pk)
    indices = build_field_indices(header, store)
    header_err = validate_marketplace_headers(indices, store)
    if header_err:
        return None, [header_err]

    store_name_lower = store.name.lower()
    vendors_by_name = {v.name.lower(): v for v in Vendor.objects.all()}
    vendors_by_name.update({v.code.lower(): v for v in Vendor.objects.all()})
    vendors_by_code = {v.code.lower(): v for v in Vendor.objects.all()}
    is_reverb = store_is_reverb(store)

    # pack_qty / prep_fees / shipping_fees are required on Add/Update rows
    # whenever the store's pricing settings include at least one tier with
    # ``margin_type='fixed'`` — the fixed-profit formula cannot be evaluated
    # without them. This is marketplace-agnostic: any vendor can use the
    # fixed method, not just Walmart.
    from stores.models import StorePriceRangeMargin
    requires_fixed_inputs = StorePriceRangeMargin.objects.filter(
        price_settings__store=store,
        margin_type='fixed',
    ).exists()

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
            pack_qty_raw = _val(row, 'pack qty')
            prep_fees_raw = _val(row, 'prep fees')
            shipping_fees_raw = _val(row, 'shipping fees')

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

            vn = _normalize(vendor_name_raw)
            vendor = vendors_by_name.get(vn.lower()) if vn else None
            if not vendor and vn:
                canon = resolve_canonical_vendor_code(vn)
                if canon:
                    vendor = vendors_by_code.get(canon)
            if not vendor and vn:
                errors.append(
                    f"Row {row_num}: Unknown vendor '{vendor_name_raw}'. "
                    f"Use one of: {', '.join(CANONICAL_VENDOR_NAMES)}."
                )
                continue
            is_ebay_v = vendor_is_ebay(vendor, vendor_name_raw)

            if action_norm == 'add':
                if is_ebay_v:
                    if _normalize(marketplace_parent_sku_raw) is None:
                        errors.append(
                            f"Row {row_num}: eBay vendor rows require Marketplace Parent SKU for Add "
                            f"(for marketplace listing / push; Child SKU, Marketplace ID, Vendor SKU may be N/A)"
                        )
                        continue
                    if _normalize(vendor_url_raw) is None and _normalize(vendor_id_raw) is None:
                        errors.append(
                            f"Row {row_num}: eBay vendor rows require Vendor URL or Vendor ID "
                            f"(eBay item id for https://www.ebay.com/itm/...) for Add"
                        )
                        continue
                elif is_reverb:
                    if _normalize(marketplace_parent_sku_raw) is None:
                        errors.append(
                            f"Row {row_num}: Reverb stores require SKU (or Marketplace Parent SKU) for Add "
                            f"(Reverb listing SKU; other marketplace columns may be N/A)"
                        )
                        continue
                else:
                    sku_val = (
                        _normalize(vendor_sku_raw)
                        or _normalize(marketplace_child_sku_raw)
                        or _normalize(vendor_id_raw)
                        or _normalize(marketplace_parent_sku_raw)
                    )
                    if not sku_val:
                        errors.append(
                            f"Row {row_num}: Vendor SKU, Marketplace Child SKU, Vendor ID, or Marketplace Parent SKU required for Add"
                        )
                        continue
            if requires_fixed_inputs and action_norm in ('add', 'update'):
                if _normalize(pack_qty_raw) is None:
                    errors.append(
                        f"Row {row_num}: Pack QTY required for {action_norm.title()} "
                        f"(store uses a fixed pricing tier)"
                    )
                    continue
                if _normalize(prep_fees_raw) is None:
                    errors.append(
                        f"Row {row_num}: Prep Fees required for {action_norm.title()} "
                        f"(store uses a fixed pricing tier)"
                    )
                    continue
                if _normalize(shipping_fees_raw) is None:
                    errors.append(
                        f"Row {row_num}: Shipping Fees required for {action_norm.title()} "
                        f"(store uses a fixed pricing tier)"
                    )
                    continue
            elif action_norm == 'delete':
                id_val = (
                    _normalize(marketplace_id_raw)
                    or _normalize(vendor_sku_raw)
                    or _normalize(vendor_id_raw)
                    or _normalize(marketplace_child_sku_raw)
                    or _normalize(marketplace_parent_sku_raw)
                )
                if not id_val:
                    errors.append(
                        f"Row {row_num}: Delete requires Marketplace ID, Vendor SKU, Vendor ID, "
                        f"Marketplace Child SKU, or Marketplace Parent SKU to find the product"
                    )
                    continue

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
                pack_qty_raw=pack_qty_raw,
                prep_fees_raw=prep_fees_raw,
                shipping_fees_raw=shipping_fees_raw,
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
