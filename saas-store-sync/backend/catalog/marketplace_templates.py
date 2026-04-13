"""
Marketplace-specific catalog CSV templates (headers, validation, export layout).

Upload files can use the minimal column set per marketplace; legacy wide templates
still work because all canonical columns are resolved from the header row.
"""
from __future__ import annotations

from typing import Any

from stores.models import Store

# Internal field keys matching EXPECTED_COLUMNS in services.py
INTERNAL_FIELDS = [
    'vendor name',
    'vendor id',
    'is variation',
    'variation id',
    'marketplace name',
    'store name',
    'marketplace parent sku',
    'marketplace child sku',
    'marketplace id',
    'vendor sku',
    'vendor url',
    'action',
    'pack qty',
    'prep fees',
    'shipping fees',
]


def store_marketplace_kind(store: Store) -> str:
    m = getattr(store, 'marketplace', None)
    if m is None and getattr(store, 'marketplace_id', None):
        from marketplace.models import Marketplace

        m = Marketplace.objects.filter(pk=store.marketplace_id).only('code', 'name').first()

    code = (getattr(m, 'code', '') or '').strip().lower()
    name = (getattr(m, 'name', '') or '').strip().lower()

    if code in ('reverb', 'walmart', 'sears'):
        return code
    if 'walmart' in name:
        return 'walmart'
    if 'sears' in name:
        return 'sears'
    if 'reverb' in name:
        return 'reverb'
    if name in ('reverb', 'walmart', 'sears'):
        return name
    return 'other'


def _norm_header_cell(h: Any) -> str:
    return (str(h) or '').strip().lower().replace('_', ' ')


def col_index(header: list, col_name: str) -> int | None:
    """
    Resolve column index. Exact match first, then header contains full column phrase.
    Short names like "sku" only match exact header "SKU", never a substring of longer headers.
    """
    col_lower = col_name.lower().replace('_', ' ').strip()
    if col_lower == 'sku':
        for i, h in enumerate(header):
            if _norm_header_cell(h) == 'sku':
                return i
        return None
    for i, h in enumerate(header):
        if _norm_header_cell(h) == col_lower:
            return i
    for i, h in enumerate(header):
        h_clean = _norm_header_cell(h)
        if col_lower in h_clean:
            return i
    return None


def build_field_indices(header: list, store: Store) -> dict[str, int | None]:
    """Map internal field keys to column indices (with marketplace-aware SKU alias)."""
    idx: dict[str, int | None] = {k: col_index(header, k) for k in INTERNAL_FIELDS}

    sku_i = col_index(header, 'sku')
    if sku_i is None:
        # "Listing SKU" etc.
        for alt in ('listing sku', 'marketplace sku'):
            sku_i = col_index(header, alt)
            if sku_i is not None:
                break

    kind = store_marketplace_kind(store)
    if sku_i is not None:
        if kind == 'reverb':
            if idx['marketplace parent sku'] is None:
                idx['marketplace parent sku'] = sku_i
        elif kind == 'walmart':
            if idx['marketplace child sku'] is None:
                idx['marketplace child sku'] = sku_i
            if idx['marketplace parent sku'] is None:
                idx['marketplace parent sku'] = sku_i
        elif kind == 'sears':
            if idx['vendor sku'] is None:
                idx['vendor sku'] = sku_i
        else:
            if idx['marketplace parent sku'] is None:
                idx['marketplace parent sku'] = sku_i
            elif idx['marketplace child sku'] is None:
                idx['marketplace child sku'] = sku_i
            elif idx['vendor sku'] is None:
                idx['vendor sku'] = sku_i

    return idx


def validate_marketplace_headers(indices: dict[str, int | None], store: Store) -> str | None:
    """Return an error message if required columns for this marketplace are missing."""

    def _req(key: str) -> bool:
        return indices.get(key) is not None

    if not _req('vendor name') or not _req('store name') or not _req('action'):
        return 'Required columns missing: Vendor Name, Store Name, and Action are required.'

    kind = store_marketplace_kind(store)

    if kind == 'reverb':
        has_listing_sku = _req('marketplace parent sku')
        has_vendor_ref = _req('vendor url') or _req('vendor id')
        if not has_listing_sku:
            return (
                'Reverb uploads require a listing SKU column: use "SKU" or "Marketplace Parent SKU".'
            )
        if not has_vendor_ref:
            return 'Reverb uploads require "Vendor URL" and/or "Vendor ID" (for vendor lookup and scraping).'

    elif kind == 'walmart':
        for col, label in (
            ('pack qty', 'Pack QTY'),
            ('prep fees', 'Prep Fees'),
            ('shipping fees', 'Shipping Fees'),
        ):
            if not _req(col):
                return f'Walmart uploads require column: {label}'
        has_sku = (
            _req('marketplace child sku')
            or _req('marketplace parent sku')
            or _req('vendor sku')
        )
        if not has_sku:
            return (
                'Walmart uploads require a SKU column: use "SKU", "Marketplace Child SKU", '
                '"Marketplace Parent SKU", or "Vendor SKU".'
            )
        if not _req('vendor url') and not _req('vendor id'):
            return 'Walmart uploads require "Vendor URL" and/or "Vendor ID".'

    elif kind == 'sears':
        required = [
            ('vendor id', 'Vendor ID'),
            ('is variation', 'Is Variation'),
            ('variation id', 'Variation ID'),
            ('marketplace name', 'Marketplace Name'),
            ('marketplace parent sku', 'Marketplace Parent SKU'),
            ('marketplace child sku', 'Marketplace Child SKU'),
            ('marketplace id', 'Marketplace ID'),
            ('vendor sku', 'Vendor SKU'),
            ('vendor url', 'Vendor URL'),
        ]
        for key, label in required:
            if not _req(key):
                return f'Sears uploads require column: {label}'

    return None


def sample_template_filename(store: Store) -> str:
    return sample_template_filename_for_kind(store_marketplace_kind(store))


def sample_template_filename_for_kind(kind: str) -> str:
    if kind == 'other':
        return 'catalog_upload_template.csv'
    return f'catalog_upload_template_{kind}.csv'


def sample_template_rows(store: Store) -> tuple[list[str], list[list[str]]]:
    """CSV header row + example data rows for the store's marketplace."""
    return sample_template_rows_for_kind(store_marketplace_kind(store))


def sample_template_rows_for_kind(kind: str) -> tuple[list[str], list[list[str]]]:
    """CSV header row + example rows when store is unknown or marketplace is generic."""
    if kind == 'walmart':
        headers = [
            'Vendor Name',
            'Vendor ID',
            'Marketplace Name',
            'Store Name',
            'SKU',
            'Vendor URL',
            'Action',
            'Pack QTY',
            'Prep Fees',
            'Shipping Fees',
        ]
        rows = [
            [
                'Amazon',
                '',
                'Walmart',
                'My Store',
                'WM-ITEM-001',
                'https://www.amazon.com/dp/B0TEST123',
                'Add',
                '1',
                '2.50',
                '5.00',
            ],
            [
                'Amazon',
                '',
                'Walmart',
                'My Store',
                'WM-ITEM-002',
                '',
                'Update',
                '2',
                '1.00',
                '3.75',
            ],
        ]
        return headers, rows

    if kind == 'sears':
        headers = [
            'Vendor Name',
            'Vendor ID',
            'Is Variation',
            'Variation ID',
            'Marketplace Name',
            'Store Name',
            'Marketplace Parent SKU',
            'Marketplace Child SKU',
            'Marketplace ID',
            'Vendor SKU',
            'Vendor URL',
            'Action',
        ]
        rows = [
            [
                'Amazon',
                'B0TEST123',
                'No',
                '',
                'Sears',
                'My Store',
                'PARENT-1',
                'CHILD-1',
                '',
                'V-SKU-1',
                'https://www.amazon.com/dp/B0TEST123',
                'Add',
            ],
        ]
        return headers, rows

    if kind == 'other':
        headers = [
            'Vendor Name',
            'Vendor ID',
            'Is Variation',
            'Variation ID',
            'Marketplace Name',
            'Store Name',
            'Marketplace Parent SKU',
            'Marketplace Child SKU',
            'Marketplace ID',
            'Vendor SKU',
            'Vendor URL',
            'Action',
            'Pack QTY',
            'Prep Fees',
            'Shipping Fees',
        ]
        rows = [
            [
                'Amazon',
                '',
                'No',
                '',
                'Reverb',
                'My Store',
                'LISTING-SKU-001',
                '',
                '',
                '',
                'https://www.amazon.com/dp/B0TEST123',
                'Add',
                '',
                '',
                '',
            ],
        ]
        return headers, rows

    # reverb: minimal template
    headers = [
        'Vendor Name',
        'Vendor ID',
        'Marketplace Name',
        'Store Name',
        'SKU',
        'Vendor URL',
        'Action',
    ]
    rows = [
        [
            'Amazon',
            '',
            'Reverb',
            'My Store',
            'LISTING-SKU-001',
            'https://www.amazon.com/dp/B0TEST123',
            'Add',
        ],
        [
            'eBay',
            '123456789',
            'Reverb',
            'My Store',
            'LISTING-SKU-002',
            '',
            'Add',
        ],
    ]
    return headers, rows


def export_headers_for_store(store: Store, *, include_posted: bool = False) -> list[str]:
    """Column headers for download / error CSV matching this store's template."""
    headers, _ = sample_template_rows(store)
    kind = store_marketplace_kind(store)
    if kind == 'other':
        headers = [
            'Vendor Name',
            'Vendor ID',
            'Is Variation',
            'Variation ID',
            'Marketplace Name',
            'Store Name',
            'Marketplace Parent SKU',
            'Marketplace Child SKU',
            'Marketplace ID',
            'Vendor SKU',
            'Vendor URL',
            'Action',
            'Pack QTY',
            'Prep Fees',
            'Shipping Fees',
        ]
    if include_posted:
        return [*headers, 'Posted Price', 'Posted Inventory']
    return headers


def upload_row_to_cells(
    r,
    store: Store,
    *,
    include_posted: bool = False,
    posted_price: str = '',
    posted_inventory: str = '',
) -> list[str]:
    """Flatten a CatalogUploadRow (+ optional mapping) to CSV cells for export."""
    kind = store_marketplace_kind(store)

    def sku_reverb() -> str:
        return (
            (r.marketplace_parent_sku_raw or '').strip()
            or (r.marketplace_child_sku_raw or '').strip()
            or (r.vendor_sku_raw or '').strip()
        )

    def sku_walmart() -> str:
        return (
            (r.marketplace_child_sku_raw or '').strip()
            or (r.marketplace_parent_sku_raw or '').strip()
            or (r.vendor_sku_raw or '').strip()
        )

    if kind == 'walmart':
        cells = [
            r.vendor_name_raw or '',
            r.vendor_id_raw or '',
            r.marketplace_name_raw or '',
            r.store_name_raw or '',
            sku_walmart(),
            r.vendor_url_raw or '',
            r.action_raw or 'Add',
            r.pack_qty_raw or '',
            r.prep_fees_raw or '',
            r.shipping_fees_raw or '',
        ]
    elif kind == 'sears':
        cells = [
            r.vendor_name_raw or '',
            r.vendor_id_raw or '',
            r.is_variation_raw or '',
            r.variation_id_raw or '',
            r.marketplace_name_raw or '',
            r.store_name_raw or '',
            r.marketplace_parent_sku_raw or '',
            r.marketplace_child_sku_raw or '',
            r.marketplace_id_raw or '',
            r.vendor_sku_raw or '',
            r.vendor_url_raw or '',
            r.action_raw or 'Add',
        ]
    elif kind == 'reverb':
        cells = [
            r.vendor_name_raw or '',
            r.vendor_id_raw or '',
            r.marketplace_name_raw or '',
            r.store_name_raw or '',
            sku_reverb(),
            r.vendor_url_raw or '',
            r.action_raw or 'Add',
        ]
    else:
        cells = [
            r.vendor_name_raw or '',
            r.vendor_id_raw or '',
            r.is_variation_raw or '',
            r.variation_id_raw or '',
            r.marketplace_name_raw or '',
            r.store_name_raw or '',
            r.marketplace_parent_sku_raw or '',
            r.marketplace_child_sku_raw or '',
            r.marketplace_id_raw or '',
            r.vendor_sku_raw or '',
            r.vendor_url_raw or '',
            r.action_raw or 'Add',
            r.pack_qty_raw or '',
            r.prep_fees_raw or '',
            r.shipping_fees_raw or '',
        ]

    if include_posted:
        cells.extend([posted_price, posted_inventory])
    return cells
