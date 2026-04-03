"""Marketplace-specific catalog rules (Reverb, eBay vendor rows) and listing SKU order."""

_EBAY_VENDOR_CODES = frozenset({'ebay', 'ebayau', 'ebay_au', 'ebay_us'})


def vendor_is_ebay(vendor, vendor_name_raw: str = '') -> bool:
    """True when the row's vendor is eBay (DB code or name contains 'ebay')."""
    if vendor is not None and (getattr(vendor, 'code', None) or '').lower() in _EBAY_VENDOR_CODES:
        return True
    vn = (vendor_name_raw or '').strip().lower()
    if not vn or vn == 'n/a':
        return False
    return 'ebay' in vn


def store_is_reverb(store) -> bool:
    """True when the store's marketplace is Reverb (code ``reverb``)."""
    m = getattr(store, 'marketplace', None)
    if m is not None:
        return getattr(m, 'code', None) == 'reverb'
    mk_id = getattr(store, 'marketplace_id', None)
    if not mk_id:
        return False
    from marketplace.models import Marketplace

    code = Marketplace.objects.filter(pk=mk_id).values_list('code', flat=True).first()
    return code == 'reverb'


def listing_sku_lookup_order(pm, store):
    """
    SKUs to try for adapters that resolve listing id by SKU (e.g. Reverb).
    Reverb stores and eBay-sourced products use Marketplace Parent SKU first.
    """
    prod_sku = pm.product.vendor_sku if pm.product else None
    ebay_source = bool(
        pm.product and pm.product.vendor and vendor_is_ebay(pm.product.vendor, '')
    )
    if store_is_reverb(store) or ebay_source:
        return [x for x in (pm.marketplace_parent_sku, pm.marketplace_child_sku, prod_sku) if x]
    return [x for x in (pm.marketplace_child_sku, pm.marketplace_parent_sku, prod_sku) if x]
