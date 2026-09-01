"""In-flight managed-listing scrape progress for Inventory management UI.

Progress is cached for the banner, but live counts are derived from StoreListing
rows (pending → scraped/failed), same idea as catalog Inventory management.

Cancel lives on a separate cache key so per-SKU progress writes cannot overwrite
the Stop flag (Gunicorn workers race on the progress blob).
"""
from __future__ import annotations

from django.core.cache import cache
from django.utils import timezone

_TTL = 60 * 60  # 1 hour
_KEY = "listings:scrape_progress:{store_id}"
_CANCEL_KEY = "listings:scrape_cancel:{store_id}"


def _sid(store_id) -> str:
    return str(store_id)


def _key(store_id) -> str:
    return _KEY.format(store_id=_sid(store_id))


def _cancel_key(store_id) -> str:
    return _CANCEL_KEY.format(store_id=_sid(store_id))


def get_scrape_progress(store_id) -> dict:
    data = cache.get(_key(store_id))
    if not isinstance(data, dict):
        data = {
            "active": False,
            "total": 0,
            "processed": 0,
            "scraped": 0,
            "failed": 0,
            "pct": 0,
            "phase": "done",
            "listing_ids": [],
            "cancel_requested": False,
            "cancelled": False,
        }
    else:
        data = dict(data)
    data["cancel_requested"] = bool(data.get("cancel_requested")) or is_scrape_cancel_requested(store_id)
    return data


def set_scrape_progress(store_id, **fields) -> dict:
    cur = get_scrape_progress(store_id)
    prev_total = int(cur.get("total") or 0)
    prev_ids = cur.get("listing_ids") if isinstance(cur.get("listing_ids"), list) else []
    cur.update(fields)
    if "total" not in fields and prev_total > 0:
        cur["total"] = prev_total
    if "listing_ids" not in fields and prev_ids:
        cur["listing_ids"] = prev_ids
    total = int(cur.get("total") or 0)
    processed = int(cur.get("processed") or 0)
    if total > 0:
        cur["pct"] = max(0, min(100, round(100.0 * processed / total)))
    else:
        cur["pct"] = 0
    # Dedicated cancel key is source of truth; never let a progress write clear it.
    cur["cancel_requested"] = is_scrape_cancel_requested(store_id)
    cache.set(_key(store_id), cur, _TTL)
    return cur


def clear_scrape_progress(store_id) -> None:
    cache.delete(_key(store_id))
    cache.delete(_cancel_key(store_id))


def begin_scrape_progress(
    store_id,
    *,
    total: int,
    message: str = "",
    listing_ids=None,
    phase: str = "running",
) -> dict:
    cache.delete(_cancel_key(store_id))
    ids = [str(x) for x in (listing_ids or [])]
    return set_scrape_progress(
        store_id,
        active=True,
        total=int(total or 0) or len(ids),
        processed=0,
        scraped=0,
        failed=0,
        pct=0,
        current_sku="",
        message=message or "Starting scrape…",
        phase=phase or "running",
        listing_ids=ids,
        started_at=timezone.now().isoformat(),
        cancelled=False,
    )


def request_scrape_cancel(store_id) -> dict:
    """Ask the in-flight managed scrape to stop after the current listing."""
    cur = get_scrape_progress(store_id)
    if not cur.get("active"):
        return cur
    cache.set(_cancel_key(store_id), True, _TTL)
    return set_scrape_progress(
        store_id,
        message="Stopping… finishing the current listing, then remaining stay Pending.",
    )


def is_scrape_cancel_requested(store_id) -> bool:
    return bool(cache.get(_cancel_key(store_id)))


def finish_scrape_progress(
    store_id,
    *,
    scraped: int = 0,
    failed: int = 0,
    message: str = "",
    cancelled: bool = False,
) -> dict:
    cur = get_scrape_progress(store_id)
    total = int(cur.get("total") or (scraped + failed) or 0)
    processed = int(cur.get("processed") or 0)
    if cancelled:
        processed = min(total, max(processed, scraped + failed))
    else:
        processed = total
    cache.delete(_cancel_key(store_id))
    return set_scrape_progress(
        store_id,
        active=False,
        total=total,
        processed=processed,
        scraped=scraped,
        failed=failed,
        pct=100 if total and not cancelled else (
            max(0, min(100, round(100.0 * processed / total))) if total else 0
        ),
        current_sku="",
        message=message or ("Scrape stopped." if cancelled else "Scrape finished."),
        phase="cancelled" if cancelled else "done",
        cancelled=bool(cancelled),
    )


def enrich_progress_from_listings(store_id) -> dict:
    """Refresh active scrape counts from DB listing statuses (catalog-style).

    When every targeted listing has left Pending, auto-finish a stuck banner.
    """
    from .models import InventorySyncStatus, StoreListing

    data = get_scrape_progress(store_id)
    if not data.get("active"):
        return data

    listing_ids = data.get("listing_ids") or []
    if not listing_ids:
        # Stale banner from older builds (queued with no batch ids).
        if (data.get("phase") or "") in ("queued", "running"):
            return finish_scrape_progress(
                store_id,
                scraped=int(data.get("scraped") or 0),
                failed=int(data.get("failed") or 0),
                message=data.get("message") or "Scrape finished.",
            )
        return data

    qs = StoreListing.objects.filter(id__in=listing_ids)
    total = int(data.get("total") or 0) or len(listing_ids)
    pending = qs.filter(inventory_sync_status=InventorySyncStatus.PENDING).count()
    scraped = qs.filter(inventory_sync_status=InventorySyncStatus.SCRAPED).count()
    failed = qs.filter(inventory_sync_status=InventorySyncStatus.FAILED).count()
    synced = qs.filter(inventory_sync_status=InventorySyncStatus.SYNCED).count()
    done = scraped + failed + synced
    processed = min(total, done)
    pct = max(0, min(100, round(100.0 * processed / total))) if total else 0

    # Still waiting on worker, but rows already moved — treat as running/done.
    phase = data.get("phase") or "running"
    stopping = is_scrape_cancel_requested(store_id)
    if pending == 0 and total > 0:
        return finish_scrape_progress(
            store_id,
            scraped=scraped + synced,
            failed=failed,
            cancelled=stopping,
            message=(
                (
                    f"Scrape stopped. {scraped + synced} listing(s) done"
                    + (f"; {failed} failed" if failed else "")
                    + ". Use Start Scraping to continue."
                )
                if stopping
                else (
                    f"Scraped {scraped + synced} listing(s)"
                    + (f"; {failed} failed" if failed else "")
                    + ". Use Manual sync or your schedule to push price/stock to the marketplace."
                )
            ),
        )

    if phase == "queued" and processed > 0:
        phase = "running"

    msg = f"Scraping {min(processed + (1 if pending else 0), total)} of {total}…"
    if stopping:
        msg = "Stopping… finishing the current listing, then remaining stay Pending."

    return set_scrape_progress(
        store_id,
        total=total,
        processed=processed,
        scraped=scraped,
        failed=failed,
        pct=pct,
        phase=phase,
        message=msg,
        active=True,
    )
