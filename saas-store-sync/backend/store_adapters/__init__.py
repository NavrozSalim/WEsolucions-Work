"""
Pluggable store adapters. Each platform (Reverb, Etsy, Walmart, etc.) has an adapter
that implements create_product, update_product, update_inventory, delete_product.
Select adapter by store.platform.
"""
from .base import BaseStoreAdapter
from .reverb_adapter import ReverbAdapter
from .etsy_adapter import EtsyAdapter
from .walmart_adapter import WalmartAdapter

_REGISTRY = {
    'Reverb': ReverbAdapter,
    'Etsy': EtsyAdapter,
    'Walmart': WalmartAdapter,
    'Kogan': ReverbAdapter,   # placeholder until KoganAdapter exists
    'MyDeal': ReverbAdapter,
    'Sears': ReverbAdapter,
}


def get_adapter(store):
    """Return the adapter instance for this store's platform (from marketplace name/code)."""
    platform = None
    if getattr(store, 'marketplace', None):
        platform = store.marketplace.name or store.marketplace.code
    platform = platform or getattr(store, 'platform', None) or 'Reverb'
    adapter_class = _REGISTRY.get(platform)
    if not adapter_class:
        adapter_class = ReverbAdapter  # fallback for unknown marketplaces
    return adapter_class(store)
