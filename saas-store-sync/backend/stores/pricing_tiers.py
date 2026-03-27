"""
Resolve which price-range margin applies to a raw vendor cost.

Tiers are matched on raw vendor price (same as sync.tasks._apply_pricing).

If the cost is above every tier's upper bound (e.g. only 0–20 configured but cost is 39),
we treat it as overflow and apply the last tier — so a single "0–20" row does not leave
higher prices on the global multiplier fallback.

If several tiers match (e.g. a loose row "0 → MAX" plus narrower bands like "16–50"),
we pick the most specific band: **largest `from_value`** among matches. That way a
catch‑all open upper bound does not shadow the tier you actually care about.
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
    Return the StorePriceRangeMargin whose [from, to] contains raw_vendor_cost,
    or the last tier when cost is strictly above every tier's max (overflow).
    """
    tiers = list(
        pricing_settings.range_margins.select_related("price_range").order_by(
            "price_range__from_value"
        )
    )
    if not tiers:
        return None

    matches = []
    for tier in tiers:
        from_v = float(tier.price_range.from_value)
        to_v = (
            float(tier.price_range.to_value)
            if tier.price_range.to_value is not None
            else float("inf")
        )
        if from_v <= raw_vendor_cost <= to_v:
            matches.append(tier)

    if matches:
        return max(matches, key=lambda t: float(t.price_range.from_value))

    sorted_tiers = sorted(
        tiers,
        key=lambda t: float(t.price_range.from_value),
    )
    last = sorted_tiers[-1]
    last_to = (
        float(last.price_range.to_value)
        if last.price_range.to_value is not None
        else float("inf")
    )
    if last_to < float("inf") and raw_vendor_cost > last_to:
        return last

    return None
