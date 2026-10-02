"""Fixed catalog used when a job runs with local sample pages.

The same products come back for every category URL so a two-row upload shows
duplicate removal. Numeric values are chosen so rating, review, price, and
category rules each drop a different row.
"""

from __future__ import annotations

from .columns import HOSTS, is_amazon

_AMAZON = (
    {
        'asin': 'B0SAMPLE01',
        'title': 'Steel Cookware Set',
        'brand': 'Northline',
        'price': 49.99,
        'rating': 4.6,
        'review_count': 320,
        'availability': 'In Stock',
        'inventory': 12,
        'category': 'Kitchen',
        'seller': 'Northline Direct',
        'description': '10-piece stainless cookware set.',
        'bullets': 'Oven safe | Dishwasher safe',
        'image': 'https://example.com/cookware.jpg',
        'images': 'https://example.com/cookware.jpg | https://example.com/cookware-2.jpg',
    },
    {
        'asin': 'B0SAMPLE02',
        'title': 'Basic USB Cable',
        'brand': 'CableCo',
        'price': 8.50,
        'rating': 3.1,
        'review_count': 40,
        'availability': 'In Stock',
        'inventory': 12,
        'category': 'Electronics',
        'seller': 'CableCo',
        'description': 'Short charging cable.',
        'bullets': '1 metre',
        'image': 'https://example.com/cable.jpg',
        'images': 'https://example.com/cable.jpg',
    },
    {
        'asin': 'B0SAMPLE03',
        'title': 'New Desk Lamp',
        'brand': 'BrightCo',
        'price': 25.00,
        'rating': 4.8,
        'review_count': 6,
        'availability': 'In Stock',
        'inventory': 12,
        'category': 'Home',
        'seller': 'BrightCo',
        'description': 'LED desk lamp with few reviews.',
        'bullets': 'Dimmable',
        'image': 'https://example.com/lamp-small.jpg',
        'images': 'https://example.com/lamp-small.jpg',
    },
    {
        'asin': 'B0SAMPLE04',
        'title': 'Luxury Floor Lamp',
        'brand': 'BrightCo',
        'price': 189.00,
        'rating': 4.4,
        'review_count': 90,
        'availability': 'In Stock',
        'inventory': 12,
        'category': 'Home',
        'seller': 'BrightCo',
        'description': 'Tall floor lamp.',
        'bullets': 'Warm light',
        'image': 'https://example.com/lamp.jpg',
        'images': 'https://example.com/lamp.jpg',
    },
    {
        'asin': 'B0SAMPLE05',
        'title': 'Paperback Novel',
        'brand': 'River Press',
        'price': 14.00,
        'rating': 4.2,
        'review_count': 200,
        'availability': 'In Stock',
        'inventory': 12,
        'category': 'Books',
        'seller': 'River Press',
        'description': 'Fiction paperback.',
        'bullets': 'Paperback',
        'image': 'https://example.com/book.jpg',
        'images': 'https://example.com/book.jpg',
    },
)

_EBAY = (
    {
        'item_id': '100000000001',
        'title': 'Steel Cookware Set',
        'brand': 'Northline',
        'price': 49.99,
        'rating': 4.6,
        'review_count': 320,
        'availability': 'In Stock',
        'inventory': 12,
        'condition': 'New',
        'category': 'Kitchen',
        'seller': 'northline_au',
        'description': '10-piece stainless cookware set.',
        'bullets': 'Oven safe | Dishwasher safe',
        'image': 'https://example.com/cookware.jpg',
        'images': 'https://example.com/cookware.jpg',
    },
    {
        'item_id': '100000000002',
        'title': 'Basic USB Cable',
        'brand': 'CableCo',
        'price': 8.50,
        'rating': 3.1,
        'review_count': 40,
        'availability': 'In Stock',
        'inventory': 12,
        'condition': 'New',
        'category': 'Electronics',
        'seller': 'cableco',
        'description': 'Short charging cable.',
        'bullets': '1 metre',
        'image': 'https://example.com/cable.jpg',
        'images': 'https://example.com/cable.jpg',
    },
    {
        'item_id': '100000000003',
        'title': 'New Desk Lamp',
        'brand': 'BrightCo',
        'price': 25.00,
        'rating': 4.8,
        'review_count': 6,
        'availability': 'In Stock',
        'inventory': 12,
        'condition': 'New',
        'category': 'Home',
        'seller': 'brightco',
        'description': 'LED desk lamp with few reviews.',
        'bullets': 'Dimmable',
        'image': 'https://example.com/lamp-small.jpg',
        'images': 'https://example.com/lamp-small.jpg',
    },
    {
        'item_id': '100000000004',
        'title': 'Luxury Floor Lamp',
        'brand': 'BrightCo',
        'price': 189.00,
        'rating': 4.4,
        'review_count': 90,
        'availability': 'In Stock',
        'inventory': 12,
        'condition': 'New',
        'category': 'Home',
        'seller': 'brightco',
        'description': 'Tall floor lamp.',
        'bullets': 'Warm light',
        'image': 'https://example.com/lamp.jpg',
        'images': 'https://example.com/lamp.jpg',
    },
    {
        'item_id': '100000000005',
        'title': 'Paperback Novel',
        'brand': 'River Press',
        'price': 14.00,
        'rating': 4.2,
        'review_count': 200,
        'availability': 'In Stock',
        'inventory': 12,
        'condition': 'New',
        'category': 'Books',
        'seller': 'riverpress',
        'description': 'Fiction paperback.',
        'bullets': 'Paperback',
        'image': 'https://example.com/book.jpg',
        'images': 'https://example.com/book.jpg',
    },
)


def _with_url(marketplace: str, row: dict) -> dict:
    host = HOSTS[marketplace]
    out = dict(row)
    if is_amazon(marketplace):
        out['url'] = f'{host}/dp/{row["asin"]}'
    else:
        out['url'] = f'{host}/itm/{row["item_id"]}'
    return out


def sample_catalog(marketplace: str) -> list[dict]:
    source = _AMAZON if is_amazon(marketplace) else _EBAY
    rows = [_with_url(marketplace, row) for row in source]
    # Same product listed again, the way overlapping category pages do.
    rows.append(dict(rows[0]))
    return rows


def sample_by_key(marketplace: str) -> dict[str, dict]:
    catalog = sample_catalog(marketplace)
    keyed = {}
    field = 'asin' if is_amazon(marketplace) else 'item_id'
    for row in catalog:
        keyed[str(row[field])] = row
    return keyed


def category_template_urls(marketplace: str) -> list[str]:
    host = HOSTS[marketplace]
    if is_amazon(marketplace):
        return [
            f'{host}/s?k=cookware',
            f'{host}/s?k=cookware&page=2',
        ]
    return [
        f'{host}/sch/i.html?_nkw=cookware',
        f'{host}/sch/i.html?_nkw=cookware&_pgn=2',
    ]


def product_template_rows(marketplace: str) -> list[dict]:
    catalog = sample_catalog(marketplace)
    # Unique products plus one repeated id so the product file shows de-dupe.
    unique = []
    seen = set()
    field = 'asin' if is_amazon(marketplace) else 'item_id'
    for row in catalog:
        if row[field] in seen:
            continue
        seen.add(row[field])
        unique.append(row)
    return unique + [dict(unique[0])]
