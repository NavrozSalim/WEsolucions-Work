"""Product identity and compulsory de-duplication."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from .columns import is_amazon

_ASIN_RE = re.compile(r'(?:/dp/|/gp/product/|[?&]asin=)([A-Z0-9]{10})\b', re.I)
_ASIN_BARE_RE = re.compile(r'\b(B[A-Z0-9]{9})\b', re.I)
_EBAY_RE = re.compile(r'/itm/(?:[^/]+/)?(\d{9,})')
_EBAY_QUERY_RE = re.compile(r'[?&](?:item|itemId)=(\d{9,})', re.I)


def extract_asin(value: str) -> str:
    text = str(value or '').strip()
    if not text:
        return ''
    match = _ASIN_RE.search(text)
    if match:
        return match.group(1).upper()
    bare = text.strip().upper()
    if re.fullmatch(r'B[A-Z0-9]{9}', bare):
        return bare
    match = _ASIN_BARE_RE.search(text.upper())
    if match and ('amazon.' in text.lower() or '/dp/' in text.lower()):
        return match.group(1).upper()
    return ''


def extract_ebay_item_id(value: str) -> str:
    text = str(value or '').strip()
    if not text:
        return ''
    if re.fullmatch(r'\d{9,}', text):
        return text
    match = _EBAY_RE.search(text) or _EBAY_QUERY_RE.search(text)
    return match.group(1) if match else ''


def normalize_url(url: str) -> str:
    text = str(url or '').strip()
    if not text:
        return ''
    parts = urlsplit(text)
    if not parts.netloc:
        return text.rstrip('/').lower()
    return f'{parts.scheme}://{parts.netloc}{parts.path}'.rstrip('/').lower()


def product_key(marketplace: str, row: dict) -> str:
    """Stable id for one marketplace. Same ASIN or eBay item id is one product."""
    if is_amazon(marketplace):
        asin = extract_asin(row.get('asin') or '') or extract_asin(row.get('url') or '')
        if asin:
            return f'asin:{asin}'
    else:
        item_id = extract_ebay_item_id(row.get('item_id') or '') or extract_ebay_item_id(row.get('url') or '')
        if item_id:
            return f'ebay:{item_id}'
    url = normalize_url(row.get('url') or '')
    return f'url:{url}' if url else ''


def dedupe_rows(marketplace: str, rows: list[dict]) -> tuple[list[dict], int]:
    """Keep the first copy of each product. Returns (unique rows, duplicates removed)."""
    seen = set()
    unique = []
    removed = 0
    for row in rows:
        key = product_key(marketplace, row)
        if key and key in seen:
            removed += 1
            continue
        if key:
            seen.add(key)
        unique.append(row)
    return unique, removed
