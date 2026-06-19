from datetime import timedelta

from celery import shared_task
from django.utils import timezone
from decimal import Decimal
import logging
import math
import re
import time

logger = logging.getLogger(__name__)

from stores.models import Store, StoreVendorPriceSettings, StoreVendorInventorySettings
from stores.pricing_tiers import resolve_margin_tier_for_raw_cost
from catalog.models import ProductMapping
from catalog.reverb_catalog import listing_sku_lookup_order, store_is_sears, store_is_walmart
from vendor.models import VendorPrice
from sync.models import StoreSyncRun
from scrapers import get_price_and_stock, close_amazon_session
from catalog.vendor_url_resolve import (
    costco_product_id_from_value as _costco_product_id_from_value,
    is_costco_vendor_code,
    resolve_costco_product_url,
)

def _is_heb_product(product) -> bool:
    """Return True when ``product`` belongs to the HEB vendor (always ingest-only)."""
    vendor = getattr(product, 'vendor', None)
    code = (getattr(vendor, 'code', '') or '').lower()
    return code in ('heb', 'hebus') or code.startswith('heb_')


def _is_ingest_only_product(product) -> bool:
    """Vendors whose price/stock comes from a desktop runner or S3 feed.

    HEB is always ingest-only (Windows desktop runner). Vevor AU is always
    ingest-only. Costco AU is ingest-only **only when** the AU worker has no
    residential proxies configured — once ``COSTCO_AU_PROXY_URLS`` is set,
    Costco runs through the live server scraper.
    """
    vendor = getattr(product, 'vendor', None)
    code = (getattr(vendor, 'code', '') or '').lower()
    if code in ('vevor', 'vevorau'):
        return True
    if code.startswith('vevor_'):
        return True
    if code in ('heb', 'hebus') or code.startswith('heb_'):
        return True
    is_costco = (
        code in ('costcoau', 'costco_au', 'costco-au')
        or code.startswith('costco_')
    )
    if is_costco:
        try:
            from scrapers.costco_au_proxies import load_proxy_urls
            return not bool(load_proxy_urls())
        except Exception:
            return True
    return False


def _fail_mapping(pm, code: str, message: str = '', *, store=None) -> None:
    """Scrape failure: zero local stock, keep last price, push 0 to marketplace."""
    from catalog.scrape_failure import fail_product_mapping

    fail_product_mapping(pm, code, message, store=store)


def _apply_no_vendor_price_fallback(
    pm,
    code: str,
    message: str = '',
    *,
    store=None,
    scrape_title: str = '',
    now=None,
) -> None:
    from catalog.scrape_failure import apply_no_vendor_price_fallback

    apply_no_vendor_price_fallback(
        pm,
        code,
        message,
        store=store,
        scrape_title=scrape_title,
        now=now,
        push_marketplace=False,
    )


def _heb_product_id_from_sku(sku: str):
    """
    Pick the HEB PDP numeric id from a composite SKU (e.g. AHJH-150275-0311-PK3).

    Prefer 7-digit ids (common on HEB), then 6 / 8 / 5. When multiple segments qualify,
    prefer the left-most hyphen segment (vendor id slot) over later numeric runs.
    """
    sku = (sku or "").strip().replace("_", "-")
    if not sku:
        return None
    if sku.isdigit():
        ln = len(sku)
        if 5 <= ln <= 12:
            return sku
        return None

    candidates = []
    for idx, part in enumerate(re.split(r"[-/]+", sku)):
        if part.isdigit() and 5 <= len(part) <= 8:
            candidates.append((idx, part))

    if not candidates:
        pos = 0
        for m in re.finditer(r"\d{5,8}", sku):
            candidates.append((1000 + pos, m.group(0)))
            pos += 1

    if not candidates:
        return None

    def tier(length: int) -> int:
        return {7: 4, 6: 3, 8: 2, 5: 1}.get(length, 0)

    candidates.sort(key=lambda it: (-tier(len(it[1])), it[0]))
    return candidates[0][1]


def _resolve_vendor_url(product, store):
    """Build a scrapable URL for a product, falling back to SKU-based construction."""
    if product.vendor_url:
        return product.vendor_url
    vcode = (product.vendor.code or '').lower() if product.vendor else ''
    sku = (product.vendor_sku or '').strip()
    if not sku:
        return None
    region = (store.region or 'USA').upper()
    if vcode in ('amazon', 'amazonus', 'amazonusa', 'amazonau', 'amazon_us', 'amazon_au'):
        if region == 'AU' or vcode.endswith('au'):
            return f"https://www.amazon.com.au/dp/{sku}"
        return f"https://www.amazon.com/dp/{sku}"
    if vcode in ('ebay', 'ebayus', 'ebayau', 'ebay_au', 'ebay_us'):
        if region == 'AU' or vcode.endswith('au'):
            return f"https://www.ebay.com.au/itm/{sku}"
        return f"https://www.ebay.com/itm/{sku}"
    if vcode in ('heb', 'hebus') or vcode.startswith('heb_'):
        pid = _heb_product_id_from_sku(sku)
        if pid:
            return f"https://www.heb.com/product-detail/{pid}"
        return None
    if is_costco_vendor_code(vcode):
        pid = _costco_product_id_from_value(sku)
        if pid:
            return f"https://www.costco.com.au/p/{pid}"
        return None
    if _is_aliexpress_vendor_code(vcode):
        from scrapers.aliexpress_scraper import build_aliexpress_item_url, extract_aliexpress_product_id

        pid = extract_aliexpress_product_id(sku)
        if pid:
            return build_aliexpress_item_url(pid)
        return None
    return None


def _is_aliexpress_vendor_code(vcode: str) -> bool:
    from scrapers.aliexpress_scraper import is_aliexpress_vendor_code

    return is_aliexpress_vendor_code(vcode)


def _vendor_url_from_vendor_id(vendor, vendor_id: str, region: str) -> str | None:
    """Build a product page URL from the catalog Vendor ID (ASIN, eBay item id, HEB PDP id)."""
    vcode = (vendor.code or '').lower() if vendor else ''
    vid = (vendor_id or '').strip()
    if not vid:
        return None
    r = (region or 'USA').upper()
    if vcode in ('amazon', 'amazonus', 'amazonusa', 'amazonau', 'amazon_us', 'amazon_au'):
        if r == 'AU' or vcode.endswith('au'):
            return f'https://www.amazon.com.au/dp/{vid}'
        return f'https://www.amazon.com/dp/{vid}'
    if vcode in ('ebay', 'ebayus', 'ebayau', 'ebay_au', 'ebay_us'):
        if r == 'AU' or vcode.endswith('au'):
            return f'https://www.ebay.com.au/itm/{vid}'
        return f'https://www.ebay.com/itm/{vid}'
    if vcode in ('heb', 'hebus') or vcode.startswith('heb_'):
        if vid.isdigit() and 5 <= len(vid) <= 12:
            return f'https://www.heb.com/product-detail/{vid}'
    if is_costco_vendor_code(vcode):
        pid = _costco_product_id_from_value(vid)
        if pid:
            return f'https://www.costco.com.au/p/{pid}'
        return None
    if _is_aliexpress_vendor_code(vcode):
        from scrapers.aliexpress_scraper import build_aliexpress_item_url, extract_aliexpress_product_id

        pid = extract_aliexpress_product_id(vid)
        if pid:
            return build_aliexpress_item_url(pid)
        return None
    return None


def resolve_vendor_scrape_url(product, store, catalog_row=None):
    """
    URL used for vendor price/stock scraping.

    Always follows the **source vendor** from the catalog / Product (Vendor Name, Vendor URL,
    Vendor ID), not the store's listing marketplace (Reverb, Walmart, Sears). The scraper
    is chosen later from the URL domain (Amazon / eBay / HEB, etc.).
    """
    from catalog.services import _normalize

    vendor = getattr(product, "vendor", None) if product else None
    vcode = (getattr(vendor, "code", "") or "").strip().lower()
    is_costco_au = is_costco_vendor_code(vcode)

    if catalog_row is not None:
        if is_costco_au:
            url = resolve_costco_product_url(
                product,
                vendor_url_raw=_normalize(getattr(catalog_row, 'vendor_url_raw', None)),
                vendor_id_raw=_normalize(getattr(catalog_row, 'vendor_id_raw', None)),
            )
            if url:
                return url
        u = _normalize(getattr(catalog_row, 'vendor_url_raw', None))
        if u:
            return u
        vid = _normalize(getattr(catalog_row, 'vendor_id_raw', None))
        if vid and vendor:
            built = _vendor_url_from_vendor_id(vendor, vid, store.region or 'USA')
            if built:
                return built

    if is_costco_au and product:
        url = resolve_costco_product_url(product)
        if url:
            return url

    if product and product.vendor_url and not is_costco_au:
        u = str(product.vendor_url).strip()
        if u:
            return u

    return _resolve_vendor_url(product, store)


def _inventory_from_scrape_result(result: dict | None) -> int | None:
    """Normalize scraper output: prefer ``inventory`` (canonical), fall back to ``stock``."""
    if not isinstance(result, dict):
        return None
    inv = result.get('inventory')
    if inv is not None:
        try:
            return int(inv)
        except (TypeError, ValueError):
            return None
    st = result.get('stock')
    if st is not None:
        try:
            return int(st)
        except (TypeError, ValueError):
            return None
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


def _build_store_vendor_pricing_inventory_caches(store):
    """Load per-store vendor pricing/inventory settings once per job.

    Calling ``_get_pricing_for_vendor`` / ``_get_inventory_for_vendor`` inside
    tight loops issues two queries per listing; at 100k+ rows/day that dominates
    DB time. Snapshots are consistent for one run (same as repeated get()).
    """
    prices = list(StoreVendorPriceSettings.objects.filter(store=store))
    price_by_vendor_id = {p.vendor_id: p for p in prices}
    price_fallback = prices[0] if prices else None
    invs = list(StoreVendorInventorySettings.objects.filter(store=store))
    inv_by_vendor_id = {i.vendor_id: i for i in invs}
    inv_fallback = invs[0] if invs else None
    return price_by_vendor_id, price_fallback, inv_by_vendor_id, inv_fallback


def _get_pricing_for_vendor_from_cache(vendor_id, price_by_vendor_id, price_fallback):
    if not price_by_vendor_id and price_fallback is None:
        return None
    if vendor_id is None:
        return price_fallback
    return price_by_vendor_id.get(vendor_id) or price_fallback


def _get_inventory_for_vendor_from_cache(vendor_id, inv_by_vendor_id, inv_fallback):
    if not inv_by_vendor_id and inv_fallback is None:
        return None
    if vendor_id is None:
        return inv_fallback
    return inv_by_vendor_id.get(vendor_id) or inv_fallback


def _adapter_push_kwargs(store, pm, price, stock, price_by_vendor_id, price_fallback):
    from catalog.marketplace_push import ensure_sears_rrp_configured
    from catalog.marketplace_rrp import adapter_push_kwargs

    kwargs = adapter_push_kwargs(
        store,
        pm,
        price,
        stock,
        price_by_vendor_id=price_by_vendor_id,
        price_fallback=price_fallback,
    )
    ensure_sears_rrp_configured(store, kwargs)
    return kwargs


def _apply_pricing(
    vendor_price,
    pricing_settings,
    *,
    pack_qty=None,
    prep_fees=None,
    shipping_fees=None,
):
    """
    Marketplace-agnostic pricing engine. Every store / marketplace uses the
    same three methods; which one runs depends purely on the matched tier's
    ``margin_type``.

    Inputs (all resolved from ``StoreVendorPriceSettings`` + the tier matched
    for ``vendor_price``)::

        D = vendor_price * (1 + purchase_tax_percentage/100)   # cost with tax
        F = tier.margin_percentage                             # user-configured
        E = marketplace_fees_percentage                        # from store

    Methods::

        direct      -> price = vendor_price * F
                      (F is used as a raw multiplier, tax ignored)
        fixed       -> _fixed_post_price(cost, tax, fee, profit=F,
                                          pack_qty, prep_fees, shipping_fees)
                      (F is treated as a flat profit in dollars; requires the
                       per-product pack/prep/ship fields)
        percentage  -> price = D * 100 / (100 - F - E)
                      (Excel formula: VendorPrice+Tax divided by the "what's
                       left after margin + marketplace fee")

    If no tier matches, or the percentage denominator is non-positive, we
    fall back to ``cost_with_tax * multiplier + optional_fee``.  Rounding is
    applied last via ``rounding_option``.
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
    fee_pct = _safe_float(getattr(pricing_settings, 'marketplace_fees_percentage', 0), 0.0)
    cost_with_tax = cost * (1 + tax_pct / 100)

    def _fixed_post_price(profit_dollars: float) -> float:
        """PostPrice = final_selling - shipping, with marketplace fee in the
        denominator. Uses per-product pack_qty / prep_fees / shipping_fees."""
        pq = _safe_float(pack_qty, 1.0)
        pf = _safe_float(prep_fees, 0.0)
        sf = _safe_float(shipping_fees, 0.0)
        if pq <= 0:
            pq = 1.0
        vendor_total_with_tax = (cost * pq) * (1 + tax_pct / 100)
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
            price = _fixed_post_price(margin_val)
        elif m_type == 'percentage':
            denom = 100.0 - margin_val - fee_pct
            if denom > 0:
                price = cost_with_tax * 100.0 / denom
            # else: denominator invalid (F+E>=100) → fall through to multiplier

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


def _has_fixed_tier(pricing_settings) -> bool:
    """Does this store have at least one Price Range Margin whose
    ``margin_type`` is ``fixed``? Used by the catalog upload validator and
    the scrape pipeline to decide whether ``pack_qty / prep_fees /
    shipping_fees`` are required on a product row.

    Returns False when ``pricing_settings`` is None or has no tiers
    configured.
    """
    if pricing_settings is None:
        return False
    try:
        return pricing_settings.range_margins.filter(margin_type='fixed').exists()
    except Exception:
        return False


def _missing_fixed_inputs(pm) -> list:
    """Return the names of pack_qty / prep_fees / shipping_fees that are
    missing on ``pm`` — used to short-circuit the scrape when the matched
    tier is ``fixed`` and the product hasn't been filled in yet."""
    missing = []
    for field in ('pack_qty', 'prep_fees', 'shipping_fees'):
        v = getattr(pm, field, None)
        if v is None:
            missing.append(field)
    return missing


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
    if store.user_id:
        session['aliexpress_user_id'] = str(store.user_id)
    error_summary = None
    price_by_vid, price_fb, inv_by_vid, inv_fb = _build_store_vendor_pricing_inventory_caches(store)
    try:
        for pm in mappings.iterator(chunk_size=300):
            processed += 1
            # Ingest-only vendors (HEB, Costco AU, Vevor AU): the desktop
            # runner / S3 feed writes store_price + store_stock directly via
            # the ingest endpoint. Do NOT re-apply older VendorPrice rows
            # here — the mapping already holds whatever the most recent
            # fresh scrape produced. Skip silently and never mark 'failed'.
            if pm.product and _is_ingest_only_product(pm.product):
                logger.info(
                    "Ingest-only row untouched (sku=%s vendor=%s)",
                    getattr(pm.product, 'vendor_sku', '?'),
                    (pm.product.vendor.code if pm.product.vendor else '?'),
                )
                continue
            pricing = _get_pricing_for_vendor_from_cache(pm.product.vendor_id, price_by_vid, price_fb)
            inventory = _get_inventory_for_vendor_from_cache(pm.product.vendor_id, inv_by_vid, inv_fb)
            url = resolve_vendor_scrape_url(pm.product, store, None)
            vendor_price = None
            vendor_stock = 0
            scrape_title = ''
            result = {}
            try:
                if not url:
                    raise ValueError("Product has no vendor_url or resolvable SKU")
                result = get_price_and_stock(
                    url,
                    store.region,
                    session,
                    vendor_code=pm.product.vendor.code if pm.product.vendor else None,
                )
                vendor_price = result.get('price')
                vendor_stock = _inventory_from_scrape_result(result)
                scrape_title = (result.get('title') or '').strip()[:500]
            except Exception as e:
                logger.exception(
                    "Store sync scrape error for %s: %s", pm.product.vendor_sku, e,
                )
                _fail_mapping(pm, 'scrape_exception', str(e), store=store)
                error_summary = str(e) if not error_summary else error_summary
                continue

            if vendor_price is None:
                err_code = (
                    result.get('error_code') if isinstance(result, dict) else None
                ) or 'no_price'
                err_msg = (
                    result.get('error_message') if isinstance(result, dict) else ''
                ) or ''
                _apply_no_vendor_price_fallback(
                    pm,
                    err_code,
                    err_msg,
                    store=store,
                    scrape_title=scrape_title,
                    now=now,
                )
                updated += 1
                continue

            if vendor_stock is None or vendor_stock <= 0:
                vendor_stock = 0

            if _has_fixed_tier(pricing):
                tier_now = resolve_margin_tier_for_raw_cost(pricing, vendor_price)
                if tier_now is not None and getattr(tier_now, 'margin_type', '') == 'fixed':
                    missing = _missing_fixed_inputs(pm)
                    if missing:
                        _fail_mapping(
                            pm,
                            'missing_fixed_inputs',
                            f"Fixed pricing requires {', '.join(missing)} on the catalog row.",
                            store=store,
                        )
                        error_summary = 'missing_fixed_inputs' if not error_summary else error_summary
                        continue

            new_price = (
                _apply_pricing(
                    vendor_price,
                    pricing,
                    pack_qty=getattr(pm, 'pack_qty', None),
                    prep_fees=getattr(pm, 'prep_fees', None),
                    shipping_fees=getattr(pm, 'shipping_fees', None),
                )
                if vendor_price is not None else None
            )
            new_stock = _apply_inventory(vendor_stock, inventory)

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
            pm.scrape_error = None
            _fields = [
                'store_price', 'store_stock', 'sync_status',
                'failed_sync_count', 'last_scrape_time', 'scrape_error',
            ]
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


def _store_can_push_to_marketplace(store_id) -> bool:
    """Fresh read of connection status before marketplace push (may change mid-run)."""
    status = (
        Store.objects.filter(pk=store_id)
        .values_list('connection_status', flat=True)
        .first()
    )
    return status == 'connected'


def _reset_active_listings_pending_for_store_update(store) -> dict:
    """Mark active catalog rows Pending before scheduled/manual scrape (chunked for large stores)."""
    from catalog.tasks import _chunked_reset_store_active_listings_pending_scrape

    stats = _chunked_reset_store_active_listings_pending_scrape(store) or {}
    Store.objects.filter(pk=store.pk).update(
        catalog_pending_reset_at=None,
        catalog_zero_pending_at=None,
    )
    return stats


def _store_has_pending_vevor_listings(store_id) -> bool:
    from vendor.models import Vendor

    vendor_ids = list(
        Vendor.objects.filter(code__iregex=r'^vevor(au|_au|-au)?$').values_list('id', flat=True)
    )
    if not vendor_ids:
        return False
    return ProductMapping.objects.filter(
        store_id=store_id,
        is_active=True,
        sync_status='pending',
        product__vendor_id__in=vendor_ids,
    ).exists()


def _scheduled_ingest_refresh(store) -> dict:
    """
    After pending reset: refresh ingest-only vendors (Vevor XLSX feed inline;
    HEB/Costco desktop jobs queued when applicable).
    """
    from catalog.ingest_views import SUPPORTED_VENDORS
    from catalog.models import HebScrapeJob
    from catalog.tasks import run_vevor_au_ingest
    from catalog.views import (
        CatalogScrapeTriggerView,
        _dispatch_server_vendor_job,
        _store_has_pending_vendor_products,
    )

    result = {'vevor': None, 'desktop_jobs': []}

    if _store_has_pending_vevor_listings(store.id):
        logger.info('Scheduled update: running Vevor AU feed ingest for store %s', store.name)
        result['vevor'] = run_vevor_au_ingest(str(store.id))

    for vendor_code, cfg in SUPPORTED_VENDORS.items():
        if vendor_code == 'vevor':
            continue
        if CatalogScrapeTriggerView._vendor_runs_live(vendor_code, cfg):
            continue
        if not _store_has_pending_vendor_products(store, vendor_code):
            continue
        existing = (
            HebScrapeJob.objects.filter(
                store=store,
                vendor_code=vendor_code,
                status__in=[HebScrapeJob.Status.PENDING, HebScrapeJob.Status.CLAIMED],
            )
            .order_by('-requested_at')
            .first()
        )
        if existing:
            result['desktop_jobs'].append({
                'vendor': vendor_code,
                'job_id': str(existing.id),
                'reused': True,
            })
            continue
        job = HebScrapeJob.objects.create(
            store=store,
            vendor_code=vendor_code,
            requested_by=None,
        )
        if (cfg or {}).get('runner') == 'server':
            _dispatch_server_vendor_job(vendor_code, store, job)
        result['desktop_jobs'].append({
            'vendor': vendor_code,
            'job_id': str(job.id),
            'reused': False,
        })
        logger.info(
            'Scheduled update: queued %s desktop ingest job %s for store %s',
            vendor_code,
            job.id,
            store.name,
        )

    return result


def _run_browser_scrape_for_scheduled_update(store, source: str) -> dict:
    """
    Run Amazon/eBay (live) scrapes the same way as Catalog → Start Scraping:
    ``StoreCatalogCeleryScrapeState`` + ``catalog_scrape_store_task`` on heavy-* workers.

    ``run_store_update`` waits until the scrape finishes or the user clicks Stop
    (``should_abort_celery_scrape`` / cancel API).
    """
    import time
    import uuid

    from django.conf import settings

    from catalog.celery_scrape_state import (
        clear_celery_scrape_state,
        mark_celery_scrape_worker_started,
        set_celery_scrape_state,
        should_abort_celery_scrape,
    )
    from catalog.models import StoreCatalogCeleryScrapeState
    from catalog.scrape_progress import invalidate_scrape_progress_cache
    from catalog.tasks import catalog_scrape_store_task, store_has_scrapeable_pending_mappings

    if not store_has_scrapeable_pending_mappings(store):
        return {'skipped': True, 'reason': 'no_scrapeable_pending'}

    invalidate_scrape_progress_cache(str(store.id))

    task_id = str(uuid.uuid4())
    set_celery_scrape_state(
        store,
        task_id=task_id,
        scope=StoreCatalogCeleryScrapeState.Scope.STORE,
        upload=None,
    )
    mark_celery_scrape_worker_started(str(store.id))

    try:
        from catalog.activity_log import append_catalog_log

        append_catalog_log(
            store.id,
            'Server-side vendor scrape started for scheduled/manual store update '
            '(same path as Start Scraping — you can use Stop scraping).',
            action_type='scrape_start',
            metadata={'scope': 'store', 'scheduled_update': True, 'source': source},
        )
    except Exception:
        logger.exception('append_catalog_log failed for scheduled browser scrape_start')

    try:
        catalog_scrape_store_task.apply_async(args=[str(store.id)], task_id=task_id)
    except Exception as exc:
        logger.exception('Failed to enqueue catalog_scrape_store_task for store %s', store.name)
        clear_celery_scrape_state(str(store.id))
        invalidate_scrape_progress_cache(str(store.id))
        return {'error': str(exc)[:500]}

    poll_interval = max(2, int(getattr(settings, 'SCHEDULED_UPDATE_BROWSER_SCRAPE_POLL_SEC', 5) or 5))
    max_wait = max(60, int(getattr(settings, 'SCHEDULED_UPDATE_BROWSER_SCRAPE_MAX_WAIT_SEC', 7200) or 7200))
    started = time.monotonic()

    while time.monotonic() - started < max_wait:
        if should_abort_celery_scrape(str(store.id)):
            try:
                from core.celery import app

                app.control.revoke(task_id, terminate=True, signal='SIGTERM')
            except Exception:
                logger.warning('Revoke catalog scrape task %s failed', task_id, exc_info=True)
            clear_celery_scrape_state(str(store.id))
            invalidate_scrape_progress_cache(str(store.id))
            return {'user_cancelled': True, 'task_id': task_id}

        if not StoreCatalogCeleryScrapeState.objects.filter(store_id=store.id).exists():
            invalidate_scrape_progress_cache(str(store.id))
            return {'completed': True, 'task_id': task_id}

        time.sleep(poll_interval)

    logger.warning(
        'Timed out waiting for browser scrape on store %s after %ss',
        store.name,
        max_wait,
    )
    try:
        from core.celery import app

        app.control.revoke(task_id, terminate=True, signal='SIGTERM')
    except Exception:
        pass
    clear_celery_scrape_state(str(store.id))
    invalidate_scrape_progress_cache(str(store.id))
    return {'error': 'browser_scrape_timeout', 'task_id': task_id}


@shared_task(bind=True, max_retries=3)
def run_store_update(self, store_id, source='beat'):
    """
    Full scheduled update: reset listings to Pending, scrape vendor prices, push to marketplace.
    Called by scheduled jobs and manual "Update now".

    Flow (beat/manual): reset active listings to Pending, refresh ingest vendors (Vevor AU
    XLSX feed inline; HEB/Costco desktop jobs queued), browser-scrape other pending rows,
    then push ``scraped`` rows when ``connection_status`` is ``connected``.

    Scheduled runs always reset + ingest/scrape even when the store is not connected;
    marketplace sync is skipped until connection is restored.

    ``source`` is ``beat`` when Celery Beat enqueues the job, ``manual`` when the user
    triggers an update from the sync API — used for clearer per-store activity logs.
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

    marketplace_push_enabled = _store_can_push_to_marketplace(store_id)
    if not marketplace_push_enabled:
        logger.info(
            "Store %s not connected: running reset + scrape only (no marketplace push).",
            store.name,
        )

    pending_reset_stats = {}
    if source in ('beat', 'manual'):
        pending_reset_stats = _reset_active_listings_pending_for_store_update(store)

    pending_reset_rows = int(pending_reset_stats.get('rows_updated') or 0)

    push_phrase = (
        'vendor scrape and marketplace push.'
        if marketplace_push_enabled
        else 'vendor scrape only (store not connected — marketplace push skipped).'
    )
    if source == 'beat':
        try:
            from catalog.activity_log import append_catalog_log

            append_catalog_log(
                store.id,
                f'Scheduled automatic update started: {pending_reset_rows} active listing(s) '
                f'set to Pending, then {push_phrase}',
                action_type='scheduled_sync_start',
                metadata={
                    'source': 'beat',
                    'pending_reset_rows': pending_reset_rows,
                    'marketplace_push_enabled': marketplace_push_enabled,
                },
            )
        except Exception:
            logger.exception('append_catalog_log failed for scheduled_sync_start')
    elif source == 'manual':
        try:
            from catalog.activity_log import append_catalog_log

            append_catalog_log(
                store.id,
                f'Manual update started: {pending_reset_rows} active listing(s) '
                f'set to Pending, then {push_phrase}',
                action_type='manual_update_start',
                metadata={
                    'source': 'manual',
                    'pending_reset_rows': pending_reset_rows,
                    'marketplace_push_enabled': marketplace_push_enabled,
                },
            )
        except Exception:
            logger.exception('append_catalog_log failed for manual_update_start')

    ingest_refresh = {}
    if source in ('beat', 'manual'):
        ingest_refresh = _scheduled_ingest_refresh(store)

    now = timezone.now()
    sync_run = StoreSyncRun.objects.create(store=store, status='running')
    processed, updated, push_ok, push_fail, push_skipped = 0, 0, 0, 0, 0
    push_blocked_not_connected = 0
    push_blocked_logged = False
    push_errors = []

    def _log_push_blocked_not_connected():
        nonlocal push_blocked_logged
        if push_blocked_logged:
            return
        push_blocked_logged = True
        logger.warning(
            "Store %s no longer connected; skipping marketplace push (scraped data kept locally).",
            store.name,
        )

    from store_adapters import get_adapter
    adapter = get_adapter(store)

    from catalog.tasks import _ingest_only_vendor_ids

    n_active = ProductMapping.objects.filter(store=store, is_active=True).count()
    n_inactive = ProductMapping.objects.filter(store=store, is_active=False).count()
    ingest_vendor_ids = _ingest_only_vendor_ids()

    vevor_ingest = ingest_refresh.get('vevor') if isinstance(ingest_refresh, dict) else None
    if isinstance(vevor_ingest, dict):
        updated += int(vevor_ingest.get('updated') or 0)
        processed += int(vevor_ingest.get('listing_count') or vevor_ingest.get('matched') or 0)

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

    error_summary = None
    browser_scrape = {}

    def _record_push_error(sku_hint: str, err: Exception):
        if len(push_errors) >= 20:
            return
        push_errors.append({"sku": (sku_hint or "")[:120], "error": str(err)[:500]})

    from catalog.scrape_progress import invalidate_scrape_progress_cache

    invalidate_scrape_progress_cache(str(store.id))

    if source in ('beat', 'manual'):
        browser_scrape = _run_browser_scrape_for_scheduled_update(store, source)
        if browser_scrape.get('user_cancelled'):
            error_summary = 'user_cancelled'
        elif browser_scrape.get('error'):
            error_summary = browser_scrape.get('error')

        browser_scraped_qs = ProductMapping.objects.filter(
            store=store,
            is_active=True,
            sync_status__in=['scraped', 'synced'],
            last_scrape_time__gte=now,
        )
        if ingest_vendor_ids:
            browser_scraped_qs = browser_scraped_qs.exclude(
                product__vendor_id__in=ingest_vendor_ids
            )
        browser_scraped = browser_scraped_qs.count()
        updated += browser_scraped
        processed += browser_scraped

    bulk_supported = callable(getattr(adapter, 'update_products_bulk', None))
    bulk_queue = []  # list of (pm, sku, price, stock)
    price_by_vid, price_fb, inv_by_vid, inv_fb = _build_store_vendor_pricing_inventory_caches(store)

    # Push ingest-fed and browser-scraped rows (same pass as before)
    push_mappings = ProductMapping.objects.filter(
        store=store,
        is_active=True,
        sync_status='scraped',
    ).select_related('product', 'product__vendor')
    for pm in push_mappings.iterator(chunk_size=300):
        if pm.store_price is None or not pm.product:
            continue
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
        if not listing_id:
            push_skipped += 1
            continue
        if not _store_can_push_to_marketplace(store_id):
            push_blocked_not_connected += 1
            _log_push_blocked_not_connected()
            continue
        try:
            if bulk_supported:
                bulk_queue.append(
                    (pm, listing_id, pm.store_price, int(pm.store_stock or 0))
                )
            else:
                adapter.update_product(
                    listing_id,
                    **_adapter_push_kwargs(
                        store,
                        pm,
                        pm.store_price,
                        int(pm.store_stock or 0),
                        price_by_vid,
                        price_fb,
                    ),
                )
                push_ok += 1
                pm.sync_status = 'synced'
                pm.last_sync_time = timezone.now()
                pm.save(update_fields=['sync_status', 'last_sync_time'])
        except Exception as push_err:
            logger.warning(
                "Push failed for scraped listing %s: %s",
                pm.marketplace_child_sku,
                push_err,
            )
            push_fail += 1
            _record_push_error(pm.marketplace_child_sku or pm.product.vendor_sku, push_err)

    if bulk_supported and bulk_queue:
        if not _store_can_push_to_marketplace(store_id):
            push_blocked_not_connected += len(bulk_queue)
            _log_push_blocked_not_connected()
            bulk_queue = []
    if bulk_supported and bulk_queue:
        try:
            if store_is_sears(store):
                from catalog.marketplace_push import flush_sears_bulk_marketplace_push

                stats = flush_sears_bulk_marketplace_push(
                    store,
                    bulk_queue,
                    price_by_vendor_id=price_by_vid,
                    price_fallback=price_fb,
                )
                push_ok += int(stats.get('push_ok') or 0)
                push_fail += int(stats.get('push_fail') or 0)
                for err in (stats.get('errors') or [])[:50]:
                    _record_push_error(err.get('sku') or '', Exception(err.get('error') or 'Bulk push failed'))
            else:
                payload = []
                for pm, sku, price, stock in bulk_queue:
                    kwargs = _adapter_push_kwargs(
                        store,
                        pm,
                        price,
                        int(stock or 0),
                        price_by_vid,
                        price_fb,
                    )
                    payload.append((
                        sku,
                        kwargs.get('price'),
                        kwargs.get('stock', int(stock or 0)),
                        kwargs.get('rrp'),
                    ))
                res = adapter.update_products_bulk(payload) or {}
                ok_set = set(res.get('ok') or [])
                failed_list = res.get('failed') or []
                now_ok = timezone.now()
                for pm, sku, _price, _stock in bulk_queue:
                    if str(sku) in ok_set:
                        push_ok += 1
                        pm.sync_status = 'synced'
                        pm.last_sync_time = now_ok
                        pm.save(update_fields=['sync_status', 'last_sync_time'])
                for it in failed_list[:50]:
                    push_fail += 1
                    _record_push_error(it.get('sku') or '', Exception(it.get('error') or 'Bulk push failed'))
        except Exception as e:
            logger.warning("Bulk push failed (scraped pass): %s", e)
            push_fail += len(bulk_queue)
            _record_push_error('bulk', e)

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
            or push_blocked_not_connected
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
    if push_blocked_not_connected:
        summary_parts.append(
            f"{push_blocked_not_connected} marketplace push(es) skipped (store not connected)"
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

    if updated > 0 and (push_blocked_not_connected or not marketplace_push_enabled):
        from catalog.activity_log import append_catalog_log

        blocked_note = (
            f'{push_blocked_not_connected} listing(s) blocked from push'
            if push_blocked_not_connected
            else 'marketplace push disabled until store is connected'
        )
        append_catalog_log(
            store.id,
            f'Update finished at {timezone.now().strftime("%Y-%m-%d %H:%M:%S %Z")}: '
            f'{updated} listing(s) scraped locally; {blocked_note}.',
            action_type='sync_push_skipped_not_connected',
            metadata={
                'scraped': updated,
                'pushed': push_ok,
                'push_blocked_not_connected': push_blocked_not_connected,
                'marketplace_push_enabled': marketplace_push_enabled,
                'source': source,
            },
        )
    elif push_ok > 0:
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

    invalidate_scrape_progress_cache(str(store.id))

    return {
        'store_id': str(store_id),
        'at': now.isoformat(),
        'pending_reset_rows': pending_reset_rows,
        'ingest_refresh': ingest_refresh,
        'browser_scrape': browser_scrape,
        'marketplace_push_enabled': marketplace_push_enabled,
        'listings_processed': processed,
        'scraped': updated,
        'pushed': push_ok,
        'push_failed': push_fail,
        'push_skipped': push_skipped,
        'push_blocked_not_connected': push_blocked_not_connected,
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
        if not sched.store.is_active:
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
            import hashlib

            h = int(hashlib.md5(str(sched.store_id).encode('utf-8')).hexdigest()[:8], 16)
            countdown = h % 30
            logger.info(
                "Enqueuing scheduled update for store %s (countdown=%ss)",
                sched.store.name,
                countdown,
            )
            run_store_update.apply_async(
                args=[str(sched.store_id), 'beat'],
                countdown=countdown,
            )


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
    """Resolve marketplace listing id; persist marketplace_id when found via SKU lookup."""
    if store_is_sears(store) or store_is_walmart(store):
        child = (pm.marketplace_child_sku or '').strip()
        if child:
            return child
    else:
        listing_id = pm.marketplace_id
        if listing_id:
            return listing_id
    listing_id = None
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


PUSH_LISTINGS_PROGRESS_EVERY = 50
PUSH_LISTINGS_PROGRESS_LOG_SEC = 120


def _raise_if_push_aborted(store_id) -> None:
    from sync.push_listings_cancel import PushListingsCancelled, should_abort_push_listings

    if should_abort_push_listings(str(store_id)):
        raise PushListingsCancelled('Manual sync stopped by user')


def _return_push_listings_cancelled(store, succeeded, failed, skipped, total_to_push):
    from catalog.activity_log import append_catalog_log

    append_catalog_log(
        store.id,
        f'Marketplace sync stopped. {succeeded:,} listing(s) pushed before stop, '
        f'{failed:,} failed, {skipped:,} skipped (no marketplace listing ID).',
        action_type='sync_end',
        metadata={
            'pushed': succeeded,
            'failed': failed,
            'skipped_no_listing': skipped,
            'cancelled': True,
        },
    )
    return {
        'store_id': str(store.id),
        'pushed': succeeded,
        'failed': failed,
        'skipped_no_listing': skipped,
        'total': total_to_push,
        'cancelled': True,
    }


def _execute_store_push_listings_only(store_id, disable_schedule=False):
    """
    Push local store_price / store_stock to the marketplace for listings that are
    already scraped or synced — no vendor URL scrape (excludes pending / failed / needs_attention).

    disable_schedule: if True (manual sync from Catalog), turn off SyncSchedule.is_active for this store.
    """
    import logging
    from sync.push_listings_cancel import PushListingsCancelled, should_abort_push_listings
    from store_adapters import get_adapter
    from store_adapters.reverb_adapter import ReverbAPIError
    from store_adapters.sears_adapter import SearsAPIError
    from store_adapters.walmart_adapter import WalmartAPIError
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
    price_by_vid, price_fb, _, _ = _build_store_vendor_pricing_inventory_caches(store)
    qs = ProductMapping.objects.filter(
        store=store,
        is_active=True,
        sync_status__in=['synced', 'scraped'],
        store_price__isnull=False,
    ).select_related('product', 'product__vendor')
    total_to_push = qs.count()

    succeeded, failed, skipped = 0, 0, 0
    last_progress_log_at = time.monotonic()
    cancelled = False

    try:
        _raise_if_push_aborted(store_id)

        append_catalog_log(
            store.id,
            f'Marketplace sync in progress: preparing bulk push — 0 of {total_to_push:,} queued.',
            action_type='sync_progress',
            metadata={
                'processed': 0,
                'total': total_to_push,
                'pushed': 0,
                'failed': 0,
                'skipped_no_listing': 0,
                'sync_step': 'queue_build',
            },
        )

        if store_is_sears(store):
            bulk_queue = []
            queued_pairs = []
            queued_count = 0
            for pm in qs.iterator(chunk_size=100):
                _raise_if_push_aborted(store_id)
                listing_id = _resolve_listing_id_for_pm(adapter, pm, store)
                if not listing_id:
                    skipped += 1
                    ReverbUpdateLog.objects.create(
                        product_mapping=pm,
                        status=ReverbUpdateLog.Status.FAILED,
                        error_message='No marketplace listing ID or resolvable SKU for push',
                    )
                    continue
                bulk_queue.append((pm, listing_id, pm.store_price, int(pm.store_stock or 0)))
                queued_pairs.append((pm, str(listing_id)))
                queued_count += 1
                now_mono = time.monotonic()
                if (
                    queued_count % PUSH_LISTINGS_PROGRESS_EVERY == 0
                    or now_mono - last_progress_log_at >= PUSH_LISTINGS_PROGRESS_LOG_SEC
                ):
                    last_progress_log_at = now_mono
                    append_catalog_log(
                        store.id,
                        f'Marketplace sync in progress: preparing bulk push — '
                        f'{skipped + queued_count:,} of {total_to_push:,} queued '
                        f'({skipped:,} skipped, no listing ID).',
                        action_type='sync_progress',
                        metadata={
                            'processed': skipped + queued_count,
                            'total': total_to_push,
                            'pushed': 0,
                            'failed': 0,
                            'skipped_no_listing': skipped,
                            'sync_step': 'queue_build',
                        },
                    )

            from catalog.marketplace_push import flush_sears_bulk_marketplace_push
            from django.conf import settings as django_settings

            bulk_succeeded = 0
            bulk_failed = 0
            sears_batch_size = max(
                1,
                int(getattr(django_settings, 'SEARS_BULK_BATCH_SIZE', 100) or 100),
            )
            total_batches_estimate = (
                max(1, (len(bulk_queue) + sears_batch_size - 1) // sears_batch_size)
                if bulk_queue else 0
            )

            def _sears_bulk_batch_progress(batch_num, total_batches, batch_ok, batch_failed, _batch_size):
                nonlocal bulk_succeeded, bulk_failed, last_progress_log_at
                bulk_succeeded += batch_ok
                bulk_failed += batch_failed
                processed = skipped + bulk_succeeded + bulk_failed
                now_mono = time.monotonic()
                if (
                    batch_num == total_batches
                    or batch_num % max(1, total_batches // 20) == 0
                    or now_mono - last_progress_log_at >= PUSH_LISTINGS_PROGRESS_LOG_SEC
                ):
                    last_progress_log_at = now_mono
                    append_catalog_log(
                        store.id,
                        f'Marketplace sync in progress: batch {batch_num:,} of {total_batches:,} '
                        f'({processed:,} of {total_to_push:,} listings; '
                        f'{bulk_succeeded:,} pushed, {bulk_failed:,} failed, {skipped:,} skipped).',
                        action_type='sync_progress',
                        metadata={
                            'processed': processed,
                            'total': total_to_push,
                            'pushed': bulk_succeeded,
                            'failed': bulk_failed,
                            'skipped_no_listing': skipped,
                            'sync_step': 'bulk_push',
                            'batch_num': batch_num,
                            'total_batches': total_batches,
                        },
                    )

            def _sears_report_wait_heartbeat(
                *,
                document_id,
                feed_label='',
                wait_phase='',
                attempt=0,
                max_attempts=0,
                batch_num=0,
                total_batches=0,
                **_kwargs,
            ):
                nonlocal last_progress_log_at, bulk_succeeded, bulk_failed
                now_mono = time.monotonic()
                if now_mono - last_progress_log_at < PUSH_LISTINGS_PROGRESS_LOG_SEC:
                    return
                last_progress_log_at = now_mono
                processed = skipped + bulk_succeeded + bulk_failed
                batch_label = batch_num or 0
                batches_label = total_batches or total_batches_estimate
                append_catalog_log(
                    store.id,
                    f'Waiting for Sears processing report {document_id} '
                    f'({feed_label or "feed"}, batch {batch_label:,}/{batches_label:,}, '
                    f'{wait_phase or "polling"} poll {attempt}/{max_attempts}).',
                    action_type='sync_progress',
                    metadata={
                        'processed': processed,
                        'total': total_to_push,
                        'pushed': bulk_succeeded,
                        'failed': bulk_failed,
                        'skipped_no_listing': skipped,
                        'sync_step': 'waiting_sears',
                        'batch_num': batch_label,
                        'total_batches': batches_label,
                        'sears_document_id': str(document_id or ''),
                    },
                )

            stats = flush_sears_bulk_marketplace_push(
                store,
                bulk_queue,
                price_by_vendor_id=price_by_vid,
                price_fallback=price_fb,
                on_batch_progress=_sears_bulk_batch_progress,
                on_report_wait=_sears_report_wait_heartbeat,
                lock_owner=str(store_id),
                should_abort=lambda: should_abort_push_listings(str(store_id)),
            )
            if stats.get('reason') == 'sears_seller_busy':
                append_catalog_log(
                    store.id,
                    stats.get('errors', [{}])[0].get('error', 'Another Sears sync is already running.')
                    if stats.get('errors')
                    else 'Another Sears sync is already running for this seller account.',
                    action_type='info',
                )
            succeeded = int(stats.get('push_ok') or 0)
            failed = int(stats.get('push_fail') or 0)
            if stats.get('cancelled') or should_abort_push_listings(str(store_id)):
                cancelled = True
            errors_by_sku = {str(e.get('sku') or ''): e.get('error') for e in (stats.get('errors') or [])}
            warnings_by_sku = stats.get('warnings') or {}
            ok_skus = {str(s) for s in (stats.get('ok_skus') or set())}
            attempted_skus = ok_skus | set(errors_by_sku.keys())

            for pm, listing_id in queued_pairs:
                if cancelled and str(listing_id) not in attempted_skus:
                    continue
                if listing_id in ok_skus or str(listing_id) in ok_skus:
                    ReverbUpdateLog.objects.create(
                        product_mapping=pm,
                        status=ReverbUpdateLog.Status.SUCCESS,
                        pushed_price=pm.store_price,
                        pushed_stock=pm.store_stock,
                        error_message=(warnings_by_sku.get(listing_id) or '')[:500] or None,
                    )
                else:
                    ReverbUpdateLog.objects.create(
                        product_mapping=pm,
                        status=ReverbUpdateLog.Status.FAILED,
                        error_message=(errors_by_sku.get(listing_id) or pm.scrape_error or 'marketplace_push_failed')[:500],
                    )
        else:
            for pm in qs.iterator(chunk_size=100):
                _raise_if_push_aborted(store_id)
                listing_id = _resolve_listing_id_for_pm(adapter, pm, store)
                if not listing_id:
                    skipped += 1
                    ReverbUpdateLog.objects.create(
                        product_mapping=pm,
                        status=ReverbUpdateLog.Status.FAILED,
                        error_message='No marketplace listing ID or resolvable SKU for push',
                    )
                else:
                    try:
                        from catalog.marketplace_push import push_product_mapping_to_marketplace

                        ok, err_or_warn = push_product_mapping_to_marketplace(
                            pm,
                            store,
                            price_by_vendor_id=price_by_vid,
                            price_fallback=price_fb,
                        )
                        if not ok:
                            raise ValueError(err_or_warn or 'marketplace_push_failed')
                        now_ok = timezone.now()
                        pm.sync_status = 'synced'
                        pm.last_sync_time = now_ok
                        pm.save(update_fields=['sync_status', 'last_sync_time'])
                        ReverbUpdateLog.objects.create(
                            product_mapping=pm,
                            status=ReverbUpdateLog.Status.SUCCESS,
                            pushed_price=pm.store_price,
                            pushed_stock=pm.store_stock,
                            error_message=(err_or_warn or '')[:500] if err_or_warn else None,
                        )
                        succeeded += 1
                    except (ReverbAPIError, SearsAPIError, WalmartAPIError) as e:
                        failed += 1
                        logger.warning("Manual push failed for %s: %s", pm.id, e)
                        ReverbUpdateLog.objects.create(
                            product_mapping=pm,
                            status=ReverbUpdateLog.Status.FAILED,
                            http_status=getattr(e, 'status_code', None),
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

                processed = succeeded + failed + skipped
                now_mono = time.monotonic()
                if (
                    processed % PUSH_LISTINGS_PROGRESS_EVERY == 0
                    or now_mono - last_progress_log_at >= PUSH_LISTINGS_PROGRESS_LOG_SEC
                ):
                    last_progress_log_at = now_mono
                    append_catalog_log(
                        store.id,
                        f'Marketplace sync in progress: {processed:,} of {total_to_push:,} processed '
                        f'({succeeded:,} pushed, {failed:,} failed, {skipped:,} skipped).',
                        action_type='sync_progress',
                        metadata={
                            'processed': processed,
                            'total': total_to_push,
                            'pushed': succeeded,
                            'failed': failed,
                            'skipped_no_listing': skipped,
                        },
                    )

    except PushListingsCancelled:
        cancelled = True

    if cancelled:
        return _return_push_listings_cancelled(
            store, succeeded, failed, skipped, total_to_push,
        )

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
        'total': total_to_push,
    }


@shared_task(bind=True)
def run_store_push_listings_only(self, store_id, disable_schedule=False):
    """Celery entry: one push per store at a time (see sync.push_listings_lock)."""
    from django.core.cache import cache

    from sync.push_listings_cancel import clear_push_listings_cancel, should_abort_push_listings
    from sync.push_listings_lock import (
        push_listings_lock_key,
        release_push_listings_lock,
        try_acquire_push_listings_lock,
    )

    store_key = str(store_id)
    task_id = str(self.request.id)
    lock_owner = cache.get(push_listings_lock_key(store_key))
    if lock_owner != task_id and not try_acquire_push_listings_lock(store_key, task_id):
        return {
            'error': 'push_already_running',
            'store_id': store_key,
            'hint': 'A marketplace push is already running for this store.',
        }
    try:
        if should_abort_push_listings(store_key):
            return {
                'store_id': store_key,
                'pushed': 0,
                'failed': 0,
                'skipped_no_listing': 0,
                'total': 0,
                'cancelled': True,
            }
        return _execute_store_push_listings_only(store_id, disable_schedule=disable_schedule)
    finally:
        clear_push_listings_cancel(store_key)
        release_push_listings_lock(store_key, task_id)


@shared_task
def run_store_critical_zero_inventory(store_id):
    """
    Set all active listing stock to 0 locally and on the marketplace, deactivate the store
    and its sync schedule (emergency stop).

    Walmart: uses each listing's ``fulfillment_center_id`` as ship node (same as normal push),
    not only the store default ship node from API credentials.
    """
    import logging
    from store_adapters import get_adapter
    from store_adapters.reverb_adapter import ReverbAPIError
    from store_adapters.sears_adapter import SearsAPIError
    from store_adapters.walmart_adapter import WalmartAPIError
    from sync.models import SyncSchedule

    logger = logging.getLogger(__name__)
    try:
        store = Store.objects.select_related('marketplace').get(id=store_id)
    except Store.DoesNotExist:
        return {'error': 'store_not_found'}

    adapter = get_adapter(store)
    pushed, push_failed, local_zeroed = 0, 0, 0
    price_by_vid, price_fb, _, _ = _build_store_vendor_pricing_inventory_caches(store)

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
            kwargs = _adapter_push_kwargs(
                store,
                pm,
                pm.store_price,
                0,
                price_by_vid,
                price_fb,
            )
            if 'stock' not in kwargs:
                kwargs['stock'] = 0
            adapter.update_product(listing_id, **kwargs)
            pushed += 1
        except (ReverbAPIError, SearsAPIError, WalmartAPIError, ValueError) as e:
            push_failed += 1
            logger.warning("Critical zero push failed for listing %s: %s", listing_id, e)
        except Exception as e:
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


@shared_task
def run_store_failed_zero_inventory(store_id):
    """
    Set stock to 0 locally and on the marketplace for failed / needs_attention
    listings only. Store and schedule stay active (unlike critical zero).
    """
    import logging
    from store_adapters import get_adapter
    from store_adapters.reverb_adapter import ReverbAPIError
    from store_adapters.sears_adapter import SearsAPIError
    from store_adapters.walmart_adapter import WalmartAPIError
    from catalog.activity_log import append_catalog_log

    logger = logging.getLogger(__name__)
    try:
        store = Store.objects.select_related('marketplace').get(id=store_id)
    except Store.DoesNotExist:
        return {'error': 'store_not_found'}

    if store.connection_status != 'connected':
        return {
            'error': 'not_connected',
            'hint': 'Validate store connection before zeroing failed listings.',
            'store_id': str(store_id),
        }

    adapter = get_adapter(store)
    price_by_vid, price_fb, _, _ = _build_store_vendor_pricing_inventory_caches(store)

    qs = ProductMapping.objects.filter(
        store=store,
        is_active=True,
        sync_status__in=['failed', 'needs_attention'],
        store_price__isnull=False,
    ).select_related('product', 'product__vendor')

    local_zeroed = pushed = push_failed = skipped = 0

    for pm in qs.iterator(chunk_size=100):
        listing_id = _resolve_listing_id_for_pm(adapter, pm, store)
        if not listing_id:
            skipped += 1
            continue

        pm.store_stock = 0
        pm.save(update_fields=['store_stock'])
        local_zeroed += 1

        try:
            from catalog.marketplace_push import push_product_mapping_to_marketplace

            ok, err = push_product_mapping_to_marketplace(
                pm,
                store,
                price_by_vendor_id=price_by_vid,
                price_fallback=price_fb,
            )
            if not ok:
                push_failed += 1
                logger.warning('Failed-listing zero push failed for %s: %s', pm.id, err)
            else:
                pushed += 1
                pm.last_sync_time = timezone.now()
                pm.save(update_fields=['last_sync_time'])
        except (ReverbAPIError, SearsAPIError, WalmartAPIError, ValueError) as e:
            push_failed += 1
            logger.warning('Failed-listing zero push failed for %s: %s', pm.id, e)
        except Exception as e:
            push_failed += 1
            logger.exception('Failed-listing zero push error for %s: %s', pm.id, e)

    append_catalog_log(
        store.id,
        (
            f'Zero inventory for failed listings: {pushed} marketplace update(s) ok, '
            f'{push_failed} failed, {skipped} skipped (no listing id), '
            f'{local_zeroed} zeroed locally.'
        ),
        action_type='failed_zero_inventory',
    )

    return {
        'store_id': str(store_id),
        'local_zeroed': local_zeroed,
        'marketplace_push_ok': pushed,
        'marketplace_push_failed': push_failed,
        'skipped_no_listing': skipped,
    }
