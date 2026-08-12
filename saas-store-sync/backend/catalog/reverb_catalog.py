"""Backward-compatible alias for ``catalog.marketplace_catalog``.

Prefer importing from ``catalog.marketplace_catalog`` in new code.
"""
from catalog.marketplace_catalog import *  # noqa: F401,F403
from catalog.marketplace_catalog import (  # noqa: F401
    listing_sku_lookup_order,
    store_is_etsy,
    store_is_kogan,
    store_is_reverb,
    store_is_sears,
    store_is_walmart,
    vendor_is_ebay,
)
