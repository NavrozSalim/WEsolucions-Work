"""Helpers to resolve Wallkoala Excel price + stock for a store."""
from __future__ import annotations

import logging

from scrapers.wallkoala_ingest import (
    is_wallkoala_vendor_code,
    load_wallkoala_feed_from_file,
    lookup_wallkoala_entry,
)

logger = logging.getLogger("stores.wallkoala")


def get_wallkoala_inventory_settings(store):
    from stores.models import StoreVendorInventorySettings

    for inv in StoreVendorInventorySettings.objects.filter(store=store).select_related("vendor"):
        if is_wallkoala_vendor_code(getattr(inv.vendor, "code", "")):
            return inv
    return None


def load_store_wallkoala_feed(store) -> dict | None:
    """SKU → {price, inventory} for ``store``, or None if not configured."""
    inv = get_wallkoala_inventory_settings(store)
    if inv is None or not inv.nora_inventory_file:
        return None
    try:
        return load_wallkoala_feed_from_file(inv.nora_inventory_file)
    except Exception:
        logger.exception(
            "Failed to parse Wallkoala inventory file for store %s",
            getattr(store, "id", None),
        )
        raise


def product_uses_wallkoala(product, row=None) -> bool:
    vendor = getattr(product, "vendor", None)
    return is_wallkoala_vendor_code(getattr(vendor, "code", None))


def resolve_wallkoala_sku(product, row=None) -> str:
    if row is not None:
        raw = (getattr(row, "vendor_id_raw", None) or "").strip()
        if raw:
            return raw
    ivid = (getattr(product, "inventory_vendor_id", None) or "").strip()
    if ivid:
        return ivid
    return (getattr(product, "vendor_sku", None) or "").strip()


def lookup_wallkoala_for_product(feed, product, row=None) -> dict | None:
    return lookup_wallkoala_entry(
        feed,
        resolve_wallkoala_sku(product, row),
        getattr(product, "vendor_sku", None) or "",
    )


def wallkoala_price_stock(feed, product, row=None) -> tuple[float | None, int]:
    """Return (vendor_price, inventory) for a catalog product.

    Raises ValueError when the SKU is missing from the Excel or has no Vendor Price.
    """
    sku = resolve_wallkoala_sku(product, row)
    entry = lookup_wallkoala_for_product(feed, product, row)
    if not entry:
        raise ValueError(
            f'SKU "{sku or getattr(product, "vendor_sku", "")}" not in Wallkoala Excel'
        )
    price = entry.get("price")
    if price is None:
        raise ValueError(
            f'No Vendor Price in Wallkoala Excel for SKU "{sku or entry.get("sku") or ""}"'
        )
    try:
        stock = int(entry.get("inventory") or 0)
    except (TypeError, ValueError):
        stock = 0
    return float(price), max(0, stock)
