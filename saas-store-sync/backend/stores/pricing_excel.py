"""
Excel-aligned margin tiers and post price (Vendor + Tax = D).

Margin F from D (same breakpoints as spreadsheet IF ladder).
Post price: G = D * 100 / (100 - F - E)  where E = marketplace fees %.

Boundary behavior: half-open intervals [0,20), [20,50), ... so D=20 maps to 25%
(Excel strict inequalities leave gaps at exact boundaries; we use inclusive lower bound).
"""
from __future__ import annotations

import math
from decimal import Decimal
from typing import Optional


def excel_margin_tier_percent(d: float) -> float:
    """
    Return margin F (percentage points) from Vendor+Tax D, matching the user's Excel IF ladder.

    Ranges (D = cost after purchase tax):
      [0,20) -> 30   | [20,50) -> 25 | [50,100) -> 20 | [100,300) -> 20
      [300,500) -> 15 | [500,800) -> 15 | [800,1000) -> 10 | [1000,1500) -> 10
      [1500,2000) -> 10 | [2000, inf) -> 10
    """
    if d <= 0:
        return 10.0
    if d < 20:
        return 30.0
    if d < 50:
        return 25.0
    if d < 100:
        return 20.0
    if d < 300:
        return 20.0
    if d < 500:
        return 15.0
    if d < 800:
        return 15.0
    if d < 1000:
        return 10.0
    if d < 1500:
        return 10.0
    if d < 2000:
        return 10.0
    return 10.0


def excel_post_price(cost_d: float, margin_f: float, fees_e: float) -> Optional[float]:
    """
    G = D * 100 / (100 - F - E). Returns None if denominator <= 0.
    """
    denom = 100.0 - float(margin_f) - float(fees_e)
    if denom <= 0:
        return None
    return cost_d * 100.0 / denom


def apply_excel_pricing(
    vendor_price: float,
    purchase_tax_pct: float,
    marketplace_fees_pct: float,
    rounding_option: str = "none",
) -> Optional[Decimal]:
    """
    D = vendor * (1 + tax/100); F = tier(D); price = D*100/(100-F-E); then rounding.
    """
    cost_d = float(vendor_price) * (1.0 + float(purchase_tax_pct or 0) / 100.0)
    f = excel_margin_tier_percent(cost_d)
    e = float(marketplace_fees_pct or 0)
    price = excel_post_price(cost_d, f, e)
    if price is None:
        return None
    price = round(price, 6)
    opt = (rounding_option or "none").lower()
    if opt == "nearest_99":
        price = math.floor(price) + 0.99
    elif opt == "nearest_int":
        price = round(price)
    elif opt == "ceil":
        price = math.ceil(price)
    elif opt == "floor":
        price = math.floor(price)
    return Decimal(str(round(price, 2)))
