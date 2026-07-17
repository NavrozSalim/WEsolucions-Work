"""Helpers to resolve Nora Inventory Excel stock for a store."""
from __future__ import annotations

import logging

from scrapers.nora_au_ingest import is_nora_vendor_code, load_nora_stock_map_from_file

logger = logging.getLogger("stores.nora")


def get_nora_inventory_settings(store):
    """Return StoreVendorInventorySettings for Nora on this store, or None."""
    from stores.models import StoreVendorInventorySettings

    for inv in StoreVendorInventorySettings.objects.filter(store=store).select_related("vendor"):
        if is_nora_vendor_code(getattr(inv.vendor, "code", "")):
            return inv
    return None


def load_store_nora_stock_map(store) -> dict[str, int] | None:
    """Load Nora Vendor ID → stock for ``store``.

    Returns:
      - ``None`` if Nora is not configured or no file uploaded
      - ``dict`` (possibly empty) when a file is present and parsed
    """
    inv = get_nora_inventory_settings(store)
    if inv is None or not inv.nora_inventory_file:
        return None
    try:
        return load_nora_stock_map_from_file(inv.nora_inventory_file)
    except Exception:
        logger.exception(
            "Failed to parse Nora inventory file for store %s",
            getattr(store, "id", None),
        )
        raise


def lookup_nora_stock(nora_map: dict[str, int] | None, vendor_id: str) -> int | None:
    """Return stock for ``vendor_id`` from a Nora map.

    Returns ``None`` when Nora is not in use (``nora_map is None``).
    Returns ``0`` when Nora is in use but the Vendor ID is missing/blank/unmatched.
    """
    if nora_map is None:
        return None
    key = (vendor_id or "").strip()
    if not key:
        return 0
    return int(nora_map.get(key, 0))


def product_uses_nora_inventory(product, row=None) -> bool:
    """True when this product should take stock from the Nora Excel map."""
    ivid = (getattr(product, "inventory_vendor_id", None) or "").strip()
    if ivid:
        return True
    if row is not None and (getattr(row, "vendor_id_raw", None) or "").strip():
        return True
    vendor = getattr(product, "vendor", None)
    return is_nora_vendor_code(getattr(vendor, "code", None))


def resolve_nora_vendor_id(product, row=None) -> str:
    """Best Vendor ID key for Nora lookup on a catalog product."""
    ivid = (getattr(product, "inventory_vendor_id", None) or "").strip()
    if ivid:
        return ivid
    if row is not None:
        raw = (getattr(row, "vendor_id_raw", None) or "").strip()
        if raw:
            return raw
    vendor = getattr(product, "vendor", None)
    if is_nora_vendor_code(getattr(vendor, "code", None)):
        return (getattr(product, "vendor_sku", None) or "").strip()
    return ""


def override_stock_from_nora(nora_map, product, scraped_stock, row=None) -> int:
    """If product uses Nora, return Nora stock (0 when unmatched); else scraped_stock."""
    if nora_map is None or not product_uses_nora_inventory(product, row):
        return scraped_stock if scraped_stock is not None else 0
    return lookup_nora_stock(nora_map, resolve_nora_vendor_id(product, row)) or 0
