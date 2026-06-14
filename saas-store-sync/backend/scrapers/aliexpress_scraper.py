"""AliExpress price/title lookup via Affiliate API (UK / US / AU markets)."""
from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation

from scrapers.aliexpress_client import AliExpressAPIError, fetch_product_detail, _credentials_configured
from scrapers.aliexpress_markets import get_aliexpress_market, resolve_aliexpress_market

logger = logging.getLogger(__name__)

# Affiliate productdetail rarely exposes warehouse stock; treat priced listings as available.
DEFAULT_IN_STOCK_QTY = 999

ALIEXPRESS_HOST_MARKERS = ('aliexpress.com', 'aliexpress.us', 'aliexpress.co.uk', 'aliexpress.ru')

PRODUCT_ID_FROM_URL_RE = re.compile(
    r'(?:item/|/)(\d{8,20})(?:\.html|[/?#]|$)',
    re.IGNORECASE,
)


def is_aliexpress_vendor_code(vcode: str) -> bool:
    v = (vcode or '').strip().lower()
    return v in ('aliexpress', 'aliexpressuk', 'aliexpress_us', 'aliexpress_au') or v.startswith('aliexpress_')


def is_aliexpress_url(url: str) -> bool:
    lower = (url or '').lower()
    return any(marker in lower for marker in ALIEXPRESS_HOST_MARKERS)


def extract_aliexpress_product_id(url_or_id: str) -> str | None:
    raw = (url_or_id or '').strip()
    if not raw:
        return None
    if raw.isdigit() and 8 <= len(raw) <= 20:
        return raw
    match = PRODUCT_ID_FROM_URL_RE.search(raw)
    if match:
        return match.group(1)
    return None


def build_aliexpress_item_url(product_id: str) -> str:
    pid = str(product_id or '').strip()
    return f'https://www.aliexpress.com/item/{pid}.html'


def _parse_price(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = re.sub(r'[^\d.,]', '', text.replace(',', ''))
    if not text:
        return None
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    if amount <= 0:
        return None
    return float(amount.quantize(Decimal('0.01')))


def _price_from_product_row(row: dict) -> float | None:
    for key in (
        'target_sale_price',
        'targetSalePrice',
        'target_app_sale_price',
        'targetAppSalePrice',
        'sale_price',
        'salePrice',
        'app_sale_price',
        'appSalePrice',
        'target_original_price',
        'targetOriginalPrice',
        'original_price',
        'originalPrice',
    ):
        price = _parse_price(row.get(key))
        if price is not None:
            return price
    return None


def _title_from_product_row(row: dict) -> str | None:
    for key in ('product_title', 'productTitle', 'title'):
        title = (row.get(key) or '').strip()
        if title:
            return title[:500]
    return None


def scrape_aliexpress(vendor_url: str, region: str, session: dict | None = None) -> dict:
    """
    Fetch price/title for an AliExpress listing via the Affiliate API.

    ``region`` maps to market (UK/USA/AU). When unset or unknown, uses
    ``ALIEXPRESS_DEFAULT_MARKET`` (default UK).
    """
    del session  # API-based; no browser session
    product_id = extract_aliexpress_product_id(vendor_url)
    if not product_id:
        return {
            'price': None,
            'stock': None,
            'title': None,
            'error_code': 'aliexpress_invalid_url',
            'error_message': 'Could not parse AliExpress product ID from URL or SKU',
        }
    if not _credentials_configured():
        return {
            'price': None,
            'stock': None,
            'title': None,
            'error_code': 'aliexpress_not_configured',
            'error_message': (
                'AliExpress API not configured — set ALIEXPRESS_APP_KEY and ALIEXPRESS_APP_SECRET '
                'on the worker'
            ),
        }

    market_key = resolve_aliexpress_market(region)
    market = get_aliexpress_market(region)
    try:
        row = fetch_product_detail(product_id, region)
    except AliExpressAPIError as exc:
        logger.warning('AliExpress API failed for %s: %s', product_id, exc)
        return {
            'price': None,
            'stock': None,
            'title': None,
            'error_code': 'aliexpress_api_error',
            'error_message': str(exc)[:500],
        }

    if not row:
        return {
            'price': None,
            'stock': 0,
            'title': None,
            'error_code': 'aliexpress_product_not_found',
            'error_message': (
                f'No AliExpress product detail for ID {product_id} '
                f'(market={market_key}, country={market["country"]}, currency={market["target_currency"]})'
            ),
        }

    price = _price_from_product_row(row)
    title = _title_from_product_row(row)
    if price is None:
        return {
            'price': None,
            'stock': 0,
            'title': title,
            'error_code': 'aliexpress_no_price',
            'error_message': f'AliExpress returned product {product_id} without a usable price',
        }

    return {
        'price': price,
        'stock': DEFAULT_IN_STOCK_QTY,
        'title': title,
    }


def close_aliexpress_session(session: dict | None) -> None:
    """No-op — API client holds no persistent session."""
    del session
