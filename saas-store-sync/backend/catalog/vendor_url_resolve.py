"""Resolve vendor product page URLs for catalog ingest, catalog UI, and scraping."""
from __future__ import annotations

import re

HEB_PRODUCT_DETAIL = 'https://www.heb.com/product-detail/{pid}'
COSTCO_AU_PRODUCT_BASE = 'https://www.costco.com.au/p/'


def heb_product_id_from_sku(sku: str) -> str | None:
    """Extract HEB PDP numeric id from a composite SKU (e.g. AHJH-150275-0311-PK3)."""
    sku = (sku or '').strip().replace('_', '-')
    if not sku:
        return None
    if sku.isdigit():
        ln = len(sku)
        if 5 <= ln <= 12:
            return sku
        return None

    candidates: list[tuple[int, str]] = []
    for idx, part in enumerate(re.split(r'[-/]+', sku)):
        if part.isdigit() and 5 <= len(part) <= 8:
            candidates.append((idx, part))

    if not candidates:
        pos = 0
        for m in re.finditer(r'\d{5,8}', sku):
            candidates.append((1000 + pos, m.group(0)))
            pos += 1

    if not candidates:
        return None

    def tier(length: int) -> int:
        return {7: 4, 6: 3, 8: 2, 5: 1}.get(length, 0)

    candidates.sort(key=lambda it: (-tier(len(it[1])), it[0]))
    return candidates[0][1]


def heb_url_from_vendor_id(vendor_id: str) -> str | None:
    vid = (vendor_id or '').strip()
    if vid.isdigit() and 5 <= len(vid) <= 12:
        return HEB_PRODUCT_DETAIL.format(pid=vid)
    return None


def is_heb_vendor_code(code: str) -> bool:
    c = (code or '').strip().lower()
    return c in ('heb', 'hebus') or c.startswith('heb_')


def normalize_heb_url(url: str) -> str | None:
    u = (url or '').strip()
    if not u or 'heb.com' not in u.lower():
        return None
    if not u.startswith('http'):
        u = 'https://' + u.lstrip('/')
    return u


def resolve_heb_product_url(
    product,
    *,
    store=None,
    vendor_url_raw: str | None = None,
    vendor_id_raw: str | None = None,
) -> str | None:
    """Best-effort HEB PDP URL for a ``Product`` (and optional catalog row fields)."""
    vendor = getattr(product, 'vendor', None)
    vcode = (getattr(vendor, 'code', '') or '').strip().lower()
    if not is_heb_vendor_code(vcode):
        return None

    from catalog.services import _normalize

    for candidate in (
        _normalize(vendor_url_raw),
        normalize_heb_url(getattr(product, 'vendor_url', None) or ''),
        heb_url_from_vendor_id(_normalize(vendor_id_raw) or ''),
        heb_url_from_vendor_id((getattr(product, 'vendor_sku', '') or '').strip()),
    ):
        if candidate:
            normalized = normalize_heb_url(candidate) or candidate
            if 'heb.com' in normalized.lower():
                return normalized

    sku = (getattr(product, 'vendor_sku', '') or '').strip()
    pid = heb_product_id_from_sku(sku)
    if pid:
        return HEB_PRODUCT_DETAIL.format(pid=pid)
    return None


def is_costco_vendor_code(code: str) -> bool:
    c = (code or '').strip().lower()
    return c in ('costcoau', 'costco_au', 'costco-au') or c.startswith('costco_')


def costco_product_id_from_value(value: str) -> str | None:
    """Extract Costco AU numeric product id from mixed values (173734, TFCO-173734-New, URLs)."""
    raw = (value or '').strip().replace('_', '-')
    if not raw:
        return None
    if raw.isdigit() and 5 <= len(raw) <= 12:
        return raw
    if 'costco.' in raw.lower() and '/p/' in raw.lower():
        path_after_p = raw.split('/p/', 1)[-1].rstrip('/')
        raw = path_after_p.split('/')[0]
    parts = [p for p in re.split(r'[-/]+', raw) if p]
    for part in parts:
        if part.isdigit() and 5 <= len(part) <= 12:
            return part
    match = re.search(r'\d{5,12}', raw)
    return match.group(0) if match else None


def normalize_costco_url(url: str) -> str | None:
    """Return the upload URL unchanged except for https prefix; never shorten slugs."""
    u = (url or '').strip()
    if not u:
        return None
    if not u.startswith('http'):
        u = 'https://' + u.lstrip('/')
    if 'costco.' not in u.lower():
        return None
    return u


def costco_url_from_vendor_id(vendor_id: str) -> str | None:
    pid = costco_product_id_from_value(vendor_id)
    if pid:
        return f'{COSTCO_AU_PRODUCT_BASE}{pid}'
    return None


def resolve_costco_product_url(
    product,
    *,
    vendor_url_raw: str | None = None,
    vendor_id_raw: str | None = None,
) -> str | None:
    """
    Costco URL priority (upload file wins):
    1. vendor_url_raw from catalog upload row
    2. product.vendor_url as stored (exact, no slug stripping)
    3. build from vendor_id / vendor_sku only when no URL provided
    """
    from catalog.services import _normalize

    upload_url = normalize_costco_url(_normalize(vendor_url_raw) or '')
    if upload_url:
        return upload_url

    stored = normalize_costco_url(getattr(product, 'vendor_url', None) or '')
    if stored:
        return stored

    built = costco_url_from_vendor_id(_normalize(vendor_id_raw) or '')
    if built:
        return built

    return costco_url_from_vendor_id((getattr(product, 'vendor_sku', '') or '').strip())


def resolve_vendor_url_for_row(
    vendor,
    row,
    product=None,
    *,
    vendor_sku: str | None = None,
) -> str | None:
    """Resolve vendor page URL from a catalog upload row (ingest). Upload Vendor URL always wins."""
    from catalog.services import _normalize

    vcode = (getattr(vendor, 'code', '') or '').strip().lower()
    upload_url = _normalize(getattr(row, 'vendor_url_raw', None))
    if upload_url:
        if is_costco_vendor_code(vcode):
            return normalize_costco_url(upload_url) or upload_url
        if is_heb_vendor_code(vcode):
            return normalize_heb_url(upload_url) or upload_url
        return upload_url

    if is_heb_vendor_code(vcode):
        if product is None and vendor_sku:
            class _Stub:
                pass

            stub = _Stub()
            stub.vendor = vendor
            stub.vendor_url = None
            stub.vendor_sku = vendor_sku
            product = stub
        return resolve_heb_product_url(
            product,
            vendor_url_raw=row.vendor_url_raw,
            vendor_id_raw=row.vendor_id_raw,
        )

    if is_costco_vendor_code(vcode):
        if product is None and vendor_sku:
            class _Stub:
                pass

            stub = _Stub()
            stub.vendor = vendor
            stub.vendor_url = None
            stub.vendor_sku = vendor_sku
            product = stub
        return resolve_costco_product_url(
            product,
            vendor_url_raw=row.vendor_url_raw,
            vendor_id_raw=row.vendor_id_raw,
        )

    return None


def sync_product_vendor_url_from_row(product, vendor, row) -> bool:
    """
    Persist vendor URL from upload row onto Product.
    When Vendor URL is present in the file, always overwrite Product.vendor_url.
    Returns True if product.vendor_url was updated.
    """
    from catalog.services import _normalize

    upload_url = _normalize(getattr(row, 'vendor_url_raw', None))
    if upload_url:
        vcode = (getattr(vendor, 'code', '') or '').strip().lower()
        if is_costco_vendor_code(vcode):
            url = normalize_costco_url(upload_url) or upload_url
        elif is_heb_vendor_code(vcode):
            url = normalize_heb_url(upload_url) or upload_url
        else:
            url = upload_url
    else:
        url = resolve_vendor_url_for_row(vendor, row, product=product)
        if not url:
            return False

    if (product.vendor_url or '') == url:
        return False
    product.vendor_url = url
    product.save(update_fields=['vendor_url'])
    return True


def latest_upload_vendor_url_for_mapping(product_mapping) -> str | None:
    """Most recent non-empty Vendor URL from catalog uploads for this listing."""
    from catalog.models import CatalogUploadRow
    from catalog.services import _normalize

    if product_mapping is None:
        return None

    qs = CatalogUploadRow.objects.filter(product_mapping=product_mapping)
    if not qs.exists() and product_mapping.product_id and product_mapping.store_id:
        qs = CatalogUploadRow.objects.filter(
            product_id=product_mapping.product_id,
            store_id=product_mapping.store_id,
        )
    raw = (
        qs.exclude(vendor_url_raw='')
        .order_by('-catalog_upload__created_at', '-row_number')
        .values_list('vendor_url_raw', flat=True)
        .first()
    )
    return _normalize(raw)
