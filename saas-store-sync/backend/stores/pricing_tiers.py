"""
Resolve which price-range margin applies to a raw vendor cost.

Tiers are matched on raw vendor price (same as sync.tasks._apply_pricing).

Interval rule (tiers sorted by ``from_value``):
- **Non-final tiers:** ``from <= cost < to`` (upper bound is **exclusive**), so a
  boundary like 15 belongs only to the tier that **starts** at 15, not the one
  that **ends** at 15.
- **Final tier:** ``from <= cost <= to`` (upper bound **inclusive**), so the top
  of the band and very large costs up to MAX are covered.

If the cost is strictly above the last tier's finite ``to_value``, we still
return the last tier (overflow) so pricing does not fall through to the global
multiplier when bands are configured but too narrow.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from stores.models import StorePriceRangeMargin, StoreVendorPriceSettings


def resolve_margin_tier_for_raw_cost(
    pricing_settings: "StoreVendorPriceSettings",
    raw_vendor_cost: float,
) -> Optional["StorePriceRangeMargin"]:
    """
    Pick the tier for ``raw_vendor_cost`` using half-open intervals on every
    tier except the last (see module docstring).
    """
    tiers = list(
        pricing_settings.range_margins.select_related("price_range").order_by(
            "price_range__from_value"
        )
    )
    if not tiers:
        return None

    cost = float(raw_vendor_cost)
    n = len(tiers)

    for i, tier in enumerate(tiers):
        from_v = float(tier.price_range.from_value)
        to_v = (
            float(tier.price_range.to_value)
            if tier.price_range.to_value is not None
            else float("inf")
        )
        is_last = i == n - 1
        if is_last:
            if from_v <= cost <= to_v:
                return tier
        else:
            if from_v <= cost < to_v:
                return tier

    last = tiers[-1]
    last_to = (
        float(last.price_range.to_value)
        if last.price_range.to_value is not None
        else float("inf")
    )
    if last_to < float("inf") and cost > last_to:
        return last

    return None
