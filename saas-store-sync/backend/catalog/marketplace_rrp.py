"""Shared marketplace RRP (strike-through / standard price) helpers for Mydeal, Sears, and Kogan."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from catalog.marketplace_catalog import store_is_kogan, store_is_sears, store_is_walmart

_TWOPL = Decimal('0.01')


def quantize_money(amount) -> Decimal | None:
    if amount is None:
        return None
    try:
        return Decimal(str(amount)).quantize(_TWOPL, rounding=ROUND_HALF_UP)
    except Exception:
        return None


def compute_marketplace_rrp(price: Decimal | float | None, margin_pct: Decimal | float | None) -> Decimal | None:
    """
    RRP from posted price and discount % off RRP.

    Same formula as Mydeal ``RRP(IncGST)``::
        RRP = Price / ((100 - discount_pct) / 100)

    Example: price 74, discount 26 → RRP 100 (price is 74% of RRP).
    """
    posted = quantize_money(price)
    if posted is None or margin_pct is None:
        return None
    try:
        m = Decimal(str(margin_pct))
    except Exception:
        return None
    if m <= 0 or m >= Decimal('100'):
        return None
    divisor = (Decimal('100') - m) / Decimal('100')
    if divisor <= 0:
        return None
    return (posted / divisor).quantize(_TWOPL, rounding=ROUND_HALF_UP)


def _margin_pct_from_settings(
    pm,
    price_by_vendor_id: dict | None,
    price_fallback: Any,
    field_name: str,
) -> Decimal | None:
    if pm is None or not getattr(pm, 'product_id', None):
        return None
    vendor_id = pm.product.vendor_id
    ps = None
    if price_by_vendor_id is not None:
        ps = price_by_vendor_id.get(vendor_id)
    if ps is None:
        ps = price_fallback
    if ps is None:
        return None
    m = getattr(ps, field_name, None)
    if m is None:
        return None
    try:
        val = Decimal(str(m))
    except Exception:
        return None
    if val <= 0 or val >= Decimal('100'):
        return None
    return val


def rrp_discount_pct_for_pm(
    store,
    pm,
    price_by_vendor_id: dict | None = None,
    price_fallback: Any = None,
) -> Decimal | None:
    """Per-vendor RRP discount % from store pricing settings (Mydeal, Sears, Kogan)."""
    return _margin_pct_from_settings(
        pm,
        price_by_vendor_id,
        price_fallback,
        'mydeal_rrp_margin_percentage',
    )


def kogan_price_margin_pct_for_pm(
    pm,
    price_by_vendor_id: dict | None = None,
    price_fallback: Any = None,
) -> Decimal | None:
    """Per-vendor Kogan PRICE column margin % from store pricing settings."""
    return _margin_pct_from_settings(
        pm,
        price_by_vendor_id,
        price_fallback,
        'kogan_price_margin_percentage',
    )


def adapter_push_kwargs(
    store,
    pm,
    price: Decimal | float | None,
    stock: int | None,
    *,
    price_by_vendor_id: dict | None = None,
    price_fallback: Any = None,
) -> dict[str, Any]:
    """
    kwargs for ``adapter.update_product`` — adds ``rrp`` for Sears/Kogan and
    ``list_price`` (Kogan PRICE column) when configured.

    ``price`` = posted/sale price (``store_price`` / kogan_first_price), quantized to cents.
    ``stock`` = ``store_stock``.
    """
    kwargs: dict[str, Any] = {}
    posted = quantize_money(price)
    if posted is not None:
        kwargs['price'] = posted
    if stock is not None:
        kwargs['stock'] = int(stock)
    if store_is_walmart(store) and pm is not None:
        fc = (getattr(pm, 'fulfillment_center_id', None) or '').strip()
        if fc:
            kwargs['ship_node'] = fc
        lt = getattr(pm, 'fulfillment_lag_time', None)
        if lt is not None:
            kwargs['lag_time'] = int(lt)
    if posted is not None and pm is not None and (store_is_sears(store) or store_is_kogan(store)):
        pct = rrp_discount_pct_for_pm(store, pm, price_by_vendor_id, price_fallback)
        rrp = compute_marketplace_rrp(posted, pct)
        if rrp is not None and rrp > posted:
            kwargs['rrp'] = rrp
    if store_is_kogan(store) and posted is not None and pm is not None:
        price_pct = kogan_price_margin_pct_for_pm(pm, price_by_vendor_id, price_fallback)
        list_price = compute_marketplace_rrp(posted, price_pct)
        if list_price is not None and list_price > posted:
            kwargs['list_price'] = list_price
    return kwargs
