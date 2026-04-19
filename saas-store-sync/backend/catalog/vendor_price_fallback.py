"""Latest-VendorPrice lookup used to promote ingest-only feeds
(HEB desktop runner, Costco AU desktop runner, Vevor AU S3 feed) onto a
``ProductMapping`` through the store's current pricing rules.

This module **must not** be used to hide a failed live scrape: the
catalog/sync tasks have been rewritten to clear ``store_price`` and mark
the mapping ``failed`` when a live scraper returns no data, rather than
silently pushing a stale VendorPrice. See
``catalog.tasks._fail_mapping`` and ``sync.tasks._fail_mapping``.
"""
from __future__ import annotations

import os
from datetime import timedelta
from typing import Optional, Tuple

from django.utils import timezone

from vendor.models import VendorPrice


def get_last_known_vendor_price_stock(product) -> Tuple[Optional[float], int]:
    """Latest ``VendorPrice`` with a non-null price, if within
    ``VENDOR_PRICE_FALLBACK_MAX_AGE_DAYS`` (default 14 days).

    Returns ``(None, 0)`` when no row is found or when the most recent row
    is older than the configured cutoff. A cutoff of ``0`` or a negative
    value disables the age check (useful for Vevor AU whose feed only
    refreshes a few times a day).
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
