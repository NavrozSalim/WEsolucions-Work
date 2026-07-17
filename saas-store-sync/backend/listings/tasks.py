"""Celery tasks for managed-store listings (orders + tickets sync + inventory scrape)."""
import logging

from celery import shared_task
from django.contrib.auth import get_user_model

from stores.models import Store

from . import order_service, ticket_service
from .errors import MarketplaceError

logger = logging.getLogger("listings")

# Store.region values used for US / AU order-ticket worker queues.
REGION_USA = "USA"
REGION_AU = "AU"
ORDER_TICKET_REGIONS = (REGION_USA, REGION_AU)


def _managed_stores_qs(*, region: str | None = None, marketplace_codes=None):
    """Active full_store queryset, optionally filtered by region and marketplace codes."""
    qs = Store.objects.filter(
        is_active=True,
        management_mode="full_store",
    ).select_related("marketplace", "user")
    if region:
        qs = qs.filter(region=region)
    if marketplace_codes is not None:
        qs = qs.filter(marketplace__code__in=list(marketplace_codes))
    return qs


def sync_store_tickets(*, region: str | None = None) -> dict:
    """Pull tickets for Lasoo + Reverb managed stores (optional region filter)."""
    stores = _managed_stores_qs(region=region, marketplace_codes=["lasoo", "reverb"])
    total_fetched = 0
    store_count = 0
    for store in stores:
        user = store.user
        if user is None:
            continue
        store_count += 1
        try:
            result = ticket_service.fetch(user, store, page=1, take=100)
            fetched = int(result.get("fetched") or 0)
            total_fetched += fetched
            logger.info(
                "Ticket sync region=%s store=%s marketplace=%s ok=%s supported=%s fetched=%s msg=%s",
                region or "all",
                store.id,
                getattr(store.marketplace, "code", None),
                result.get("ok"),
                result.get("marketplace_supported"),
                fetched,
                (result.get("message") or "")[:160],
            )
        except Exception:  # noqa: BLE001
            logger.exception("Ticket sync failed store=%s region=%s", store.id, region)
    return {"region": region or "all", "stores": store_count, "fetched": total_fetched}


def sync_store_orders(*, region: str | None = None, marketplace_codes=None) -> dict:
    """Pull orders for managed stores (default Lasoo + Reverb; optional region)."""
    codes = marketplace_codes if marketplace_codes is not None else ["lasoo", "reverb"]
    stores = _managed_stores_qs(region=region, marketplace_codes=codes)
    total_fetched = 0
    store_count = 0
    for store in stores:
        user = store.user
        if user is None:
            continue
        store_count += 1
        try:
            result = order_service.fetch(user, store)
            fetched = int(result.get("fetched") or 0)
            total_fetched += fetched
            logger.info(
                "Order sync region=%s store=%s marketplace=%s ok=%s fetched=%s msg=%s",
                region or "all",
                store.id,
                getattr(store.marketplace, "code", None),
                result.get("ok"),
                fetched,
                (result.get("message") or "")[:160],
            )
        except Exception:  # noqa: BLE001
            logger.exception("Order sync failed store=%s region=%s", store.id, region)
    return {"region": region or "all", "stores": store_count, "fetched": total_fetched}


@shared_task(name="listings.scrape_store_listings", bind=True, ignore_result=True)
def scrape_store_listings(self, user_id, store_id, listing_ids=None):
    """Background vendor scrape for managed Inventory management (survives page reload)."""
    from . import listing_service
    from . import scrape_progress as scrape_prog

    User = get_user_model()
    try:
        user = User.objects.get(pk=user_id)
        store = Store.objects.select_related("marketplace", "user").get(pk=store_id)
    except (User.DoesNotExist, Store.DoesNotExist):
        scrape_prog.finish_scrape_progress(
            store_id,
            message="Scrape cancelled: store or user not found.",
        )
        return {"ok": False, "error": "not_found"}

    scrape_prog.set_scrape_progress(
        store.id,
        active=True,
        phase="running",
        task_id=getattr(self.request, "id", None) or "",
        message="Worker started…",
    )
    try:
        return listing_service.scrape_listings(user, store, listing_ids)
    except MarketplaceError as exc:
        scrape_prog.finish_scrape_progress(
            store.id,
            message=str(exc)[:200],
        )
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Managed scrape task failed store=%s", store_id)
        scrape_prog.finish_scrape_progress(
            store.id,
            message=(str(exc) or "Scrape failed.")[:200],
        )
        return {"ok": False, "error": str(exc)}


@shared_task(name="listings.fetch_us_store_tickets", ignore_result=True)
def fetch_us_store_tickets():
    """Hourly on US orders VPS (queue orders-us): USA-region Lasoo + Reverb tickets."""
    return sync_store_tickets(region=REGION_USA)


@shared_task(name="listings.fetch_au_store_tickets", ignore_result=True)
def fetch_au_store_tickets():
    """Hourly on AU orders VPS (queue orders-au): AU-region Lasoo + Reverb tickets."""
    return sync_store_tickets(region=REGION_AU)


@shared_task(name="listings.fetch_us_store_orders", ignore_result=True)
def fetch_us_store_orders():
    """Hourly on US orders VPS (queue orders-us): USA-region Lasoo + Reverb orders."""
    return sync_store_orders(region=REGION_USA)


@shared_task(name="listings.fetch_au_store_orders", ignore_result=True)
def fetch_au_store_orders():
    """Hourly on AU orders VPS (queue orders-au): AU-region Lasoo + Reverb orders."""
    return sync_store_orders(region=REGION_AU)


@shared_task(name="listings.fetch_all_store_tickets", ignore_result=True)
def fetch_all_store_tickets():
    """Legacy/all-regions ticket sync (local/dev). Prefer fetch_us/au_store_tickets in prod."""
    return sync_store_tickets(region=None)


@shared_task(name="listings.fetch_all_reverb_orders", ignore_result=True)
def fetch_all_reverb_orders():
    """Legacy Reverb-only all-regions order sync. Prefer fetch_us/au_store_orders in prod."""
    return sync_store_orders(region=None, marketplace_codes=["reverb"])
