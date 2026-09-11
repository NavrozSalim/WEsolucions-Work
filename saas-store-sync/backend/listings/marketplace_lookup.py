"""Live marketplace SKU lookup for managed stores (Lasoo + Reverb + Bunnings + MyDeal)."""
from __future__ import annotations

import logging

from django.db.models import Q

from store_adapters import get_adapter
from store_adapters.reverb_adapter import ReverbAPIError
from stores.credentials import marketplace_kind

from .errors import MarketplaceError
from .lasoo.client import LasooClient
from .lasoo.connect_search import search_variant
from .lasoo.response import lookup_message
from .models import StoreListing

logger = logging.getLogger("listings")

LOOKUP_KINDS = ("lasoo", "reverb", "bunnings", "mydeal")


def _normalize_reverb_hit(row: dict) -> dict:
    state = row.get("state")
    if isinstance(state, dict):
        status = str(state.get("slug") or state.get("description") or state.get("name") or "").strip()
    else:
        status = str(state or "").strip()
    sku = str(row.get("sku") or "").strip()
    lid = row.get("id") or row.get("uuid")
    links = row.get("_links") if isinstance(row.get("_links"), dict) else {}
    web = links.get("web") if isinstance(links.get("web"), dict) else {}
    self_link = links.get("self") if isinstance(links.get("self"), dict) else {}
    url = (
        str(web.get("href") or "").strip()
        or str(self_link.get("href") or "").strip()
        or None
    )
    return {
        "product_key": str(lid or "").strip() or sku,
        "variant_key": sku,
        "sku": sku,
        "title": str(row.get("title") or "").strip(),
        "status": status or "unknown",
        "created_at": row.get("created_at") or row.get("createdAt") or None,
        "updated_at": row.get("updated_at") or row.get("updatedAt") or None,
        "published_at": row.get("published_at") or row.get("publishedAt") or None,
        "marketplace_id": str(lid) if lid is not None else None,
        "url": url,
    }


def _local_listing_summary(store, sku: str) -> dict | None:
    listing = (
        StoreListing.objects.filter(store=store)
        .filter(Q(sku=sku) | Q(external_variant_key=sku) | Q(external_product_key=sku))
        .order_by("-updated_at")
        .first()
    )
    if not listing:
        return None
    return {
        "id": str(listing.id),
        "sku": listing.sku,
        "variant_key": listing.external_variant_key,
        "product_key": listing.external_product_key,
        "title": listing.title,
        "status": listing.status,
        "action": listing.action,
        "created_at": listing.created_at.isoformat() if listing.created_at else None,
        "updated_at": listing.updated_at.isoformat() if listing.updated_at else None,
    }


def _lookup_lasoo(store, sku: str) -> dict:
    listing = (
        StoreListing.objects.filter(store=store)
        .filter(Q(sku=sku) | Q(external_variant_key=sku) | Q(external_product_key=sku))
        .first()
    )
    product_key = (
        (listing.external_product_key if listing else "") or sku
    ).strip()
    variant_key = (
        (listing.external_variant_key if listing else "") or sku
    ).strip()

    client = LasooClient(store)
    searched = search_variant(
        client,
        product_key=product_key,
        variant_key=variant_key,
        sku=sku,
    )
    if not searched.get("ok"):
        raise MarketplaceError(
            searched.get("message") or "Could not search Lasoo for this SKU."
        )

    hits = [searched["hit"]] if searched.get("found") and searched.get("hit") else []
    advertised = searched.get("advertised")
    mapping_errors = list(searched.get("mapping_errors") or [])
    return {
        "ok": True,
        "found": bool(searched.get("found")),
        "advertised": advertised,
        "mapping_errors": mapping_errors,
        "marketplace": "lasoo",
        "environment": client.environment,
        "query": searched.get("query") or {
            "sku": sku, "product_key": product_key, "variant_key": variant_key,
        },
        "message": searched.get("message") or lookup_message(
            found=bool(searched.get("found")),
            advertised=advertised,
            mapping_errors=mapping_errors,
        ),
        "results": hits[:10],
        "local_listing": _local_listing_summary(store, sku),
    }


def _lookup_reverb(store, sku: str) -> dict:
    if not (getattr(store, "api_token", None) or "").strip():
        raise MarketplaceError(
            "No Reverb API token configured for this store. Add it in store settings."
        )
    adapter = get_adapter(store)
    try:
        raw_listings = adapter.find_listings_by_sku(sku)
    except ReverbAPIError as exc:
        raise MarketplaceError(str(exc) or "Reverb listing lookup failed.") from exc

    hits = [_normalize_reverb_hit(r) for r in raw_listings if isinstance(r, dict)]
    found = bool(hits)
    return {
        "ok": True,
        "found": found,
        "marketplace": "reverb",
        "environment": "production",
        "query": {"sku": sku},
        "message": (
            "Found on Reverb."
            if found
            else "Not found on Reverb for this SKU (checked live + draft)."
        ),
        "results": hits[:10],
        "local_listing": _local_listing_summary(store, sku),
    }


def _lookup_bunnings(store, sku: str) -> dict:
    from .bunnings import products as bunnings_products

    try:
        offer = bunnings_products.lookup_offer(store, sku)
    except MarketplaceError as exc:
        raise MarketplaceError(str(exc) or "Bunnings offer lookup failed.") from exc

    found = bool(offer)
    hits = []
    if offer:
        shop_sku = str(offer.get("shop_sku") or offer.get("sku") or sku).strip()
        hits.append({
            "product_key": str(offer.get("product_id") or shop_sku).strip(),
            "variant_key": shop_sku,
            "sku": shop_sku,
            "title": str(offer.get("product_title") or offer.get("description") or "").strip(),
            "status": str(offer.get("active") if offer.get("active") is not None else offer.get("state_code") or "unknown"),
            "created_at": offer.get("date_created"),
            "updated_at": offer.get("last_updated_date") or offer.get("date_created"),
            "published_at": None,
            "marketplace_id": str(offer.get("offer_id") or offer.get("id") or shop_sku),
            "url": None,
        })
    return {
        "ok": True,
        "found": found,
        "marketplace": "bunnings",
        "environment": getattr(store, "bunnings_environment", None) or "production",
        "query": {"sku": sku},
        "message": "Found on Bunnings." if found else "Not found on Bunnings for this SKU.",
        "results": hits[:10],
        "local_listing": _local_listing_summary(store, sku),
    }


def _norm_id(value) -> str:
    return str(value or "").strip()


def _mydeal_groups(payload) -> list[dict]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    inner = payload.get("Data")
    if inner is None:
        inner = payload.get("data")
    if isinstance(inner, list):
        return [row for row in inner if isinstance(row, dict)]
    if isinstance(inner, dict):
        return [inner]
    if payload.get("ProductSKU") or payload.get("BuyableProducts") or payload.get("ExternalProductId"):
        return [payload]
    return []


def _mydeal_is_not_found(result) -> bool:
    if getattr(result, "status", 0) == 404:
        return True
    parts = [getattr(result, "message", "") or "", getattr(result, "response_status", "") or ""]
    data = getattr(result, "data", None)
    if isinstance(data, dict):
        parts.append(str(data.get("ResponseStatus") or ""))
        errors = data.get("Errors") or data.get("errors") or []
        if isinstance(errors, list):
            for err in errors:
                if isinstance(err, dict):
                    parts.append(str(err.get("ID") or err.get("Id") or err.get("Code") or ""))
                    parts.append(str(err.get("Message") or err.get("message") or ""))
                else:
                    parts.append(str(err))
    blob = " ".join(parts).lower()
    compact = blob.replace(" ", "")
    return "productnotfound" in compact or "not found" in blob or "5000" in blob


def _mydeal_buyable_ids(buyable: dict) -> set[str]:
    return {
        value
        for value in (
            _norm_id(buyable.get("SKU")),
            _norm_id(buyable.get("ExternalBuyableProductID")),
            _norm_id(buyable.get("ExternalBuyableProductId")),
        )
        if value
    }


def _mydeal_group_ids(group: dict) -> set[str]:
    return {
        value
        for value in (
            _norm_id(group.get("ProductSKU")),
            _norm_id(group.get("ExternalProductId")),
            _norm_id(group.get("ExternalProductID")),
        )
        if value
    }


def _mydeal_id_match(ids: set[str], needles: list[str]) -> bool:
    wanted = {_norm_id(item) for item in needles if _norm_id(item)}
    if not wanted or not ids:
        return False
    if ids & wanted:
        return True
    folded = {item.casefold() for item in ids}
    return any(item.casefold() in folded for item in wanted)


def _mydeal_pick_buyable(group: dict, needles: list[str]) -> dict | None:
    buyables = [row for row in (group.get("BuyableProducts") or []) if isinstance(row, dict)]
    for buyable in buyables:
        if _mydeal_id_match(_mydeal_buyable_ids(buyable), needles):
            return buyable
    if buyables and _mydeal_id_match(_mydeal_group_ids(group), needles):
        return buyables[0]
    return buyables[0] if buyables else None


def _mydeal_hit(group: dict, buyable: dict | None, sku: str) -> dict:
    status = _norm_id((buyable or {}).get("ListingStatus")) or "unknown"
    advertised = status.lower() == "live"
    product_key = (
        _norm_id(group.get("ProductSKU"))
        or _norm_id(group.get("ExternalProductId"))
        or sku
    )
    variant_key = (
        _norm_id((buyable or {}).get("SKU"))
        or _norm_id((buyable or {}).get("ExternalBuyableProductID"))
        or sku
    )
    return {
        "product_key": product_key,
        "variant_key": variant_key,
        "sku": variant_key,
        "title": _norm_id(group.get("Title")),
        "status": status,
        "advertised": advertised,
        "approved": (buyable or {}).get("Approved"),
        "created_at": None,
        "updated_at": None,
        "published_at": None,
        "marketplace_id": variant_key or product_key,
        "url": None,
    }


def _mydeal_message(*, found: bool, advertised, status: str) -> str:
    if not found:
        return "Not found on MyDeal for this SKU."
    if advertised is True:
        return "Found on MyDeal and live."
    if (status or "").lower() == "pending":
        return (
            "Found on MyDeal but still pending WMP review — "
            "not live on the website yet."
        )
    return "Found on MyDeal in the seller catalog — not live on the website."


def _lookup_mydeal(store, sku: str) -> dict:
    from .mydeal.client import MyDealClient
    from .mydeal.products import listing_sku, parent_product_id

    listing = (
        StoreListing.objects.filter(store=store)
        .filter(Q(sku=sku) | Q(external_variant_key=sku) | Q(external_product_key=sku))
        .first()
    )
    parent_key = parent_product_id(listing) if listing else sku
    variant_key = listing_sku(listing) if listing else sku
    keys = []
    for key in (parent_key, sku, variant_key):
        text = _norm_id(key)
        if text and text not in keys:
            keys.append(text)

    client = MyDealClient(store)

    group = None
    for key in keys:
        result = client.get_product(key, by="sku")
        if result.ok:
            groups = _mydeal_groups(result.data)
            if groups:
                group = groups[0]
                break
            continue
        if _mydeal_is_not_found(result):
            continue
        raise MarketplaceError(result.message or "Could not search MyDeal for this SKU.")

    found = group is not None
    buyable = _mydeal_pick_buyable(group, [sku, variant_key, parent_key]) if group else None
    hit = _mydeal_hit(group, buyable, sku) if group else None
    advertised = hit.get("advertised") if hit else None
    status = (hit or {}).get("status") or ""
    return {
        "ok": True,
        "found": found,
        "advertised": advertised,
        "marketplace": "mydeal",
        "environment": client.environment,
        "query": {
            "sku": sku,
            "product_key": parent_key,
            "variant_key": variant_key,
        },
        "message": _mydeal_message(found=found, advertised=advertised, status=status),
        "results": [hit] if hit else [],
        "local_listing": _local_listing_summary(store, sku),
    }


def lookup_sku(store, sku: str) -> dict:
    """Search the store's marketplace for a SKU / variant key."""
    text = (sku or "").strip()
    if not text:
        raise MarketplaceError("SKU is required.")
    if getattr(store, "management_mode", None) != "full_store":
        raise MarketplaceError("Marketplace SKU check is only available for managed stores.")

    kind = marketplace_kind(store.marketplace)
    if kind == "lasoo":
        return _lookup_lasoo(store, text)
    if kind == "reverb":
        return _lookup_reverb(store, text)
    if kind == "bunnings":
        return _lookup_bunnings(store, text)
    if kind == "mydeal":
        return _lookup_mydeal(store, text)
    raise MarketplaceError(
        f'Marketplace SKU check is not supported for "{kind or "this marketplace"}" yet.'
    )


BULK_MAX_SKUS = 20  # per HTTP request — keeps lookups under gunicorn timeout
BULK_TOTAL_MAX_SKUS = 2000  # max SKUs accepted for parse / overall client-driven run


def parse_sku_list(raw) -> list[str]:
    """Normalize a list/string of SKUs (dedupe, preserve order)."""
    items: list[str] = []
    if isinstance(raw, str):
        # Split on newlines, commas, or semicolons.
        for part in raw.replace(";", "\n").replace(",", "\n").splitlines():
            text = part.strip()
            if text:
                items.append(text)
    elif isinstance(raw, (list, tuple)):
        for part in raw:
            text = str(part or "").strip()
            if text:
                items.append(text)
    seen: set[str] = set()
    out: list[str] = []
    for sku in items:
        key = sku.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(sku)
    return out


def store_listing_skus(store) -> list[str]:
    """SKUs for every managed listing on this store (for reconcile-all)."""
    pairs = StoreListing.objects.filter(store=store).order_by("sku").values_list(
        "sku", "external_variant_key",
    )
    return parse_sku_list([sku or variant for sku, variant in pairs])


def parse_skus_from_file(content: bytes, filename: str = "") -> list[str]:
    """Parse SKUs from .txt / .csv / .xlsx (SKU column or first column / one per line)."""
    import csv
    from io import BytesIO, StringIO

    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        import openpyxl

        wb = openpyxl.load_workbook(BytesIO(content), data_only=True, read_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []
        headers = [str(c or "").strip().lower() for c in rows[0]]
        sku_headers = {"sku", "variant key", "variant_key", "external variant key"}
        sku_idx = 0
        for i, h in enumerate(headers):
            if h in sku_headers:
                sku_idx = i
                break
        start = 1 if any(h in sku_headers for h in headers) else 0
        values = []
        for row in rows[start:]:
            if not row or sku_idx >= len(row):
                continue
            val = row[sku_idx]
            if val is None:
                continue
            text = str(val).strip()
            if text:
                values.append(text)
        return parse_sku_list(values)

    text = content.decode("utf-8-sig", errors="replace")
    lines = text.lstrip().splitlines()
    if lines and "," in lines[0]:
        rows = list(csv.reader(StringIO(text)))
        if not rows:
            return []
        headers = [str(c or "").strip().lower() for c in rows[0]]
        sku_headers = {"sku", "variant key", "variant_key"}
        if any(h in sku_headers for h in headers):
            idx = next(i for i, h in enumerate(headers) if h in sku_headers)
            values = [
                str(row[idx]).strip()
                for row in rows[1:]
                if idx < len(row) and str(row[idx]).strip()
            ]
            return parse_sku_list(values)
    return parse_sku_list(text)


def _row_from_lookup(sku: str, result: dict | None, error: str = "") -> dict:
    hit = None
    if result and isinstance(result.get("results"), list) and result["results"]:
        hit = result["results"][0]
    local = (result or {}).get("local_listing") if result else None
    found = bool(result and result.get("found"))
    advertised = None
    if hit and "advertised" in hit:
        advertised = hit.get("advertised")
    elif result:
        advertised = result.get("advertised")
    if advertised is True:
        advertised_label = "Yes"
    elif advertised is False:
        advertised_label = "No"
    else:
        advertised_label = ""
    mapping_errors = []
    if hit and hit.get("mapping_errors"):
        mapping_errors = hit.get("mapping_errors") or []
    elif result:
        mapping_errors = result.get("mapping_errors") or []
    return {
        "sku": sku,
        "found": "Yes" if found else "No",
        "advertised": advertised_label,
        "marketplace_status": (hit or {}).get("status") or "",
        "mapping_errors": "; ".join(mapping_errors) if mapping_errors else "",
        "created_at": (hit or {}).get("created_at") or (hit or {}).get("published_at") or "",
        "published_at": (hit or {}).get("published_at") or "",
        "title": (hit or {}).get("title") or "",
        "marketplace_id": (hit or {}).get("marketplace_id") or "",
        "url": (hit or {}).get("url") or "",
        "local_status": (local or {}).get("status") or "",
        "local_action": (local or {}).get("action") or "",
        "local_title": (local or {}).get("title") or "",
        "local_created_at": (local or {}).get("created_at") or "",
        "message": error or ((result or {}).get("message") or ""),
    }


def lookup_skus_bulk(store, skus: list[str]) -> dict:
    """Look up many SKUs; returns summary + per-SKU rows for CSV/UI."""
    if getattr(store, "management_mode", None) != "full_store":
        raise MarketplaceError("Marketplace SKU check is only available for managed stores.")
    cleaned = parse_sku_list(skus)
    if not cleaned:
        raise MarketplaceError("Provide at least one SKU.")
    if len(cleaned) > BULK_MAX_SKUS:
        raise MarketplaceError(
            f'Too many SKUs in one batch ({len(cleaned)}). '
            f'Maximum is {BULK_MAX_SKUS} per request — the UI sends batches automatically.'
        )

    kind = marketplace_kind(store.marketplace)
    if kind not in LOOKUP_KINDS:
        raise MarketplaceError(
            f'Marketplace SKU check is not supported for "{kind or "this marketplace"}" yet.'
        )

    rows = []
    found_count = 0
    error_count = 0
    marketplace = kind
    environment = ""
    for sku in cleaned:
        try:
            result = lookup_sku(store, sku)
            marketplace = result.get("marketplace") or marketplace
            environment = result.get("environment") or environment
            row = _row_from_lookup(sku, result)
            if result.get("found"):
                found_count += 1
            rows.append(row)
        except MarketplaceError as exc:
            error_count += 1
            rows.append(_row_from_lookup(sku, None, error=str(exc)))
            rows[-1]["found"] = "Error"
        except Exception as exc:  # noqa: BLE001
            logger.exception("Bulk marketplace lookup failed for sku=%s", sku)
            error_count += 1
            rows.append(_row_from_lookup(sku, None, error=str(exc)[:300]))
            rows[-1]["found"] = "Error"

    not_found = sum(1 for r in rows if r["found"] == "No")
    return {
        "ok": True,
        "marketplace": marketplace,
        "environment": environment,
        "total": len(rows),
        "found": found_count,
        "not_found": not_found,
        "errors": error_count,
        "rows": rows,
        "message": (
            f"Checked {len(rows)} SKU(s): {found_count} found, {not_found} not found"
            + (f", {error_count} error(s)" if error_count else "")
            + "."
        ),
    }


CSV_COLUMNS = [
    ("sku", "SKU"),
    ("found", "Found"),
    ("advertised", "Advertised / Live"),
    ("marketplace_status", "Marketplace Status"),
    ("mapping_errors", "Mapping Errors"),
    ("created_at", "Created At"),
    ("published_at", "Published At"),
    ("title", "Title"),
    ("marketplace_id", "Marketplace ID"),
    ("url", "URL"),
    ("local_status", "Hub Status"),
    ("local_action", "Hub Action"),
    ("local_title", "Hub Title"),
    ("local_created_at", "Hub Created At"),
    ("message", "Message"),
]


def build_lookup_csv(bulk_result: dict) -> bytes:
    """Serialize bulk lookup rows to CSV bytes."""
    import csv
    from io import StringIO

    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow([header for _, header in CSV_COLUMNS])
    for row in bulk_result.get("rows") or []:
        writer.writerow([row.get(key, "") for key, _ in CSV_COLUMNS])
    return buf.getvalue().encode("utf-8-sig")


def run_marketplace_lookup_job(store_id, skus: list[str]) -> dict:
    """Process a queued bulk lookup (Celery / background thread). Checks cancel between SKUs."""
    from stores.models import Store

    from . import marketplace_lookup_progress as prog

    try:
        store = Store.objects.select_related("marketplace", "user").get(pk=store_id)
    except Store.DoesNotExist:
        prog.finish_lookup_progress(
            store_id,
            status="error",
            message="Store not found.",
        )
        return {"ok": False, "error": "not_found"}

    cleaned = parse_sku_list(skus)
    prog.set_lookup_progress(
        store.id,
        active=True,
        status="running",
        message=f"Checking 0 of {len(cleaned)}…",
    )

    for sku in cleaned:
        if prog.is_lookup_cancel_requested(store.id):
            return prog.finish_lookup_progress(store.id, status="cancelled")
        try:
            result = lookup_sku(store, sku)
            row = _row_from_lookup(sku, result)
            prog.append_lookup_row(
                store.id,
                row,
                marketplace=result.get("marketplace") or "",
                environment=result.get("environment") or "",
            )
        except MarketplaceError as exc:
            row = _row_from_lookup(sku, None, error=str(exc))
            row["found"] = "Error"
            prog.append_lookup_row(store.id, row)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Marketplace lookup job failed for sku=%s store=%s", sku, store_id)
            row = _row_from_lookup(sku, None, error=str(exc)[:300])
            row["found"] = "Error"
            prog.append_lookup_row(store.id, row)

    if prog.is_lookup_cancel_requested(store.id):
        return prog.finish_lookup_progress(store.id, status="cancelled")
    return prog.finish_lookup_progress(store.id, status="done")


def start_marketplace_lookup_async(store, skus) -> dict:
    """Start a background marketplace SKU check (survives leaving the page).

    Uses a daemon thread (same pattern as managed listing scrape). Progress and
    result rows live in cache until the user starts a new check.
    """
    import threading

    from . import marketplace_lookup_progress as prog

    if getattr(store, "management_mode", None) != "full_store":
        raise MarketplaceError("Marketplace SKU check is only available for managed stores.")

    cleaned = parse_sku_list(skus)
    if not cleaned:
        raise MarketplaceError("Provide at least one SKU.")
    if len(cleaned) > BULK_TOTAL_MAX_SKUS:
        raise MarketplaceError(
            f"Too many SKUs ({len(cleaned)}). Maximum is {BULK_TOTAL_MAX_SKUS} per run."
        )

    kind = marketplace_kind(store.marketplace)
    if kind not in LOOKUP_KINDS:
        raise MarketplaceError(
            f'Marketplace SKU check is not supported for "{kind or "this marketplace"}" yet.'
        )

    existing = prog.get_lookup_progress(store.id)
    if existing.get("active"):
        return {
            "started": False,
            "already_running": True,
            "ok": True,
            "message": "A marketplace check is already running. You can leave this page — progress continues.",
            **prog.public_lookup_progress(store.id),
        }

    prog.begin_lookup_progress(
        store.id,
        skus=cleaned,
        message=f"Starting check for {len(cleaned)} SKU(s)…",
    )

    store_id = str(store.id)
    sku_list = list(cleaned)

    def _run():
        try:
            run_marketplace_lookup_job(store_id, sku_list)
        except Exception:  # noqa: BLE001
            logger.exception("Marketplace lookup thread failed store=%s", store_id)
            prog.finish_lookup_progress(
                store_id,
                status="error",
                message="Marketplace check failed unexpectedly.",
            )

    threading.Thread(
        target=_run,
        daemon=True,
        name=f"marketplace-lookup-{store_id}",
    ).start()

    # Progress and CSV results live in cache until the next run.

    return {
        "started": True,
        "async": True,
        "via": "thread",
        "ok": True,
        "total": len(sku_list),
        "processed": 0,
        "message": (
            f"Started check for {len(sku_list)} SKU(s). "
            "You can leave this page — results stay available until you start a new check."
        ),
        **prog.public_lookup_progress(store.id),
    }
