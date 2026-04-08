from celery import shared_task
from django.utils import timezone
from decimal import Decimal
import logging
import math

logger = logging.getLogger(__name__)

from stores.models import Store, StoreVendorPriceSettings, StoreVendorInventorySettings
from stores.pricing_excel import apply_excel_pricing
from stores.pricing_tiers import resolve_margin_tier_for_raw_cost
from catalog.models import ProductMapping
from catalog.reverb_catalog import listing_sku_lookup_order
from vendor.models import VendorPrice
from sync.models import StoreSyncRun
from scrapers import get_price_and_stock, close_amazon_session
from catalog.vendor_price_fallback import get_last_known_vendor_price_stock, resolve_vendor_price_for_listing


def _resolve_vendor_url(product, store):
    """Build a scrapable URL for a product, falling back to SKU-based construction."""
    if product.vendor_url:
        return product.vendor_url
    vcode = (product.vendor.code or '').lower() if product.vendor else ''
    sku = (product.vendor_sku or '').strip()
    if not sku:
        return None
    region = (store.region or 'USA').upper()
    if vcode in ('amazon', 'amazonusa', 'amazonau', 'amazon_us', 'amazon_au'):
        if region == 'AU':
            return f"https://www.amazon.com.au/dp/{sku}"
        return f"https://www.amazon.com/dp/{sku}"
    if vcode in ('ebay', 'ebayau', 'ebay_au', 'ebay_us'):
        if region == 'AU':
            return f"https://www.ebay.com.au/itm/{sku}"
        return f"https://www.ebay.com/itm/{sku}"
    return None


def _get_pricing_for_vendor(store, vendor_id):
    try:
        return StoreVendorPriceSettings.objects.get(store=store, vendor_id=vendor_id)
    except StoreVendorPriceSettings.DoesNotExist:
        return StoreVendorPriceSettings.objects.filter(store=store).first()


def _get_inventory_for_vendor(store, vendor_id):
    try:
        return StoreVendorInventorySettings.objects.get(store=store, vendor_id=vendor_id)
    except StoreVendorInventorySettings.DoesNotExist:
        return StoreVendorInventorySettings.objects.filter(store=store).first()


def _apply_pricing(
    vendor_price,
    pricing_settings,
    *,
    is_walmart=False,
    pack_qty=None,
    prep_fees=None,
    shipping_fees=None,
):
    """
    cost_with_tax = vendor_price * (1 + purchase_tax_percentage/100).
    Per tier: margin_type fixed → list = cost_with_tax + margin_value.
    margin_type percentage → Excel: D = cost_with_tax, F = tier(D), G = D*100/(100-F-E), then rounding.
    Walmart + percentage tier: same revenue math as Walmart fixed, but profit = (cost×pack_qty) after tax × (tier%/100).
    No tier: cost_with_tax * multiplier + optional_fee, then rounding.
    """
    if vendor_price is None or pricing_settings is None:
        return Decimal(str(vendor_price)) if vendor_price is not None else None

    def _safe_float(val, default=0.0):
        try:
            if val is None:
                return float(default)
            if isinstance(val, str):
                v = val.strip()
                if v == '':
                    return float(default)
                return float(v)
            return float(val)
        except Exception:
            return float(default)

    cost = _safe_float(vendor_price, 0.0)
    tax_pct = _safe_float(getattr(pricing_settings, 'purchase_tax_percentage', 0), 0.0)
    cost_with_tax = cost * (1 + tax_pct / 100)

    def _walmart_post_price(profit_dollars: float) -> float:
        """PostPrice = final_selling - shipping, with marketplace fee in denominator."""
        pq = _safe_float(pack_qty, 1.0)
        pf = _safe_float(prep_fees, 0.0)
        sf = _safe_float(shipping_fees, 0.0)
        fee_pct = _safe_float(getattr(pricing_settings, 'marketplace_fees_percentage', 0), 0.0)
        if pq <= 0:
            pq = 1.0
        vendor_total = cost * pq
        vendor_total_with_tax = vendor_total * (1 + tax_pct / 100)
        denom = 1 - (fee_pct / 100)
        if denom <= 0:
            return vendor_total_with_tax + profit_dollars
        final_selling = (vendor_total_with_tax + profit_dollars + pf + sf) / denom
        return final_selling - sf

    price = None
    tier = resolve_margin_tier_for_raw_cost(pricing_settings, cost)
    if tier is not None:
        margin_val = _safe_float(getattr(tier, 'margin_percentage', 0), 0.0)
        m_type = getattr(tier, 'margin_type', 'percentage') or 'percentage'
        if m_type == 'direct':
            price = cost * margin_val
        elif m_type == 'fixed':
            if is_walmart:
                price = _walmart_post_price(margin_val)
            else:
                price = cost_with_tax + margin_val
        elif is_walmart and m_type == 'percentage':
            pq = _safe_float(pack_qty, 1.0)
            if pq <= 0:
                pq = 1.0
            vendor_total = cost * pq
            vendor_total_with_tax = vendor_total * (1 + tax_pct / 100)
            profit_dollars = vendor_total_with_tax * (margin_val / 100)
            price = _walmart_post_price(profit_dollars)
        else:
            excel_dec = apply_excel_pricing(
                cost,
                tax_pct,
                _safe_float(getattr(pricing_settings, 'marketplace_fees_percentage', 0), 0.0),
                str(pricing_settings.rounding_option or 'none'),
            )
            if excel_dec is not None:
                return excel_dec
            # Denominator invalid (F+E>=100): fall through to multiplier like Amazon path

    if price is None:
        price = cost_with_tax * _safe_float(getattr(pricing_settings, 'multiplier', 1), 1.0) + _safe_float(getattr(pricing_settings, 'optional_fee', 0), 0.0)

    opt = pricing_settings.rounding_option
    if opt == 'nearest_99':
        price = math.floor(price) + 0.99
    elif opt == 'nearest_int':
        price = round(price)
    elif opt == 'ceil':
        price = math.ceil(price)
    elif opt == 'floor':
        price = math.floor(price)
    return Decimal(str(round(price, 2)))


def _is_walmart_store(store):
    code = (getattr(store.marketplace, 'code', '') or '').strip().lower()
    name = (getattr(store.marketplace, 'name', '') or '').strip().lower()
    return code == 'walmart' or name == 'walmart'


def _apply_inventory(vendor_stock, inventory_settings):
    if vendor_stock is None or vendor_stock <= 0:
        return 0
    if inventory_settings is None:
        return vendor_stock
    if getattr(inventory_settings, 'zero_if_low', True) and vendor_stock == 1:
        vendor_stock = 0
    stock = float(vendor_stock)

    # Check range rules first (sorted by from_value)
    ranges = list(inventory_settings.range_multipliers.order_by('from_value'))
    for r in ranges:
        from_v = float(r.from_value)
        to_v = float(r.to_value) if r.to_value is not None else float('inf')
        if from_v <= stock <= to_v:
            if getattr(r, 'range_type', 'multiplier') == 'fixed':
                return max(0, int(r.fixed_value or 0))
            return max(0, int(stock * float(r.multiplier or 1)))

    # Fallback to default rule
    rule_type = inventory_settings.rule_type or 'multiplier'
    val = float(inventory_settings.default_multiplier or 1) if rule_type == 'multiplier' else (inventory_settings.default_value or 1)
    if rule_type == 'multiplier':
        return max(0, int(stock * val))
    if rule_type == 'fixed':
        return int(val) if vendor_stock > 0 else 0
    if rule_type == 'cap':
        return min(int(stock), int(val))
    if rule_type == 'floor':
        return max(int(stock), int(val))
    return int(vendor_stock)


@shared_task(bind=True, max_retries=3)
def run_store_sync(self, store_id):
    """Scrape vendor URLs for a store's products, apply rules, update listings, log results."""
    try:
        store = Store.objects.select_related('marketplace').get(id=store_id)
    except Store.DoesNotExist:
        return
    now = timezone.now()
    sync_run = StoreSyncRun.objects.create(store=store, status='running')
    processed, updated = 0, 0

    mappings = ProductMapping.objects.filter(store=store).select_related('product', 'product__vendor')
    session = {}
    error_summary = None
    try:
        for pm in mappings:
            processed += 1
            price_from_fallback = False
            pricing = _get_pricing_for_vendor(store, pm.product.vendor_id)
            inventory = _get_inventory_for_vendor(store, pm.product.vendor_id)
            url = _resolve_vendor_url(pm.product, store)
            try:
                if not url:
                    raise ValueError("Product has no vendor_url or resolvable SKU")
                result = get_price_and_stock(url, store.region, session)
                vendor_price = result.get('price')
                vendor_stock = result.get('stock')
                scrape_title = (result.get('title') or '').strip()[:500]
            except Exception as e:
                p_cached, s_cached = get_last_known_vendor_price_stock(pm.product)
                if p_cached is not None:
                    vendor_price = p_cached
                    vendor_stock = s_cached
                    price_from_fallback = True
                    scrape_title = ''
                    logger.warning(
                        "Store sync scrape error for %s; using last known vendor price %.2f from DB (%s)",
                        pm.product.vendor_sku,
                        p_cached,
                        e,
                    )
                else:
                    pm.store_price = None
                    pm.store_stock = None
                    pm.failed_sync_count = (pm.failed_sync_count or 0) + 1
                    pm.sync_status = 'needs_attention' if pm.failed_sync_count >= 3 else 'failed'
                    pm.save(
                        update_fields=[
                            'store_price',
                            'store_stock',
                            'failed_sync_count',
                            'sync_status',
                        ]
                    )
                    error_summary = str(e) if not error_summary else error_summary
                    continue

            vendor_price, vendor_stock, from_db = resolve_vendor_price_for_listing(
                pm.product, vendor_price, vendor_stock
            )
            price_from_fallback = price_from_fallback or from_db
            if from_db:
                logger.warning(
                    "No live vendor price for %s; using cached price %.2f from DB (store sync)",
                    pm.product.vendor_sku,
                    vendor_price,
                )

            if vendor_price is None:
                pm.store_price = None
                pm.store_stock = None
                pm.failed_sync_count = (pm.failed_sync_count or 0) + 1
                pm.sync_status = 'needs_attention' if pm.failed_sync_count >= 3 else 'failed'
                pm.save(
                    update_fields=[
                        'store_price',
                        'store_stock',
                        'failed_sync_count',
                        'sync_status',
                    ]
                )
                error_summary = 'Scraper returned no price' if not error_summary else error_summary
                continue

            if vendor_stock is None or vendor_stock <= 0:
                vendor_stock = 0
            new_price = (
                _apply_pricing(
                    vendor_price,
                    pricing,
                    is_walmart=_is_walmart_store(store),
                    pack_qty=getattr(pm, 'pack_qty', None),
                    prep_fees=getattr(pm, 'prep_fees', None),
                    shipping_fees=getattr(pm, 'shipping_fees', None),
                )
                if vendor_price is not None else None
            )
            new_stock = _apply_inventory(vendor_stock, inventory)

            if vendor_price is not None and not price_from_fallback:
                VendorPrice.objects.create(
                    product=pm.product,
                    price=Decimal(str(vendor_price)),
                    stock=vendor_stock or 0,
                )
            pm.store_price = new_price
            pm.store_stock = new_stock
            pm.sync_status = 'scraped'
            pm.failed_sync_count = 0
            pm.last_scrape_time = now
            _fields = ['store_price', 'store_stock', 'sync_status', 'failed_sync_count', 'last_scrape_time']
            if scrape_title:
                pm.title = scrape_title
                _fields.append('title')
            pm.save(update_fields=_fields)
            updated += 1
    finally:
        close_amazon_session(session)

    sync_run.finished_at = timezone.now()
    sync_run.status = 'failed' if error_summary and updated == 0 else ('partial' if error_summary else 'success')
    sync_run.listings_processed = processed
    sync_run.listings_updated = updated
    sync_run.error_summary = error_summary
    sync_run.save()

    return {'store_id': str(store_id), 'at': str(now)}


def _set_catalog_sync_reset_timer(store_id):
    """Start 24h window after marketplace push; then listings return to Pending."""
    from datetime import timedelta

    when = timezone.now() + timedelta(days=1)
    Store.objects.filter(id=store_id).update(catalog_pending_reset_at=when)


def _reset_expired_catalog_pending_statuses():
    """Clear pending-reset timer and set only synced active mappings back to pending when due."""
    from catalog.activity_log import append_catalog_log

    now = timezone.now()
    qs = Store.objects.filter(
        catalog_pending_reset_at__isnull=False,
        catalog_pending_reset_at__lte=now,
    )
    for store in qs:
        n = ProductMapping.objects.filter(
            store=store,
            is_active=True,
            sync_status='synced',
        ).update(sync_status='pending')
        Store.objects.filter(id=store.id).update(catalog_pending_reset_at=None)
        append_catalog_log(
            store.id,
            f'The 24-hour window after your last marketplace sync ended. '
            f'{n} active listing(s) were set back to Pending.',
            action_type='catalog_reset',
        )


@shared_task(bind=True, max_retries=3)
def run_store_update(self, store_id):
    """
    Full scheduled update: scrape vendor prices, apply rules, push to marketplace.
    Called by scheduled jobs and manual "Update now".
    """
    import logging
    logger = logging.getLogger(__name__)

    try:
        store = Store.objects.select_related('marketplace').get(id=store_id)
    except Store.DoesNotExist:
        return {
            'store_id': str(store_id),
            'skipped': True,
            'reason': 'store_not_found',
            'hint': 'Store no longer exists.',
        }

    if store.connection_status != 'connected':
        logger.warning("Store %s not connected, skipping update", store.name)
        return {'store_id': str(store_id), 'skipped': True, 'reason': 'not_connected'}

    now = timezone.now()
    sync_run = StoreSyncRun.objects.create(store=store, status='running')
    processed, updated, push_ok, push_fail, push_skipped = 0, 0, 0, 0, 0
    push_errors = []

    from store_adapters import get_adapter
    adapter = get_adapter(store)

    n_active = ProductMapping.objects.filter(store=store, is_active=True).count()
    n_inactive = ProductMapping.objects.filter(store=store, is_active=False).count()
    mappings = ProductMapping.objects.filter(
        store=store, is_active=True,
    ).select_related('product', 'product__vendor')

    hint = None
    if n_active == 0:
        if ProductMapping.objects.filter(store=store).exists():
            hint = (
                f"No active listings ({n_inactive} inactive). Open Catalog → Products, "
                "turn listings on, or re-sync the catalog."
            )
        else:
            hint = (
                "No catalog products for this store. Upload a catalog in Catalog and run Sync first."
            )

    session = {}
    error_summary = None

    def _record_push_error(sku_hint: str, err: Exception):
        if len(push_errors) >= 20:
            return
        push_errors.append({"sku": (sku_hint or "")[:120], "error": str(err)[:500]})

    try:
        for pm in mappings:
            processed += 1
            price_from_fallback = False
            pricing = _get_pricing_for_vendor(store, pm.product.vendor_id)
            inventory = _get_inventory_for_vendor(store, pm.product.vendor_id)

            # --- Scrape ---
            url = _resolve_vendor_url(pm.product, store)
            try:
                if not url:
                    raise ValueError("Product has no vendor_url or resolvable SKU")
                result = get_price_and_stock(url, store.region, session)
                vendor_price = result.get('price')
                vendor_stock = result.get('stock')
                scrape_title = (result.get('title') or '').strip()[:500]
            except Exception as e:
                p_cached, s_cached = get_last_known_vendor_price_stock(pm.product)
                if p_cached is not None:
                    vendor_price = p_cached
                    vendor_stock = s_cached
                    price_from_fallback = True
                    scrape_title = ''
                    logger.warning(
                        "Scheduled update scrape error for %s; using last known vendor price %.2f from DB (%s)",
                        pm.product.vendor_sku,
                        p_cached,
                        e,
                    )
                else:
                    pm.store_price = None
                    pm.store_stock = None
                    pm.failed_sync_count = (pm.failed_sync_count or 0) + 1
                    pm.sync_status = 'needs_attention' if pm.failed_sync_count >= 3 else 'failed'
                    pm.save(
                        update_fields=[
                            'store_price',
                            'store_stock',
                            'failed_sync_count',
                            'sync_status',
                        ]
                    )
                    error_summary = str(e) if not error_summary else error_summary
                    continue

            vendor_price, vendor_stock, from_db = resolve_vendor_price_for_listing(
                pm.product, vendor_price, vendor_stock
            )
            price_from_fallback = price_from_fallback or from_db
            if from_db:
                logger.warning(
                    "No live vendor price for %s; using cached price %.2f from DB (scheduled update)",
                    pm.product.vendor_sku,
                    vendor_price,
                )

            if vendor_price is None:
                pm.store_price = None
                pm.store_stock = None
                pm.failed_sync_count = (pm.failed_sync_count or 0) + 1
                pm.sync_status = 'needs_attention' if pm.failed_sync_count >= 3 else 'failed'
                pm.save(
                    update_fields=[
                        'store_price',
                        'store_stock',
                        'failed_sync_count',
                        'sync_status',
                    ]
                )
                error_summary = 'Scraper returned no price' if not error_summary else error_summary
                continue

            if vendor_stock is None or vendor_stock <= 0:
                vendor_stock = 0

            prev_vp = VendorPrice.objects.filter(product=pm.product).order_by('-scraped_at').first()
            new_price = (
                _apply_pricing(
                    vendor_price,
                    pricing,
                    is_walmart=_is_walmart_store(store),
                    pack_qty=getattr(pm, 'pack_qty', None),
                    prep_fees=getattr(pm, 'prep_fees', None),
                    shipping_fees=getattr(pm, 'shipping_fees', None),
                )
                if vendor_price is not None else None
            )
            if new_price is None and vendor_price is not None:
                new_price = Decimal(str(vendor_price))
            new_stock = _apply_inventory(vendor_stock, inventory)

            raw_changed = prev_vp is None or (
                prev_vp.price != Decimal(str(vendor_price))
                or int(prev_vp.stock or 0) != int(vendor_stock or 0)
            )

            if not price_from_fallback:
                VendorPrice.objects.create(
                    product=pm.product,
                    price=Decimal(str(vendor_price)),
                    stock=vendor_stock or 0,
                )

            pm.store_price = new_price
            pm.store_stock = new_stock
            pm.sync_status = 'scraped'
            pm.failed_sync_count = 0
            pm.last_scrape_time = now
            _uf = ['store_price', 'store_stock', 'sync_status', 'failed_sync_count', 'last_scrape_time']
            if scrape_title:
                pm.title = scrape_title
                _uf.append('title')
            pm.save(update_fields=_uf)
            updated += 1

            # --- Push to marketplace ---
            listing_id = pm.marketplace_id
            if not listing_id:
                lookup = getattr(adapter, 'lookup_listing_by_sku', None)
                if lookup:
                    for sku_candidate in listing_sku_lookup_order(pm, store):
                        listing_id = lookup(sku_candidate)
                        if listing_id:
                            pm.marketplace_id = listing_id
                            if not pm.marketplace_child_sku:
                                pm.marketplace_child_sku = sku_candidate
                                pm.save(update_fields=['marketplace_id', 'marketplace_child_sku'])
                            else:
                                pm.save(update_fields=['marketplace_id'])
                            break

            continuous = bool(pricing and getattr(pricing, 'continuous_update', False))
            should_push = bool(listing_id and new_price is not None)
            if should_push and continuous and not raw_changed:
                should_push = False

            if should_push:
                try:
                    adapter.update_product(listing_id, price=float(new_price), stock=new_stock or 0)
                    push_ok += 1
                    pm.sync_status = 'synced'
                    pm.last_sync_time = timezone.now()
                    pm.save(update_fields=['sync_status', 'last_sync_time'])
                except Exception as push_err:
                    logger.warning("Push failed for %s: %s", pm.marketplace_child_sku, push_err)
                    push_fail += 1
                    _record_push_error(pm.marketplace_child_sku or pm.product.vendor_sku, push_err)
            elif new_price is not None and not listing_id:
                push_skipped += 1
    finally:
        close_amazon_session(session)

    if processed > 0 and updated == 0 and not hint:
        hint = (
            "No listings updated: every row failed to scrape or returned no price "
            "(check vendor URLs and worker logs)."
        )

    sync_run.finished_at = timezone.now()
    sync_run.status = (
        'failed'
        if updated == 0 and error_summary
        else (
            'partial'
            if processed == 0
            or error_summary
            or push_fail
            or push_skipped
            or (processed > 0 and updated == 0)
            else 'success'
        )
    )
    sync_run.listings_processed = processed
    sync_run.listings_updated = updated
    summary_parts = []
    if error_summary:
        summary_parts.append(error_summary)
    if push_fail:
        summary_parts.append(f"{push_fail} marketplace push(es) failed")
    if push_skipped:
        summary_parts.append(
            f"{push_skipped} listing(s) not pushed (set Marketplace ID or Child SKU so Reverb listing can be found)"
        )
    combined = "; ".join(summary_parts) if summary_parts else ""
    if hint and hint not in combined:
        combined = f"{combined}; {hint}" if combined else hint
    sync_run.error_summary = combined or None
    sync_run.save()

    # Update schedule last_run
    from sync.models import SyncSchedule
    try:
        sched = SyncSchedule.objects.get(store=store)
        sched.last_run = timezone.now()
        sched.save(update_fields=['last_run'])
    except SyncSchedule.DoesNotExist:
        pass

    if push_ok > 0:
        _set_catalog_sync_reset_timer(store_id)
        from catalog.activity_log import append_catalog_log

        append_catalog_log(
            store.id,
            f'Scheduled update finished at {timezone.now().strftime("%Y-%m-%d %H:%M:%S %Z")}. '
            f'{updated} listing(s) scraped, {push_ok} pushed to marketplace.',
            action_type='scheduled_sync_end',
            metadata={
                'scraped': updated,
                'pushed': push_ok,
                'push_failed': push_fail,
                'push_skipped': push_skipped,
            },
        )

    return {
        'store_id': str(store_id),
        'at': now.isoformat(),
        'listings_processed': processed,
        'scraped': updated,
        'pushed': push_ok,
        'push_failed': push_fail,
        'push_skipped': push_skipped,
        'error_summary': error_summary,
        'push_errors': push_errors,
        'hint': hint,
        'store_is_active': store.is_active,
        'inactive_mapping_count': n_inactive,
    }


@shared_task
def check_scheduled_updates():
    """
    Celery Beat calls this every minute. Check each active SyncSchedule and
    enqueue run_store_update if the schedule is due.
    """
    import logging
    from datetime import datetime
    from zoneinfo import ZoneInfo

    logger = logging.getLogger(__name__)
    from sync.models import SyncSchedule

    now_utc = timezone.now()

    _reset_expired_catalog_pending_statuses()

    for sched in SyncSchedule.objects.filter(is_active=True).select_related('store'):
        if not sched.store.is_active or sched.store.connection_status != 'connected':
            continue

        try:
            tz_info = ZoneInfo(sched.timezone or 'UTC')
        except Exception:
            tz_info = ZoneInfo('UTC')

        now_local = now_utc.astimezone(tz_info)

        if sched.schedule_type == 'interval':
            if sched.interval_seconds and sched.interval_seconds > 0:
                if sched.last_run is None:
                    is_due = True
                else:
                    elapsed = (now_utc - sched.last_run).total_seconds()
                    is_due = elapsed >= sched.interval_seconds
            else:
                continue
        else:
            is_due = _crontab_matches(sched, now_local)
            if is_due and sched.last_run:
                last_local = sched.last_run.astimezone(tz_info)
                if last_local.date() == now_local.date() and last_local.hour == now_local.hour and last_local.minute == now_local.minute:
                    is_due = False

        if is_due:
            logger.info("Enqueuing scheduled update for store %s", sched.store.name)
            run_store_update.delay(str(sched.store_id))


def _crontab_matches(sched, now_local):
    """Check if the current local time matches the crontab fields."""
    def _field_matches(field_val, current_val):
        if field_val == '*':
            return True
        for part in field_val.split(','):
            part = part.strip()
            if '/' in part:
                base, step = part.split('/', 1)
                step = int(step)
                if base == '*':
                    if current_val % step == 0:
                        return True
                continue
            if '-' in part:
                lo, hi = part.split('-', 1)
                if int(lo) <= current_val <= int(hi):
                    return True
                continue
            if int(part) == current_val:
                return True
        return False

    return (
        _field_matches(sched.crontab_minute, now_local.minute)
        and _field_matches(sched.crontab_hour, now_local.hour)
        and _field_matches(sched.crontab_day_of_week, now_local.weekday())
        and _field_matches(getattr(sched, 'crontab_day_of_month', '*') or '*', now_local.day)
        and _field_matches(getattr(sched, 'crontab_month_of_year', '*') or '*', now_local.month)
    )


def _resolve_listing_id_for_pm(adapter, pm, store):
    """Resolve Reverb listing id; persist marketplace_id when found via SKU lookup."""
    listing_id = pm.marketplace_id
    if listing_id:
        return listing_id
    lookup = getattr(adapter, 'lookup_listing_by_sku', None)
    if not lookup:
        return None
    for sku_candidate in listing_sku_lookup_order(pm, store):
        listing_id = lookup(sku_candidate)
        if listing_id:
            pm.marketplace_id = listing_id
            if not pm.marketplace_child_sku:
                pm.marketplace_child_sku = sku_candidate
                pm.save(update_fields=['marketplace_id', 'marketplace_child_sku'])
            else:
                pm.save(update_fields=['marketplace_id'])
            break
    return listing_id


@shared_task
def run_store_push_listings_only(store_id, disable_schedule=False):
    """
    Push local store_price / store_stock to the marketplace for listings that are
    already scraped or synced — no vendor URL scrape (excludes pending / failed / needs_attention).

    disable_schedule: if True (manual sync from Catalog), turn off SyncSchedule.is_active for this store.
    """
    import logging
    from store_adapters import get_adapter
    from store_adapters.reverb_adapter import ReverbAPIError
    from catalog.models import ReverbUpdateLog
    from catalog.activity_log import append_catalog_log
    from sync.models import SyncSchedule

    logger = logging.getLogger(__name__)
    try:
        store = Store.objects.select_related('marketplace').get(id=store_id)
    except Store.DoesNotExist:
        return {'error': 'store_not_found', 'store_id': str(store_id)}

    if store.connection_status != 'connected':
        return {
            'error': 'not_connected',
            'hint': 'Validate store connection before pushing listings.',
            'store_id': str(store_id),
        }

    append_catalog_log(
        store.id,
        'Marketplace sync started — pushing local prices and stock to your marketplace.',
        action_type='sync_start',
    )
    if disable_schedule:
        SyncSchedule.objects.filter(store=store).update(is_active=False)
        append_catalog_log(
            store.id,
            'Scheduled automatic updates were turned off because you used Manual sync. '
            'You can turn them back on in store settings.',
            action_type='schedule_paused',
        )

    adapter = get_adapter(store)
    qs = ProductMapping.objects.filter(
        store=store,
        is_active=True,
        sync_status__in=['synced', 'scraped'],
        store_price__isnull=False,
    ).select_related('product', 'product__vendor')

    succeeded, failed, skipped = 0, 0, 0
    for pm in qs.iterator(chunk_size=100):
        listing_id = _resolve_listing_id_for_pm(adapter, pm, store)
        if not listing_id:
            skipped += 1
            ReverbUpdateLog.objects.create(
                product_mapping=pm,
                status=ReverbUpdateLog.Status.FAILED,
                error_message='No marketplace listing ID or resolvable SKU for push',
            )
            continue
        try:
            adapter.update_product(
                listing_id,
                price=float(pm.store_price),
                stock=pm.store_stock or 0,
            )
            now_ok = timezone.now()
            pm.sync_status = 'synced'
            pm.last_sync_time = now_ok
            pm.save(update_fields=['sync_status', 'last_sync_time'])
            ReverbUpdateLog.objects.create(
                product_mapping=pm,
                status=ReverbUpdateLog.Status.SUCCESS,
                pushed_price=pm.store_price,
                pushed_stock=pm.store_stock,
            )
            succeeded += 1
        except ReverbAPIError as e:
            failed += 1
            logger.warning("Manual push failed for %s: %s", pm.id, e)
            ReverbUpdateLog.objects.create(
                product_mapping=pm,
                status=ReverbUpdateLog.Status.FAILED,
                http_status=e.status_code,
                error_message=str(e),
            )
        except Exception as e:
            failed += 1
            logger.exception("Manual push error for %s", pm.id)
            ReverbUpdateLog.objects.create(
                product_mapping=pm,
                status=ReverbUpdateLog.Status.FAILED,
                error_message=str(e)[:500],
            )

    if succeeded > 0:
        _set_catalog_sync_reset_timer(store_id)
    append_catalog_log(
        store.id,
        f'Marketplace sync finished at {timezone.now().strftime("%Y-%m-%d %H:%M:%S %Z")}. '
        f'{succeeded} listing(s) updated, {failed} failed, {skipped} skipped (no marketplace listing ID).',
        action_type='sync_end',
        metadata={'pushed': succeeded, 'failed': failed, 'skipped_no_listing': skipped},
    )

    return {
        'store_id': str(store_id),
        'pushed': succeeded,
        'failed': failed,
        'skipped_no_listing': skipped,
    }


@shared_task
def run_store_critical_zero_inventory(store_id):
    """
    Set all active listing stock to 0 locally and on the marketplace, deactivate the store
    and its sync schedule (emergency stop).
    """
    import logging
    from store_adapters import get_adapter
    from store_adapters.reverb_adapter import ReverbAPIError
    from sync.models import SyncSchedule

    logger = logging.getLogger(__name__)
    try:
        store = Store.objects.select_related('marketplace').get(id=store_id)
    except Store.DoesNotExist:
        return {'error': 'store_not_found'}

    adapter = get_adapter(store)
    pushed, push_failed, local_zeroed = 0, 0, 0

    qs = ProductMapping.objects.filter(store=store, is_active=True).select_related('product')
    for pm in qs.iterator(chunk_size=100):
        pm.store_stock = 0
        pm.save(update_fields=['store_stock'])
        local_zeroed += 1
        if store.connection_status != 'connected':
            continue
        listing_id = _resolve_listing_id_for_pm(adapter, pm, store)
        if not listing_id:
            continue
        try:
            if pm.store_price is not None:
                adapter.update_product(listing_id, price=float(pm.store_price), stock=0)
            else:
                adapter.update_product(listing_id, stock=0)
            pushed += 1
        except (ReverbAPIError, Exception) as e:
            push_failed += 1
            logger.warning("Critical zero push failed for listing %s: %s", listing_id, e)

    store.is_active = False
    store.save()
    try:
        sched = SyncSchedule.objects.get(store=store)
        sched.is_active = False
        sched.save(update_fields=['is_active'])
    except SyncSchedule.DoesNotExist:
        pass

    return {
        'store_id': str(store_id),
        'store_deactivated': True,
        'schedule_deactivated': True,
        'listings_zeroed_local': local_zeroed,
        'marketplace_push_ok': pushed,
        'marketplace_push_failed': push_failed,
    }
