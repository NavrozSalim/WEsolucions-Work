"""
Pluggable store adapters. Each platform (Reverb, Etsy, Walmart, etc.) has an adapter
that implements create_product, update_product, update_inventory, delete_product.
Select adapter by store.platform.
"""
from .base import BaseStoreAdapter
from .reverb_adapter import ReverbAdapter
from .etsy_adapter import EtsyAdapter
from .walmart_adapter import WalmartAdapter
from .sears_adapter import SearsAdapter

_REGISTRY = {
    'Reverb': ReverbAdapter,
    'Etsy': EtsyAdapter,
    'Walmart': WalmartAdapter,
    'Kogan': ReverbAdapter,   # placeholder until KoganAdapter exists
    'MyDeal': ReverbAdapter,
    'Sears': SearsAdapter,
}


def _resolve_adapter_class(platform: str):
    """Match registry by exact key, then case-insensitive (DB often stores lowercase codes)."""
    if not platform:
        return None
    key = str(platform).strip()
    if key in _REGISTRY:
        return _REGISTRY[key]
    kl = key.lower()
    for reg_key, cls in _REGISTRY.items():
        if reg_key.lower() == kl:
            return cls
    return None


def get_adapter(store):
    """Return the adapter instance for this store's platform (from marketplace name/code)."""
    platform = None
    if getattr(store, 'marketplace', None):
        platform = store.marketplace.name or store.marketplace.code
    platform = platform or getattr(store, 'platform', None) or 'Reverb'
    adapter_class = _resolve_adapter_class(platform)
    if not adapter_class:
        adapter_class = ReverbAdapter  # fallback for unknown marketplaces
    return adapter_class(store)
