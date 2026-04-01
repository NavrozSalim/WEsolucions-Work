from celery import shared_task
from django.utils import timezone
from decimal import Decimal
import math

from stores.models import Store, StoreVendorPriceSettings, StoreVendorInventorySettings
from stores.pricing_excel import apply_excel_pricing
from stores.pricing_tiers import resolve_margin_tier_for_raw_cost
from catalog.models import ProductMapping
from vendor.models import VendorPrice
from sync.models import StoreSyncRun
from scrapers import get_price_and_stock, close_amazon_session


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


def _apply_pricing(vendor_price, pricing_settings):
    """
    cost_with_tax = vendor_price * (1 + purchase_tax_percentage/100).
    Per tier: margin_type fixed → list = cost_with_tax + margin_value.
    margin_type percentage → Excel: D = cost_with_tax, F = tier(D), G = D*100/(100-F-E), then rounding.
    No tier: cost_with_tax * multiplier + optional_fee, then rounding.
    """
    if vendor_price is None or pricing_settings is None:
        return Decimal(str(vendor_price)) if vendor_price is not None else None

    cost = float(vendor_price)
    tax_pct = float(pricing_settings.purchase_tax_percentage or 0)
    cost_with_tax = cost * (1 + tax_pct / 100)

    price = None
    tier = resolve_margin_tier_for_raw_cost(pricing_settings, cost)
    if tier is not None:
        margin_val = float(tier.margin_percentage or 0)
        m_type = getattr(tier, 'margin_type', 'percentage') or 'percentage'
        if m_type == 'direct':
            price = cost * margin_val
        elif m_type == 'fixed':
            price = cost_with_tax + margin_val
        else:
            return apply_excel_pricing(
                cost,
                tax_pct,
                float(pricing_settings.marketplace_fees_percentage or 0),
                str(pricing_settings.rounding_option or 'none'),
            )

    if price is None:
        price = cost_with_tax * float(pricing_settings.multiplier or 1) + float(pricing_settings.optional_fee or 0)

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
        store = Store.objects.get(id=store_id)
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
            new_price = _apply_pricing(vendor_price, pricing) if vendor_price is not None else None
            new_stock = _apply_inventory(vendor_stock, inventory)

            if vendor_price is not None:
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


@shared_task(bind=True, max_retries=3)
def run_store_update(self, store_id):
    """
    Full scheduled update: scrape vendor prices, apply rules, push to marketplace.
    Called by scheduled jobs and manual "Update now".
    """
    import logging
    logger = logging.getLogger(__name__)

    try:
        store = Store.objects.get(id=store_id)
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
            new_price = _apply_pricing(vendor_price, pricing) if vendor_price is not None else None
            new_stock = _apply_inventory(vendor_stock, inventory)

            raw_changed = prev_vp is None or (
                prev_vp.price != Decimal(str(vendor_price))
                or int(prev_vp.stock or 0) != int(vendor_stock or 0)
            )

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
                    for sku_candidate in filter(None, [
                        pm.marketplace_child_sku,
                        pm.marketplace_parent_sku,
                        pm.product.vendor_sku,
                    ]):
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
