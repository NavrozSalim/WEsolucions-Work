"""
Reuse the latest VendorPrice row when a live scrape returns no price or errors.

Avoids empty posted price (store_price) while the UI still shows vendor_price from history.
"""
from __future__ import annotations

import os
from datetime import timedelta
from typing import Optional, Tuple

from django.utils import timezone

from vendor.models import VendorPrice


def get_last_known_vendor_price_stock(product) -> Tuple[Optional[float], int]:
    """
    Latest VendorPrice with non-null price, if within VENDOR_PRICE_FALLBACK_MAX_AGE_DAYS (default 14).
    Returns (None, 0) if none or too old. max_age 0 or negative disables the age check.
    """
    try:
        max_age_days = int(os.getenv("VENDOR_PRICE_FALLBACK_MAX_AGE_DAYS", "14"))
    except ValueError:
        max_age_days = 14

    vp = (
        VendorPrice.objects.filter(product=product, price__isnull=False)
        .order_by("-scraped_at")
        .first()
    )
    if not vp:
        return None, 0
    if max_age_days > 0:
        if timezone.now() - vp.scraped_at > timedelta(days=max_age_days):
            return None, 0
    return float(vp.price), int(vp.stock or 0)


def resolve_vendor_price_for_listing(
    product,
    scraped_price,
    scraped_stock,
) -> Tuple[Optional[float], int, bool]:
    """
    If scraped_price is set, normalize stock and return (price, stock, used_fallback=False).
    Else try DB history; return (price, stock, used_fallback=True) or (None, 0, False).
    """
    if scraped_price is not None:
        s = 0 if scraped_stock is None else int(scraped_stock)
        if s < 0:
            s = 0
        return float(scraped_price), s, False
    p, s = get_last_known_vendor_price_stock(product)
    if p is not None:
        return p, s, True
    return None, 0, False
