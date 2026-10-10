"""Columns a discovery job can write, per marketplace and mode."""

from __future__ import annotations

MARKETPLACES = (
    ('amazon_us', 'Amazon US', 'US'),
    ('amazon_au', 'Amazon AU', 'AU'),
    ('ebay_us', 'eBay US', 'US'),
    ('ebay_au', 'eBay AU', 'AU'),
)

MODES = (
    ('category', 'Category'),
    ('product', 'Product details'),
)

AMAZON_CATEGORY = (
    'asin',
    'url',
    'title',
    'price',
    'rating',
    'review_count',
    'image',
    'category',
)

AMAZON_PRODUCT = (
    'asin',
    'url',
    'title',
    'brand',
    'price',
    'rating',
    'review_count',
    'availability',
    'inventory',
    'delivery_date',
    'ships_from',
    'sold_by',
    'description',
    'bullets',
    'images',
    'category',
    'seller',
)

EBAY_CATEGORY = (
    'item_id',
    'url',
    'title',
    'price',
    'rating',
    'review_count',
    'image',
    'condition',
    'category',
)

EBAY_PRODUCT = (
    'item_id',
    'url',
    'title',
    'brand',
    'price',
    'rating',
    'review_count',
    'availability',
    'inventory',
    'delivery_date',
    'condition',
    'description',
    'bullets',
    'images',
    'category',
    'seller',
)

_AMAZON = {'category': AMAZON_CATEGORY, 'product': AMAZON_PRODUCT}
_EBAY = {'category': EBAY_CATEGORY, 'product': EBAY_PRODUCT}

COLUMNS = {
    'amazon_us': _AMAZON,
    'amazon_au': _AMAZON,
    'ebay_us': _EBAY,
    'ebay_au': _EBAY,
}

# Category output is meant to feed the product-details step.
_AMAZON_CATEGORY_DEFAULT = ('asin', 'url', 'title', 'price', 'rating', 'review_count', 'category')
_AMAZON_PRODUCT_DEFAULT = (
    'asin', 'url', 'title', 'brand', 'price', 'rating', 'review_count', 'category', 'availability', 'inventory', 'delivery_date', 'ships_from', 'sold_by', 'description',
)
_EBAY_CATEGORY_DEFAULT = ('item_id', 'url', 'title', 'price', 'rating', 'review_count', 'category')
_EBAY_PRODUCT_DEFAULT = (
    'item_id', 'url', 'title', 'brand', 'price', 'rating', 'review_count', 'category', 'availability', 'inventory', 'delivery_date',
)

DEFAULTS = {
    'amazon_us': {'category': _AMAZON_CATEGORY_DEFAULT, 'product': _AMAZON_PRODUCT_DEFAULT},
    'amazon_au': {'category': _AMAZON_CATEGORY_DEFAULT, 'product': _AMAZON_PRODUCT_DEFAULT},
    'ebay_us': {'category': _EBAY_CATEGORY_DEFAULT, 'product': _EBAY_PRODUCT_DEFAULT},
    'ebay_au': {'category': _EBAY_CATEGORY_DEFAULT, 'product': _EBAY_PRODUCT_DEFAULT},
}

ID_FIELD = {
    'amazon_us': 'asin',
    'amazon_au': 'asin',
    'ebay_us': 'item_id',
    'ebay_au': 'item_id',
}

HOSTS = {
    'amazon_us': 'https://www.amazon.com',
    'amazon_au': 'https://www.amazon.com.au',
    'ebay_us': 'https://www.ebay.com',
    'ebay_au': 'https://www.ebay.com.au',
}

IMAGE_COLUMNS = tuple(f'image-{index:02d}' for index in range(1, 11))


def is_amazon(marketplace: str) -> bool:
    return str(marketplace or '').startswith('amazon_')


def is_ebay(marketplace: str) -> bool:
    return str(marketplace or '').startswith('ebay_')


def region_for(marketplace: str) -> str:
    for code, _label, region in MARKETPLACES:
        if code == marketplace:
            return region
    return 'US'


def allowed_columns(marketplace: str, mode: str) -> tuple[str, ...]:
    return COLUMNS.get(marketplace, {}).get(mode, ())


def default_columns(marketplace: str, mode: str) -> tuple[str, ...]:
    return DEFAULTS.get(marketplace, {}).get(mode, ())


def clean_columns(marketplace: str, mode: str, requested) -> list[str]:
    allowed = list(allowed_columns(marketplace, mode))
    if not requested:
        return list(default_columns(marketplace, mode))
    if isinstance(requested, str):
        requested = [part.strip() for part in requested.split(',') if part.strip()]
    chosen = [col for col in requested if col in allowed]
    return chosen or list(default_columns(marketplace, mode))


def fill_image_columns(row: dict) -> dict:
    """Put up to ten image URLs in image-01 … image-10. Extra images are dropped."""
    urls = []
    seen = set()
    for column in IMAGE_COLUMNS:
        value = str(row.get(column) or '').strip()
        if value and value not in seen:
            seen.add(value)
            urls.append(value)
    if not urls:
        raw = row.get('images') or ''
        parts = raw if isinstance(raw, list) else str(raw).split('|')
        for part in parts:
            value = str(part).strip()
            if value and value not in seen:
                seen.add(value)
                urls.append(value)
    urls = urls[:len(IMAGE_COLUMNS)]
    for index, column in enumerate(IMAGE_COLUMNS):
        row[column] = urls[index] if index < len(urls) else ''
    return row


def output_columns(marketplace: str, selected: list[str]) -> list[str]:
    """Always keep the product id and URL so the file can feed the next step.

    Selecting images writes image-01 through image-10 instead of one cell.
    """
    required = [ID_FIELD[marketplace], 'url']
    ordered = []
    for column in required + list(selected):
        if column == 'images':
            for slot in IMAGE_COLUMNS:
                if slot not in ordered:
                    ordered.append(slot)
            continue
        if column not in ordered:
            ordered.append(column)
    return ordered


def options_payload() -> dict:
    columns = {}
    defaults = {}
    for code, _label, _region in MARKETPLACES:
        columns[code] = {
            'category': list(COLUMNS[code]['category']),
            'product': list(COLUMNS[code]['product']),
        }
        defaults[code] = {
            'category': list(DEFAULTS[code]['category']),
            'product': list(DEFAULTS[code]['product']),
        }
    return {
        'marketplaces': [
            {'value': code, 'label': label, 'region': region}
            for code, label, region in MARKETPLACES
        ],
        'modes': [{'value': code, 'label': label} for code, label in MODES],
        'columns': columns,
        'defaults': defaults,
    }
