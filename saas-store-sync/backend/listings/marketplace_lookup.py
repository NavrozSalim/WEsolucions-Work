"""Live marketplace SKU lookup for managed stores (Lasoo + Reverb)."""
from __future__ import annotations

import logging

from django.db.models import Q

from store_adapters import get_adapter
from store_adapters.reverb_adapter import ReverbAPIError
from stores.credentials import marketplace_kind

from .errors import MarketplaceError
from .lasoo.client import LasooClient
from .lasoo.queries import build_payload
from .models import StoreListing

logger = logging.getLogger("listings")


def _collect_lasoo_rows(body) -> list:
    if not isinstance(body, dict):
        return []
    candidates = []
    results = body.get("results")
    if isinstance(results, dict):
        for key in ("variants", "items", "records", "data", "rows", "results"):
            val = results.get(key)
            if isinstance(val, list):
                candidates.append(val)
        for nest_key in ("body", "data"):
            nested = results.get(nest_key)
            if isinstance(nested, dict):
                for key in ("variants", "items", "records", "rows"):
                    val = nested.get(key)
                    if isinstance(val, list):
                        candidates.append(val)
            elif isinstance(nested, list):
                candidates.append(nested)
    elif isinstance(results, list):
        candidates.append(results)
    for key in ("variants", "items", "data", "records"):
        val = body.get(key)
        if isinstance(val, list):
            candidates.append(val)
    for rows in candidates:
        if rows:
            return rows
    return candidates[0] if candidates else []


def _first_str(row: dict, *keys: str) -> str:
    for key in keys:
        val = row.get(key)
        if val is None:
            continue
        text = str(val).strip()
        if text:
            return text
    return ""


def _normalize_lasoo_hit(row: dict) -> dict:
    product_key = _first_str(
        row,
        "externalProductKey",
        "ExternalProductKey",
        "productKey",
        "product_key",
    )
    variant_key = _first_str(
        row,
        "externalVariantKey",
        "ExternalVariantKey",
        "variantKey",
        "variant_key",
        "sku",
        "SKU",
    )
    status = _first_str(
        row,
        "status",
        "Status",
        "state",
        "State",
        "listingStatus",
        "publishStatus",
        "publishedStatus",
    )
    created_at = _first_str(
        row,
        "createdAt",
        "created_at",
        "CreatedAt",
        "dateCreated",
        "createdDate",
        "insertedAt",
    )
    updated_at = _first_str(
        row,
        "updatedAt",
        "updated_at",
        "UpdatedAt",
        "dateUpdated",
        "modifiedAt",
    )
    title = _first_str(row, "title", "Title", "name", "Name")
    return {
        "product_key": product_key,
        "variant_key": variant_key,
        "sku": variant_key or product_key,
        "title": title,
        "status": status or "found",
        "created_at": created_at or None,
        "updated_at": updated_at or None,
        "marketplace_id": _first_str(row, "id", "Id", "variantId", "VariantId") or None,
        "url": _first_str(row, "url", "Url", "webUrl", "permalink") or None,
    }


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
    payload = build_payload(
        "variants_search",
        data={
            "externalProductKey": product_key,
            "externalVariantKey": variant_key,
            "take": 25,
            "page": 1,
            "returnDataObject": False,
            "dataMappingErrors": False,
            "returnMappingInfo": False,
        },
        auth=client.auth_key,
    )
    result = client.send("variants_search", payload)
    if not result.ok:
        raise MarketplaceError(
            result.message or "Could not search Lasoo for this SKU."
        )

    rows = [r for r in _collect_lasoo_rows(result.data) if isinstance(r, dict)]
    matched = []
    for row in rows:
        hit = _normalize_lasoo_hit(row)
        if (
            hit["variant_key"] == variant_key
            or hit["product_key"] == product_key
            or hit["variant_key"] == sku
            or hit["product_key"] == sku
            or hit["sku"] == sku
        ):
            matched.append(hit)
    hits = matched or [_normalize_lasoo_hit(r) for r in rows]
    found = bool(hits)
    return {
        "ok": True,
        "found": found,
        "marketplace": "lasoo",
        "environment": client.environment,
        "query": {"sku": sku, "product_key": product_key, "variant_key": variant_key},
        "message": (
            "Found on Lasoo."
            if found
            else "Not found on Lasoo for this product/variant key."
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
    return {
        "sku": sku,
        "found": "Yes" if found else "No",
        "marketplace_status": (hit or {}).get("status") or "",
        "created_at": (hit or {}).get("created_at") or (hit or {}).get("published_at") or "",
        "published_at": (hit or {}).get("published_at") or "",
        "title": (hit or {}).get("title") or "",
        "marketplace_id": (hit or {}).get("marketplace_id") or "",
        "url": (hit or {}).get("url") or "",
        "local_status": (local or {}).get("status") or "",
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
    if kind not in ("lasoo", "reverb"):
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
    ("marketplace_status", "Marketplace Status"),
    ("created_at", "Created At"),
    ("published_at", "Published At"),
    ("title", "Title"),
    ("marketplace_id", "Marketplace ID"),
    ("url", "URL"),
    ("local_status", "Local Status"),
    ("local_created_at", "Local Created At"),
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
    if kind not in ("lasoo", "reverb"):
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
