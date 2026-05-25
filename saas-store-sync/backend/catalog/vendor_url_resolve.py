"""Resolve vendor product page URLs for catalog ingest and scraping."""
from __future__ import annotations

import re

HEB_PRODUCT_DETAIL = 'https://www.heb.com/product-detail/{pid}'


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
