"""Desktop-runner vendors (HEB, Costco AU without proxies) for managed Inventory management.

These vendors cannot be fetched by the server. Start Scraping leaves their
listings Pending and queues a ``HebScrapeJob``; the desktop runner pulls the
Vendor URLs from ``next-job`` and posts results to ``/api/v1/ingest/<vendor>/``.
Results land on the listing exactly like a server scrape: Scraped with
vendor price, store price and stock from the store rules, or Failed.
"""
from __future__ import annotations

import logging

from django.utils import timezone

from .models import InventorySyncStatus, ListingStatus, StoreListing

logger = logging.getLogger("listings")

DESKTOP_RUNNER_VENDORS = ("heb", "costco")

_INVENTORY_LISTING_STATUSES = (
    ListingStatus.UPLOADED_STAGING,
    ListingStatus.UPLOADED_PRODUCTION,
    ListingStatus.READY,
    ListingStatus.FAILED,
)


def desktop_runner_vendor(listing) -> str:
    """``'heb'`` / ``'costco'`` when this listing's price comes from the desktop runner."""
    from .template_routing import is_nora_like

    if is_nora_like((getattr(listing, "source_vendor_code", None) or "").strip()):
        return ""
    url = (getattr(listing, "vendor_url", None) or "").strip().lower()
    if "heb.com" in url:
        return "heb"
    if "costco.com.au" in url:
        from catalog.tasks import _costco_au_runs_on_server

        if not _costco_au_runs_on_server():
            return "costco"
    return ""


def queue_desktop_runner_jobs(user, store, groups: dict) -> dict:
    """Queue one pending runner job per vendor that has listings waiting.

    A job the runner already claimed carries its own URL list, so a new
    pending job is created unless one is already waiting.
    """
    from catalog.models import HebScrapeJob

    queued: dict[str, int] = {}
    for vendor_code, group in (groups or {}).items():
        if not group:
            continue
        waiting = HebScrapeJob.objects.filter(
            store=store,
            vendor_code=vendor_code,
            status=HebScrapeJob.Status.PENDING,
        ).exists()
        if not waiting:
            HebScrapeJob.objects.create(
                store=store,
                requested_by=user,
                vendor_code=vendor_code,
            )
        queued[vendor_code] = len(group)
    return queued


def _runner_listings_qs(url_host_contains: str, store_id, restrict_to_user_id):
    qs = StoreListing.objects.filter(
        store__management_mode="full_store",
        status__in=_INVENTORY_LISTING_STATUSES,
        vendor_url__icontains=url_host_contains,
    )
    if store_id:
        qs = qs.filter(store_id=store_id)
    if restrict_to_user_id is not None:
        qs = qs.filter(store__user_id=restrict_to_user_id)
    elif not store_id:
        return StoreListing.objects.none()
    return qs


def listing_urls_for_runner(
    url_host_contains: str,
    store_id,
    *,
    restrict_to_user_id=None,
    pending_only: bool = False,
) -> list[str]:
    """Vendor URLs of managed listings the desktop runner should scrape."""
    qs = _runner_listings_qs(url_host_contains, store_id, restrict_to_user_id)
    if pending_only:
        qs = qs.filter(inventory_sync_status=InventorySyncStatus.PENDING)
    urls = {
        (u or "").strip()
        for u in qs.values_list("vendor_url", flat=True).distinct()
    }
    return sorted(u for u in urls if u)


def apply_runner_result_to_listings(
    url: str,
    price,
    stock,
    error_code: str | None,
    *,
    url_host_contains: str,
    restrict_to_user_id,
) -> int:
    """Write one desktop-runner result onto every matching managed listing.

    Only listings in stores owned by the ingest token owner are touched.
    Returns how many listings were updated.
    """
    if restrict_to_user_id is None or not url:
        return 0
    from sync.tasks import (
        _apply_inventory,
        _apply_pricing,
        _build_store_vendor_pricing_inventory_caches,
        _get_inventory_for_vendor_from_cache,
        _get_pricing_for_vendor_from_cache,
    )

    from .listing_service import (
        _LISTING_SCRAPE_SAVE_FIELDS,
        _safe_decimal,
        _vendor_id_from_source_code,
        _vendor_id_from_url,
    )

    listings = list(
        _runner_listings_qs(url_host_contains, None, restrict_to_user_id)
        .filter(vendor_url__iexact=url.strip())
        .select_related("store")
    )
    if not listings:
        return 0

    now = timezone.now()
    caches: dict = {}
    updated = 0
    for listing in listings:
        listing.last_scrape_at = now
        if price is None:
            listing.inventory_sync_status = InventorySyncStatus.FAILED
            listing.last_scrape_error = (
                error_code or "No price returned by the desktop runner."
            )[:500]
            listing.save(update_fields=list(_LISTING_SCRAPE_SAVE_FIELDS))
            updated += 1
            continue

        store = listing.store
        if store.id not in caches:
            caches[store.id] = _build_store_vendor_pricing_inventory_caches(store)
        price_by_vid, price_fb, inv_by_vid, inv_fb = caches[store.id]
        src = (listing.source_vendor_code or "").strip()
        vendor_id = (
            _vendor_id_from_source_code(src, price_by_vid, inv_by_vid)
            or _vendor_id_from_url(url, price_by_vid, inv_by_vid)
        )
        vp = _safe_decimal(price)
        priced = _apply_pricing(vp, _get_pricing_for_vendor_from_cache(vendor_id, price_by_vid, price_fb))
        if priced is None:
            priced = vp
        try:
            raw_stock = max(0, int(stock or 0))
        except (TypeError, ValueError):
            raw_stock = 0
        inventory_settings = _get_inventory_for_vendor_from_cache(vendor_id, inv_by_vid, inv_fb)

        listing.vendor_price = vp
        listing.sale_price = priced
        listing.original_price = priced
        cents = int(priced * 100)
        listing.sale_price_cents = cents
        listing.original_price_cents = cents
        listing.inventory = int(_apply_inventory(raw_stock, inventory_settings))
        listing.infinite_quantity = False
        listing.inventory_sync_status = InventorySyncStatus.SCRAPED
        listing.last_scrape_error = ""
        listing.save(update_fields=list(_LISTING_SCRAPE_SAVE_FIELDS))
        updated += 1
    return updated
