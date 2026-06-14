"""Push scraped store_price / store_stock (+ Sears RRP) to the marketplace listing."""
from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_UP

from catalog.marketplace_rrp import adapter_push_kwargs
from catalog.reverb_catalog import store_is_sears, store_is_walmart

logger = logging.getLogger(__name__)

_TWOPL = Decimal('0.01')


def quantize_posted_price(price) -> Decimal | None:
    if price is None:
        return None
    try:
        return Decimal(str(price)).quantize(_TWOPL, rounding=ROUND_HALF_UP)
    except Exception:
        return None


def ensure_sears_rrp_configured(store, kwargs: dict) -> None:
    """Sears requires RRP discount % so Standard (RRP) and Sale (posted) can both be sent."""
    if not store_is_sears(store):
        return
    if kwargs.get('rrp') is not None:
        return
    posted = kwargs.get('price')
    raise ValueError(
        'RRP discount (%) is not set for this vendor — configure it in store settings '
        f'so Sears Standard price (RRP) and Sale price (posted {posted}) can be pushed together.'
    )


def push_product_mapping_to_marketplace(
    pm,
    store,
    *,
    price_by_vendor_id=None,
    price_fallback=None,
) -> tuple[bool, str | None]:
    """
    Push ``pm.store_price`` / ``pm.store_stock`` to the marketplace adapter.

    Sears: sale price = posted, standard price = computed RRP (when RRP discount % set).
    Returns ``(success, error_or_warning)``.

    On partial Sears success (price OK, inventory blocked), returns
    ``(True, warning_message)``.
    """
    from store_adapters import get_adapter
    from sync.tasks import _resolve_listing_id_for_pm

    posted = quantize_posted_price(pm.store_price)
    if posted is None:
        return False, 'no_store_price'

    adapter = get_adapter(store)
    listing_id = _resolve_listing_id_for_pm(adapter, pm, store)
    if not listing_id:
        return False, 'no_marketplace_child_sku'

    kwargs = adapter_push_kwargs(
        store,
        pm,
        posted,
        int(pm.store_stock or 0),
        price_by_vendor_id=price_by_vendor_id,
        price_fallback=price_fallback,
    )

    if store_is_sears(store) and kwargs.get('rrp') is None:
        pct_source = 'RRP discount (%) is not set for this vendor'
        return False, (
            f'{pct_source} — configure it in store settings so Sears Standard price '
            f'(RRP) and Sale price (posted ${posted}) can be pushed together.'
        )

    try:
        adapter.update_product(listing_id, **kwargs)
        warning = getattr(adapter, 'last_inventory_warning', None)
        if warning:
            logger.warning(
                'Marketplace push partial success pm=%s sku=%s: %s',
                pm.id,
                getattr(pm, 'marketplace_child_sku', None),
                warning,
            )
        return True, warning
    except Exception as exc:
        logger.warning(
            'Marketplace push failed pm=%s sku=%s: %s',
            pm.id,
            getattr(pm, 'marketplace_child_sku', None),
            exc,
        )
        return False, str(exc)[:500]


def _sears_bulk_items_from_queue(
    store,
    bulk_queue: list[tuple],
    *,
    price_by_vendor_id=None,
    price_fallback=None,
) -> tuple[list[dict], list[dict], dict]:
    """
    Build Sears bulk API items from ``(pm, listing_id, price, stock)`` rows.

    Returns ``(items, pre_failed, pm_by_sku)``.
    """
    from sync.tasks import _adapter_push_kwargs

    items: list[dict] = []
    pre_failed: list[dict] = []
    pm_by_sku: dict[str, object] = {}

    for pm, listing_id, price, stock in bulk_queue:
        sku = str(listing_id or '').strip()
        if not sku:
            pre_failed.append({'sku': '', 'error': 'no_marketplace_child_sku', 'pm': pm})
            continue
        try:
            kwargs = _adapter_push_kwargs(
                store,
                pm,
                price,
                int(stock or 0),
                price_by_vendor_id,
                price_fallback,
            )
        except ValueError as exc:
            pre_failed.append({'sku': sku, 'error': str(exc)[:500], 'pm': pm})
            continue
        items.append({
            'sku': sku,
            'price': kwargs.get('price'),
            'rrp': kwargs.get('rrp'),
            'stock': kwargs.get('stock'),
        })
        pm_by_sku[sku] = pm

    return items, pre_failed, pm_by_sku


def _apply_sears_bulk_push_results(
    pm_by_sku: dict[str, object],
    result: dict,
    *,
    pre_failed: list[dict] | None = None,
) -> dict:
    """Mark product mappings synced / failed from Sears bulk push results."""
    from django.utils import timezone

    now_ok = timezone.now()
    push_ok = 0
    push_fail = 0
    errors: list[dict] = []
    warnings_by_sku = result.get('warnings') or {}

    for row in pre_failed or []:
        push_fail += 1
        pm = row.get('pm')
        err = row.get('error') or 'marketplace_push_failed'
        errors.append({'sku': row.get('sku') or '', 'error': err})
        if pm is not None:
            pm.scrape_error = str(err)[:500]
            pm.save(update_fields=['scrape_error'])

    ok_set = set(result.get('ok') or [])
    failed_list = result.get('failed') or []

    for sku, pm in pm_by_sku.items():
        if sku in ok_set:
            push_ok += 1
            pm.sync_status = 'synced'
            pm.last_sync_time = now_ok
            warn = warnings_by_sku.get(sku)
            pm.scrape_error = (warn or '')[:500] if warn else None
            pm.save(update_fields=['sync_status', 'last_sync_time', 'scrape_error'])
        elif not any(f.get('sku') == sku for f in failed_list):
            continue

    for it in failed_list:
        sku = str(it.get('sku') or '')
        push_fail += 1
        err = it.get('error') or 'Bulk push failed'
        errors.append({'sku': sku, 'error': err})
        pm = pm_by_sku.get(sku)
        if pm is not None:
            pm.scrape_error = str(err)[:500]
            pm.save(update_fields=['scrape_error'])

    return {
        'push_ok': push_ok,
        'push_fail': push_fail,
        'errors': errors,
        'warnings': warnings_by_sku,
    }


def flush_sears_bulk_marketplace_push(
    store,
    bulk_queue: list[tuple],
    *,
    price_by_vendor_id=None,
    price_fallback=None,
) -> dict:
    """
    Push many Sears listings in batched multi-item XML feeds.

    ``bulk_queue`` entries are ``(pm, listing_id, price, stock)``.
    """
    from store_adapters import get_adapter

    if not store_is_sears(store):
        return {'push_ok': 0, 'push_fail': 0, 'errors': [], 'skipped': True}
    if not bulk_queue:
        return {'push_ok': 0, 'push_fail': 0, 'errors': [], 'skipped': True}

    items, pre_failed, pm_by_sku = _sears_bulk_items_from_queue(
        store,
        bulk_queue,
        price_by_vendor_id=price_by_vendor_id,
        price_fallback=price_fallback,
    )

    if not items:
        stats = _apply_sears_bulk_push_results(pm_by_sku, {'ok': set(), 'failed': []}, pre_failed=pre_failed)
        stats['skipped'] = False
        return stats

    adapter = get_adapter(store)
    bulk_fn = getattr(adapter, 'update_products_bulk', None)
    if not callable(bulk_fn):
        stats = {'push_ok': 0, 'push_fail': len(bulk_queue), 'errors': [], 'skipped': True}
        return stats

    try:
        result = bulk_fn(items) or {}
    except Exception as exc:
        logger.warning('Sears bulk marketplace push failed: %s', exc)
        failed = [{'sku': it['sku'], 'error': str(exc)[:500]} for it in items]
        stats = _apply_sears_bulk_push_results(
            pm_by_sku,
            {'ok': set(), 'failed': failed, 'warnings': {}},
            pre_failed=pre_failed,
        )
        stats['skipped'] = False
        return stats

    stats = _apply_sears_bulk_push_results(pm_by_sku, result, pre_failed=pre_failed)
    stats['skipped'] = False
    return stats


def bulk_push_sears_scraped_listings(
    store,
    *,
    mappings=None,
    sync_statuses=('scraped',),
    price_by_vendor_id=None,
    price_fallback=None,
    require_connected: bool = True,
) -> dict:
    """
    After a scrape batch, push all scraped Sears rows to the marketplace in bulk.

    Skips when the store is not Sears, not connected, or has no scraped rows.
    """
    from store_adapters import get_adapter
    from sync.tasks import _build_store_vendor_pricing_inventory_caches, _resolve_listing_id_for_pm

    if not store_is_sears(store):
        return {'push_ok': 0, 'push_fail': 0, 'skipped': True, 'reason': 'not_sears'}

    if require_connected and getattr(store, 'connection_status', None) != 'connected':
        return {'push_ok': 0, 'push_fail': 0, 'skipped': True, 'reason': 'not_connected'}

    if price_by_vendor_id is None and price_fallback is None:
        price_by_vendor_id, price_fallback, _, _ = _build_store_vendor_pricing_inventory_caches(store)

    if mappings is None:
        from catalog.models import ProductMapping

        mappings = ProductMapping.objects.filter(
            store=store,
            is_active=True,
            sync_status__in=list(sync_statuses),
            store_price__isnull=False,
        ).select_related('product', 'product__vendor')

    adapter = get_adapter(store)
    bulk_queue: list[tuple] = []
    push_skipped = 0

    for pm in mappings:
        if pm.store_price is None or not pm.product:
            continue
        listing_id = _resolve_listing_id_for_pm(adapter, pm, store)
        if not listing_id:
            push_skipped += 1
            continue
        bulk_queue.append((pm, listing_id, pm.store_price, int(pm.store_stock or 0)))

    if not bulk_queue:
        return {'push_ok': 0, 'push_fail': 0, 'push_skipped': push_skipped, 'skipped': True}

    stats = flush_sears_bulk_marketplace_push(
        store,
        bulk_queue,
        price_by_vendor_id=price_by_vendor_id,
        price_fallback=price_fallback,
    )
    stats['push_skipped'] = push_skipped
    if stats.get('push_ok'):
        logger.info(
            'Sears bulk marketplace push store=%s ok=%s fail=%s skipped=%s',
            getattr(store, 'id', None),
            stats.get('push_ok'),
            stats.get('push_fail'),
            push_skipped,
        )
    return stats


def apply_post_scrape_marketplace_push(
    pm,
    store,
    *,
    price_by_vendor_id=None,
    price_fallback=None,
) -> None:
    """
    After a successful vendor scrape, push to Walmart immediately.

    Sears pushes are deferred and flushed in bulk when the scrape batch finishes
    (see ``bulk_push_sears_scraped_listings``) or during ``run_store_update``.
    """
    from django.utils import timezone

    if store_is_sears(store):
        return

    if not store_is_walmart(store):
        return

    ok, err = push_product_mapping_to_marketplace(
        pm,
        store,
        price_by_vendor_id=price_by_vendor_id,
        price_fallback=price_fallback,
    )
    if ok:
        pm.sync_status = 'synced'
        pm.last_sync_time = timezone.now()
        pm.scrape_error = (err or '')[:500] if err else None
        pm.save(update_fields=['sync_status', 'last_sync_time', 'scrape_error'])
    else:
        pm.scrape_error = (err or 'marketplace_push_failed')[:500]
        pm.save(update_fields=['scrape_error'])
