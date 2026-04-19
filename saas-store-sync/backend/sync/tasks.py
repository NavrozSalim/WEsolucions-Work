from celery import shared_task
from django.utils import timezone
from decimal import Decimal
import logging
import math
import re

logger = logging.getLogger(__name__)

from stores.models import Store, StoreVendorPriceSettings, StoreVendorInventorySettings
from stores.pricing_tiers import resolve_margin_tier_for_raw_cost
from catalog.models import ProductMapping
from catalog.reverb_catalog import listing_sku_lookup_order
from vendor.models import VendorPrice
from sync.models import StoreSyncRun
from scrapers import get_price_and_stock, close_amazon_session
from catalog.vendor_price_fallback import get_last_known_vendor_price_stock


def _is_heb_product(product) -> bool:
    """Return True when ``product`` belongs to the HEB vendor.

    HEB is ingest-only: prices are POSTed from a desktop runner to
    ``/api/v1/ingest/heb/``. Server-side scrape loops must skip HEB rows so
    the listing is not marked ``failed`` / ``needs_attention`` just because
    there was no live scrape.
    """
    vendor = getattr(product, 'vendor', None)
    code = (getattr(vendor, 'code', '') or '').lower()
    return code in ('heb', 'hebus') or code.startswith('heb_')


def _is_ingest_only_product(product) -> bool:
    """Vendors whose price/stock comes from a desktop runner or S3 feed
    (HEB, Costco AU, Vevor AU). There is no live server-side scraper for
    these; ``_apply_latest_heb_ingest`` promotes the most recent VendorPrice
    through the current pricing rules so margin edits take effect without
    a new ingest."""
    vendor = getattr(product, 'vendor', None)
    code = (getattr(vendor, 'code', '') or '').lower()
    if code in ('heb', 'hebus', 'costcoau', 'costco_au', 'costco-au', 'vevor', 'vevorau'):
        return True
    if code.startswith('heb_') or code.startswith('costco_') or code.startswith('vevor_'):
        return True
    return False


def _fail_mapping(pm, code: str, message: str = '') -> None:
    """Strict live-scrape failure: clear posted price/stock, mark failed,
    and record the reason. Do **not** fall back to historical VendorPrice —
    stale data silently pushed to a marketplace is worse than no update.
    """
    pm.store_price = None
    pm.store_stock = None
    pm.failed_sync_count = (pm.failed_sync_count or 0) + 1
    pm.sync_status = 'needs_attention' if pm.failed_sync_count >= 3 else 'failed'
    reason = (code or 'scrape_failed').strip() or 'scrape_failed'
    if message:
        reason = f'{reason}: {str(message)[:240]}'
    pm.scrape_error = reason[:512]
    pm.save(update_fields=[
        'store_price',
        'store_stock',
        'failed_sync_count',
        'sync_status',
        'scrape_error',
    ])


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


def _costco_product_id_from_value(value: str):
    """
    Extract Costco AU numeric product id from mixed values like:
    - 173734
    - TFCO-173734-New
    """
    raw = (value or "").strip().replace("_", "-")
    if not raw:
        return None
    if raw.isdigit() and 5 <= len(raw) <= 12:
        return raw

    parts = [p for p in re.split(r"[-/]+", raw) if p]
    for p in parts:
        if p.isdigit() and 5 <= len(p) <= 12:
            return p

    m = re.search(r"\d{5,12}", raw)
    if m:
        return m.group(0)
    return None


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
    if vcode in ('costcoau', 'costco_au', 'costco-au'):
        pid = _costco_product_id_from_value(sku)
        if pid:
            return f"https://www.costco.com.au/p/{pid}"
        return None
    return None


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
    if vcode in ('costcoau', 'costco_au', 'costco-au'):
        pid = _costco_product_id_from_value(vid)
        if pid:
            return f'https://www.costco.com.au/p/{pid}'
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
    is_costco_au = vcode in ('costcoau', 'costco_au', 'costco-au')

    if catalog_row is not None:
        if is_costco_au:
            vid = _normalize(getattr(catalog_row, 'vendor_id_raw', None))
            built = _vendor_url_from_vendor_id(vendor, vid or '', store.region or 'USA')
            if built:
                return built
        u = _normalize(getattr(catalog_row, 'vendor_url_raw', None))
        if u:
            return u
        vid = _normalize(getattr(catalog_row, 'vendor_id_raw', None))
        if vid and vendor:
            built = _vendor_url_from_vendor_id(vendor, vid, store.region or 'USA')
            if built:
                return built

    if product and product.vendor_url and not is_costco_au:
        u = str(product.vendor_url).strip()
        if u:
            return u
    if product and product.vendor_url and is_costco_au:
        # Canonicalize legacy Costco URLs like /p/TFCO-173734-New -> /p/173734
        u = str(product.vendor_url).strip()
        pid = _costco_product_id_from_value(u.rsplit("/", 1)[-1])
        if pid:
            return f'https://www.costco.com.au/p/{pid}'

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


def _apply_latest_heb_ingest(pm, product, store, now=None, *, scrape_title: str = '') -> bool:
    """Promote the most recent ingested VendorPrice onto a HEB ProductMapping.

    HEB is ingest-only on the server: the desktop runner POSTs scraped prices
    to ``/api/v1/ingest/heb/``. When the user clicks "Scrape data" we don't
    hit the internet for HEB — we just re-apply the newest ``VendorPrice`` row
    that the ingest API stored.

    Returns
    -------
    bool
        True if the mapping was updated (caller should count this as a
        success), False if there is no recent VendorPrice to apply (caller
        should simply skip; never mark HEB as ``failed``).
    """
    p, s = get_last_known_vendor_price_stock(product)
    if p is None:
        return False

    try:
        vendor_price = Decimal(str(p))
    except Exception:
        return False
    vendor_stock = int(s or 0)

    pricing = _get_pricing_for_vendor(store, product.vendor_id)
    inventory = _get_inventory_for_vendor(store, product.vendor_id)

    new_price = _apply_pricing(
        vendor_price,
        pricing,
        pack_qty=getattr(pm, 'pack_qty', None),
        prep_fees=getattr(pm, 'prep_fees', None),
        shipping_fees=getattr(pm, 'shipping_fees', None),
    )
    if new_price is None:
        new_price = vendor_price
    new_stock = _apply_inventory(vendor_stock, inventory)

    pm.store_price = new_price
    pm.store_stock = new_stock
    pm.sync_status = 'scraped'
    pm.failed_sync_count = 0
    pm.last_scrape_time = now or timezone.now()
    update_fields = [
        'store_price',
        'store_stock',
        'sync_status',
        'failed_sync_count',
        'last_scrape_time',
    ]
    if scrape_title:
        pm.title = scrape_title[:500]
        update_fields.append('title')
    pm.save(update_fields=update_fields)
    return True


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
            # HEB is ingest-only; live scrape is disabled. Promote the latest
            # ingested VendorPrice (posted by the desktop runner) onto the
            # mapping. Never mark HEB rows as 'failed' just because there's
            # no cached price yet — that's the job of the ingest API.
            if pm.product and _is_ingest_only_product(pm.product):
                if _apply_latest_heb_ingest(pm, pm.product, store, now):
                    updated += 1
                    logger.info(
                        "Ingest-only row refreshed (sku=%s vendor=%s)",
                        getattr(pm.product, 'vendor_sku', '?'),
                        (pm.product.vendor.code if pm.product.vendor else '?'),
                    )
                else:
                    logger.info(
                        "Ingest-only row skipped, no ingest data yet (sku=%s vendor=%s)",
                        getattr(pm.product, 'vendor_sku', '?'),
                        (pm.product.vendor.code if pm.product.vendor else '?'),
                    )
                continue
            pricing = _get_pricing_for_vendor(store, pm.product.vendor_id)
            inventory = _get_inventory_for_vendor(store, pm.product.vendor_id)
            url = resolve_vendor_scrape_url(pm.product, store, None)
            vendor_price = None
            vendor_stock = 0
            scrape_title = ''
            result = {}
            try:
                if not url:
                    raise ValueError("Product has no vendor_url or resolvable SKU")
                result = get_price_and_stock(url, store.region, session)
                vendor_price = result.get('price')
                vendor_stock = _inventory_from_scrape_result(result)
                scrape_title = (result.get('title') or '').strip()[:500]
            except Exception as e:
                logger.exception(
                    "Store sync scrape error for %s: %s", pm.product.vendor_sku, e,
                )
                _fail_mapping(pm, 'scrape_exception', str(e))
                error_summary = str(e) if not error_summary else error_summary
                continue

            if vendor_price is None:
                err_code = (
                    result.get('error_code') if isinstance(result, dict) else None
                ) or 'no_price'
                err_msg = (
                    result.get('error_message') if isinstance(result, dict) else ''
                ) or ''
                _fail_mapping(pm, err_code, err_msg)
                error_summary = err_code if not error_summary else error_summary
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
        bulk_supported = callable(getattr(adapter, 'update_products_bulk', None))
        bulk_queue = []  # list of (pm, sku, price, stock)
        for pm in mappings:
            processed += 1
            # HEB is ingest-only; promote latest VendorPrice to the mapping
            # (see _apply_latest_heb_ingest). If a marketplace listing id is
            # resolvable we still queue the HEB row for push so any freshly
            # ingested price flows through to the marketplace on this run.
            if pm.product and _is_ingest_only_product(pm.product):
                if _apply_latest_heb_ingest(pm, pm.product, store, now):
                    updated += 1
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
                    if listing_id and pm.store_price is not None:
                        try:
                            if bulk_supported:
                                bulk_queue.append(
                                    (pm, listing_id, float(pm.store_price), int(pm.store_stock or 0))
                                )
                            else:
                                adapter.update_product(
                                    listing_id,
                                    price=float(pm.store_price),
                                    stock=int(pm.store_stock or 0),
                                )
                                push_ok += 1
                                pm.sync_status = 'synced'
                                pm.last_sync_time = timezone.now()
                                pm.save(update_fields=['sync_status', 'last_sync_time'])
                        except Exception as push_err:
                            logger.warning(
                                "Push failed for HEB %s: %s",
                                pm.marketplace_child_sku,
                                push_err,
                            )
                            push_fail += 1
                            _record_push_error(
                                pm.marketplace_child_sku or pm.product.vendor_sku,
                                push_err,
                            )
                    elif pm.store_price is not None and not listing_id:
                        push_skipped += 1
                else:
                    logger.info(
                        "Ingest-only row skipped, no ingest data yet (sku=%s vendor=%s)",
                        getattr(pm.product, 'vendor_sku', '?'),
                        (pm.product.vendor.code if pm.product.vendor else '?'),
                    )
                continue
            pricing = _get_pricing_for_vendor(store, pm.product.vendor_id)
            inventory = _get_inventory_for_vendor(store, pm.product.vendor_id)

            # --- Scrape ---
            url = resolve_vendor_scrape_url(pm.product, store, None)
            vendor_price = None
            vendor_stock = 0
            scrape_title = ''
            result = {}
            try:
                if not url:
                    raise ValueError("Product has no vendor_url or resolvable SKU")
                result = get_price_and_stock(url, store.region, session)
                vendor_price = result.get('price')
                vendor_stock = _inventory_from_scrape_result(result)
                scrape_title = (result.get('title') or '').strip()[:500]
            except Exception as e:
                logger.exception(
                    "Scheduled update scrape error for %s: %s",
                    pm.product.vendor_sku, e,
                )
                _fail_mapping(pm, 'scrape_exception', str(e))
                error_summary = str(e) if not error_summary else error_summary
                continue

            if vendor_price is None:
                err_code = (
                    result.get('error_code') if isinstance(result, dict) else None
                ) or 'no_price'
                err_msg = (
                    result.get('error_message') if isinstance(result, dict) else ''
                ) or ''
                _fail_mapping(pm, err_code, err_msg)
                error_summary = err_code if not error_summary else error_summary
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
                        )
                        error_summary = 'missing_fixed_inputs' if not error_summary else error_summary
                        continue

            prev_vp = VendorPrice.objects.filter(product=pm.product).order_by('-scraped_at').first()
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
            if new_price is None and vendor_price is not None:
                new_price = Decimal(str(vendor_price))
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
            pm.scrape_error = None
            _uf = [
                'store_price', 'store_stock', 'sync_status',
                'failed_sync_count', 'last_scrape_time', 'scrape_error',
            ]
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
                    if bulk_supported:
                        bulk_queue.append((pm, listing_id, float(new_price), int(new_stock or 0)))
                    else:
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

    # Bulk push (Kogan sheets) after scraping loop
    if bulk_supported and bulk_queue:
        try:
            payload = [(sku, price, stock) for (_pm, sku, price, stock) in bulk_queue]
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
            logger.warning("Bulk push failed: %s", e)
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
