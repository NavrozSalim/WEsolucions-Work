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


def apply_post_scrape_marketplace_push(
    pm,
    store,
    *,
    price_by_vendor_id=None,
    price_fallback=None,
) -> None:
    """
    After a successful vendor scrape, push to Sears or Walmart immediately so live
    prices/inventory (and Walmart lag time) match catalog store_price / store_stock.
    """
    from django.utils import timezone

    if not store_is_sears(store) and not store_is_walmart(store):
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
