"""Shared marketplace RRP (strike-through / standard price) helpers for Mydeal export and Sears API push."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from catalog.reverb_catalog import store_is_sears


def compute_marketplace_rrp(price: Decimal | float | None, margin_pct: Decimal | float | None) -> Decimal | None:
    """
    RRP from posted price and discount % off RRP.

    Same formula as Mydeal ``RRP(IncGST)``::
        RRP = Price / ((100 - discount_pct) / 100)

    Example: price 74, discount 26 → RRP 100 (price is 74% of RRP).
    """
    if price is None or margin_pct is None:
        return None
    try:
        p = Decimal(str(price))
        m = Decimal(str(margin_pct))
    except Exception:
        return None
    if m <= 0 or m >= Decimal('100'):
        return None
    divisor = (Decimal('100') - m) / Decimal('100')
    if divisor <= 0:
        return None
    return (p / divisor).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def rrp_discount_pct_for_pm(
    store,
    pm,
    price_by_vendor_id: dict | None = None,
    price_fallback: Any = None,
) -> Decimal | None:
    """Per-vendor RRP discount % from store pricing settings (Mydeal + Sears)."""
    if not store_is_sears(store):
        return None
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
    m = getattr(ps, 'mydeal_rrp_margin_percentage', None)
    if m is None:
        return None
    try:
        return Decimal(str(m))
    except Exception:
        return None


def adapter_push_kwargs(
    store,
    pm,
    price: float | None,
    stock: int | None,
    *,
    price_by_vendor_id: dict | None = None,
    price_fallback: Any = None,
) -> dict[str, Any]:
    """
    kwargs for ``adapter.update_product`` — adds ``rrp`` for Sears when configured.

    ``price`` = posted/sale price (``store_price``).
    ``stock`` = ``store_stock``.
    """
    kwargs: dict[str, Any] = {}
    if price is not None:
        kwargs['price'] = price
    if stock is not None:
        kwargs['stock'] = stock
    if not store_is_sears(store) or price is None or pm is None:
        return kwargs
    pct = rrp_discount_pct_for_pm(store, pm, price_by_vendor_id, price_fallback)
    rrp = compute_marketplace_rrp(Decimal(str(price)), pct)
    if rrp is not None:
        posted = Decimal(str(price)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        if rrp > posted:
            kwargs['rrp'] = float(rrp)
    return kwargs
