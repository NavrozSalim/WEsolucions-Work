"""Celery tasks for managed-store listings (tickets sync, etc.)."""
import logging

from celery import shared_task

from stores.models import Store

from . import ticket_service

logger = logging.getLogger("listings")


@shared_task(name="listings.fetch_all_store_tickets", ignore_result=True)
def fetch_all_store_tickets():
    """Hourly: pull customer tickets/messages for every active Lasoo managed store."""
    stores = (
        Store.objects.filter(
            is_active=True,
            management_mode="full_store",
            marketplace__code__iexact="lasoo",
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
                "Ticket sync store=%s ok=%s supported=%s fetched=%s msg=%s",
                store.id,
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
