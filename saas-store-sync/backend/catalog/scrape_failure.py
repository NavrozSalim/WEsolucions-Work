"""Shared scrape-failure handling: zero local stock and push 0 to marketplace."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def fail_product_mapping(pm, code: str, message: str = '', *, store=None) -> None:
    """Mark a listing failed, set local stock to 0, push zero inventory when possible.

    Keeps ``store_price`` so the marketplace adapter can post stock=0 with the last
    known sale price (all marketplaces: Reverb, Walmart, Sears, eBay, etc.).
    """
    from stores.models import Store

    pm.store_stock = 0
    pm.failed_sync_count = (pm.failed_sync_count or 0) + 1
    pm.sync_status = 'needs_attention' if pm.failed_sync_count >= 3 else 'failed'
    reason = (code or 'scrape_failed').strip() or 'scrape_failed'
    if message:
        reason = f'{reason}: {str(message)[:240]}'
    pm.scrape_error = reason[:512]
    pm.save(update_fields=[
        'store_stock',
        'failed_sync_count',
        'sync_status',
        'scrape_error',
    ])

    if store is None:
        try:
            store = Store.objects.select_related('marketplace').get(pk=pm.store_id)
        except Store.DoesNotExist:
            return

    _push_zero_stock_for_failed(pm, store)


def _push_zero_stock_for_failed(pm, store) -> None:
    if store.connection_status != 'connected':
        return
    if pm.store_price is None:
        return

    try:
        from catalog.marketplace_push import push_product_mapping_to_marketplace
        from sync.tasks import _build_store_vendor_pricing_inventory_caches

        price_by_vid, price_fb, _, _ = _build_store_vendor_pricing_inventory_caches(store)
        ok, err = push_product_mapping_to_marketplace(
            pm,
            store,
            price_by_vendor_id=price_by_vid,
            price_fallback=price_fb,
        )
        if not ok and err:
            combined = f'{pm.scrape_error or ""}; zero_push: {err}'.strip('; ').strip()
            pm.scrape_error = combined[:512]
            pm.save(update_fields=['scrape_error'])
    except Exception as exc:
        logger.warning('Zero-stock push on scrape fail failed pm=%s: %s', pm.id, exc)
