"""Shared scrape-failure handling: zero local stock and push 0 to marketplace."""
from __future__ import annotations

import logging
from decimal import Decimal

logger = logging.getLogger(__name__)

FALLBACK_LISTING_PRICE = Decimal('489.99')

# eBay answers with a bot wall (Akamai) when it does not trust a request. Those
# codes mean "the page was not seen", not "the product is gone", so the listing
# keeps its price and stock and stays Pending for the next pass.
VENDOR_BLOCK_CODES = frozenset({'blocked', 'challenge', 'captcha'})
# Blocked tries in a row before the listing fails the usual way.
BLOCK_RETRY_LIMIT = 3


def is_vendor_block(product, result) -> bool:
    """True when an eBay scrape was refused by the bot wall (US or AU)."""
    from catalog.marketplace_catalog import vendor_is_ebay

    if not isinstance(result, dict) or result.get('price') is not None:
        return False
    if (result.get('error_code') or '') not in VENDOR_BLOCK_CODES:
        return False
    return vendor_is_ebay(getattr(product, 'vendor', None))


def leave_pending_after_block(pm, code: str, message: str = '') -> bool:
    """Keep a blocked listing Pending, untouched. False once the retry limit is reached.

    Price, stock and title stay as they were and nothing is pushed. The try
    count rides on ``failed_sync_count``, which a successful scrape and the
    scheduled reset both clear.
    """
    if (message or '').strip() == 'proxy_exhausted':
        # Our own throttle (every proxy resting or in use), not the vendor
        # refusing: it does not count toward the limit.
        pm.scrape_error = 'proxy_exhausted: every proxy was resting or in use, will retry'
        pm.save(update_fields=['scrape_error'])
        return True
    tries = (pm.failed_sync_count or 0) + 1
    if tries >= BLOCK_RETRY_LIMIT:
        return False
    pm.failed_sync_count = tries
    note = f'{code or "blocked"}: vendor page blocked, will retry ({tries}/{BLOCK_RETRY_LIMIT})'
    if message:
        note = f'{note}: {str(message)[:200]}'
    pm.scrape_error = note[:512]
    pm.save(update_fields=['failed_sync_count', 'scrape_error'])
    return True


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


def apply_no_vendor_price_fallback(
    pm,
    code: str = 'no_price',
    message: str = '',
    *,
    store=None,
    scrape_title: str = '',
    now=None,
    price_by_vendor_id=None,
    price_fallback=None,
    push_marketplace: bool = True,
) -> None:
    """When the vendor site returns no price, list at the fixed fallback price with zero stock."""
    from django.utils import timezone

    from stores.models import Store

    if now is None:
        now = timezone.now()

    reason = (code or 'no_price').strip() or 'no_price'
    if message:
        reason = f'{reason}: {str(message)[:200]}'
    reason = f'{reason}; listing_price={FALLBACK_LISTING_PRICE}'

    pm.store_price = FALLBACK_LISTING_PRICE
    pm.store_stock = 0
    pm.failed_sync_count = (pm.failed_sync_count or 0) + 1
    pm.sync_status = 'needs_attention' if pm.failed_sync_count >= 3 else 'failed'
    pm.last_scrape_time = now
    pm.scrape_error = reason[:512]
    save_fields = [
        'store_price',
        'store_stock',
        'sync_status',
        'failed_sync_count',
        'last_scrape_time',
        'scrape_error',
    ]
    if scrape_title:
        pm.title = scrape_title[:500]
        save_fields.append('title')
    pm.save(update_fields=save_fields)

    if not push_marketplace:
        return

    if store is None:
        try:
            store = Store.objects.select_related('marketplace').get(pk=pm.store_id)
        except Store.DoesNotExist:
            return

    _push_fallback_listing(pm, store, price_by_vendor_id, price_fallback)


def _push_fallback_listing(pm, store, price_by_vendor_id=None, price_fallback=None) -> None:
    if store.connection_status != 'connected':
        return

    try:
        from catalog.marketplace_push import (
            apply_post_scrape_marketplace_push,
            push_product_mapping_to_marketplace,
        )
        from catalog.marketplace_catalog import store_is_sears

        # Sears pricing PUTs are rate-limited; per-row pushes during parallel scrape
        # chunks cause cascading 403s. Defer to the end-of-scrape bulk feed instead.
        if store_is_sears(store):
            logger.info(
                'Deferring Sears fallback push to bulk flush pm=%s sku=%s',
                getattr(pm, 'id', None),
                getattr(pm, 'marketplace_child_sku', None),
            )
            return

        if price_by_vendor_id is None or price_fallback is None:
            from sync.tasks import _build_store_vendor_pricing_inventory_caches

            price_by_vendor_id, price_fallback, _, _ = (
                _build_store_vendor_pricing_inventory_caches(store)
            )

        ok, err = push_product_mapping_to_marketplace(
            pm,
            store,
            price_by_vendor_id=price_by_vendor_id,
            price_fallback=price_fallback,
        )
        if not ok and err:
            combined = f'{pm.scrape_error or ""}; fallback_push: {err}'.strip('; ').strip()
            pm.scrape_error = combined[:512]
            pm.save(update_fields=['scrape_error'])

        apply_post_scrape_marketplace_push(
            pm,
            store,
            price_by_vendor_id=price_by_vendor_id,
            price_fallback=price_fallback,
        )
    except Exception as exc:
        logger.warning('Fallback listing push failed pm=%s: %s', pm.id, exc)


def _push_zero_stock_for_failed(pm, store) -> None:
    if store.connection_status != 'connected':
        return
    if pm.store_price is None:
        return

    try:
        from catalog.marketplace_push import push_product_mapping_to_marketplace
        from catalog.marketplace_catalog import store_is_sears
        from sync.tasks import _build_store_vendor_pricing_inventory_caches

        if store_is_sears(store):
            logger.info(
                'Deferring Sears zero-stock push to bulk flush pm=%s sku=%s',
                getattr(pm, 'id', None),
                getattr(pm, 'marketplace_child_sku', None),
            )
            return

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
