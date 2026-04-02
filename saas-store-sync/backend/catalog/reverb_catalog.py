"""Reverb-specific catalog rules: upload expectations and listing SKU lookup order."""


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
    Reverb catalog template uses Marketplace Parent SKU as the listing SKU; try it first.
    """
    prod_sku = pm.product.vendor_sku if pm.product else None
    if store_is_reverb(store):
        return [x for x in (pm.marketplace_parent_sku, pm.marketplace_child_sku, prod_sku) if x]
    return [x for x in (pm.marketplace_child_sku, pm.marketplace_parent_sku, prod_sku) if x]
