"""Celery tasks for managed-store listings (orders + tickets sync + inventory scrape)."""
import logging

from celery import shared_task
from django.contrib.auth import get_user_model

from stores.models import Store

from . import order_service, ticket_service
from .errors import MarketplaceError

logger = logging.getLogger("listings")


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


@shared_task(name="listings.fetch_all_store_tickets", ignore_result=True)
def fetch_all_store_tickets():
    """Hourly: pull tickets/conversations for every active Lasoo + Reverb managed store."""
    stores = (
        Store.objects.filter(
            is_active=True,
            management_mode="full_store",
            marketplace__code__in=["lasoo", "reverb"],
        )
        .select_related("marketplace", "user")
    )
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
                "Ticket sync store=%s marketplace=%s ok=%s supported=%s fetched=%s msg=%s",
                store.id,
                getattr(store.marketplace, "code", None),
                result.get("ok"),
                result.get("marketplace_supported"),
                fetched,
                (result.get("message") or "")[:160],
            )
        except Exception:  # noqa: BLE001
            logger.exception("Ticket sync failed store=%s", store.id)

    return {
        "stores": store_count,
        "fetched": total_fetched,
    }


@shared_task(name="listings.fetch_all_reverb_orders", ignore_result=True)
def fetch_all_reverb_orders():
    """Hourly: incremental Reverb order pull for every active Reverb managed store."""
    stores = (
        Store.objects.filter(
            is_active=True,
            management_mode="full_store",
            marketplace__code__iexact="reverb",
        )
        .select_related("marketplace", "user")
    )
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
                "Reverb order sync store=%s ok=%s fetched=%s msg=%s",
                store.id,
                result.get("ok"),
                fetched,
                (result.get("message") or "")[:160],
            )
        except Exception:  # noqa: BLE001
            logger.exception("Reverb order sync failed store=%s", store.id)

    return {
        "stores": store_count,
        "fetched": total_fetched,
    }
