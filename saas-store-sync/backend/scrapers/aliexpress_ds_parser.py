"""Parse aliexpress.ds.product.get responses into scraper fields."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation


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


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


_SKU_NESTED_KEYS = (
    'ae_item_sku_info_dto',
    'ae_item_sku_info_d_t_o',  # simplify=true responses
    'aeItemSkuInfoDto',
    'ae_item_sku_info',
    'aeItemSkuInfo',
)

_SKU_PRICE_KEYS = (
    'offer_sale_price',
    'offerSalePrice',
    'offer_bulk_sale_price',
    'offerBulkSalePrice',
    'sku_price',
    'skuPrice',
    'target_sale_price',
    'targetSalePrice',
    'target_original_price',
    'targetOriginalPrice',
    'sale_price',
    'salePrice',
)

_BASE_PRICE_KEYS = (
    'target_sale_price',
    'targetSalePrice',
    'product_min_price',
    'productMinPrice',
    'product_max_price',
    'productMaxPrice',
)


def _as_list(value) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in _SKU_NESTED_KEYS:
            inner = value.get(key)
            if isinstance(inner, list):
                return inner
            if isinstance(inner, dict):
                return [inner]
    return []


def _sku_rows(result: dict) -> list[dict]:
    skus = result.get('ae_item_sku_info_dtos') or result.get('aeItemSkuInfoDtos')
    if skus is None:
        return []
    return _as_list(skus)


def _base_info(result: dict) -> dict:
    return _as_dict(result.get('ae_item_base_info_dto') or result.get('aeItemBaseInfoDto'))


def title_from_ds_result(result: dict) -> str | None:
    base = _base_info(result)
    title = (base.get('subject') or result.get('subject') or '').strip()
    return title[:500] if title else None


def _first_price(row: dict, keys: tuple[str, ...]) -> float | None:
    for key in keys:
        price = _parse_price(row.get(key))
        if price is not None:
            return price
    return None


def price_from_ds_result(result: dict) -> float | None:
    prices: list[float] = []
    for row in _sku_rows(result):
        price = _first_price(row, _SKU_PRICE_KEYS)
        if price is not None:
            prices.append(price)
    if prices:
        return min(prices)
    base = _base_info(result)
    return _first_price(base, _BASE_PRICE_KEYS)


def stock_from_ds_result(result: dict) -> int | None:
    total = 0
    found = False
    for row in _sku_rows(result):
        for key in (
            'sku_available_stock',
            'skuAvailableStock',
            'ipm_sku_stock',
            'ipmSkuStock',
            'available_stock',
            'availableStock',
        ):
            raw = row.get(key)
            if raw is None or raw == '':
                continue
            try:
                qty = int(float(str(raw).strip()))
            except (TypeError, ValueError):
                continue
            total += max(0, qty)
            found = True
            break
        if not found and row.get('sku_stock') is True:
            total += 1
            found = True
    if found:
        return total
    base = _base_info(result)
    status = (base.get('product_status_type') or base.get('productStatusType') or '').strip().lower()
    if status == 'onselling':
        return None
    if status:
        return 0
    return None
