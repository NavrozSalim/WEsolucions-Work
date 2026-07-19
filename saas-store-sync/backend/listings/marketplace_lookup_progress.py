"""Background marketplace SKU lookup progress (survives page leave / reload).

One job per store. Finished rows stay cached until the user starts a new run.
"""
from __future__ import annotations

from django.core.cache import cache
from django.utils import timezone

_TTL_ACTIVE = 60 * 60 * 6  # 6 hours while running
_TTL_DONE = 60 * 60 * 24 * 7  # keep results 7 days until replaced
_KEY = "listings:marketplace_lookup:{store_id}"


def _key(store_id) -> str:
    return _KEY.format(store_id=store_id)


def _empty() -> dict:
    return {
        "active": False,
        "cancel_requested": False,
        "status": "idle",  # idle | queued | running | done | cancelled | error
        "total": 0,
        "processed": 0,
        "found": 0,
        "not_found": 0,
        "errors": 0,
        "pct": 0,
        "current_sku": "",
        "message": "",
        "marketplace": "",
        "environment": "",
        "task_id": "",
        "rows": [],
        "started_at": None,
        "finished_at": None,
    }


def get_lookup_progress(store_id) -> dict:
    data = cache.get(_key(store_id))
    if not isinstance(data, dict):
        return _empty()
    # Ensure required keys exist for older cache entries.
    base = _empty()
    base.update(data)
    if not isinstance(base.get("rows"), list):
        base["rows"] = []
    return base


def set_lookup_progress(store_id, **fields) -> dict:
    cur = get_lookup_progress(store_id)
    # Preserve rows unless explicitly replaced.
    prev_rows = cur.get("rows") if isinstance(cur.get("rows"), list) else []
    cur.update(fields)
    if "rows" not in fields:
        cur["rows"] = prev_rows
    total = int(cur.get("total") or 0)
    processed = int(cur.get("processed") or 0)
    if total > 0:
        cur["pct"] = max(0, min(100, round(100.0 * processed / total)))
    else:
        cur["pct"] = 0
    active = bool(cur.get("active"))
    ttl = _TTL_ACTIVE if active else _TTL_DONE
    cache.set(_key(store_id), cur, ttl)
    return cur


def clear_lookup_progress(store_id) -> None:
    cache.delete(_key(store_id))


def begin_lookup_progress(store_id, *, skus: list[str], message: str = "") -> dict:
    total = len(skus)
    return set_lookup_progress(
        store_id,
        active=True,
        cancel_requested=False,
        status="queued",
        total=total,
        processed=0,
        found=0,
        not_found=0,
        errors=0,
        pct=0,
        current_sku="",
        message=message or f"Queued {total} SKU(s)…",
        marketplace="",
        environment="",
        task_id="",
        rows=[],
        started_at=timezone.now().isoformat(),
        finished_at=None,
    )


def request_lookup_cancel(store_id) -> dict:
    cur = get_lookup_progress(store_id)
    if not cur.get("active"):
        return cur
    return set_lookup_progress(
        store_id,
        cancel_requested=True,
        message="Cancel requested… finishing current SKU.",
    )


def is_lookup_cancel_requested(store_id) -> bool:
    return bool(get_lookup_progress(store_id).get("cancel_requested"))


def append_lookup_row(store_id, row: dict, *, marketplace: str = "", environment: str = "") -> dict:
    cur = get_lookup_progress(store_id)
    rows = list(cur.get("rows") or [])
    rows.append(row)
    found = int(cur.get("found") or 0)
    not_found = int(cur.get("not_found") or 0)
    errors = int(cur.get("errors") or 0)
    flag = row.get("found")
    if flag == "Yes":
        found += 1
    elif flag == "Error":
        errors += 1
    else:
        not_found += 1
    processed = len(rows)
    total = int(cur.get("total") or 0) or processed
    return set_lookup_progress(
        store_id,
        active=True,
        status="running",
        rows=rows,
        processed=processed,
        found=found,
        not_found=not_found,
        errors=errors,
        current_sku=str(row.get("sku") or ""),
        marketplace=marketplace or cur.get("marketplace") or "",
        environment=environment or cur.get("environment") or "",
        message=f"Checking {processed} of {total}…",
    )


def finish_lookup_progress(
    store_id,
    *,
    status: str = "done",
    message: str = "",
) -> dict:
    cur = get_lookup_progress(store_id)
    total = int(cur.get("total") or 0)
    processed = int(cur.get("processed") or len(cur.get("rows") or []))
    found = int(cur.get("found") or 0)
    not_found = int(cur.get("not_found") or 0)
    errors = int(cur.get("errors") or 0)
    if not message:
        if status == "cancelled":
            message = (
                f"Cancelled after {processed} of {total} SKU(s): "
                f"{found} found, {not_found} not found"
                + (f", {errors} error(s)" if errors else "")
                + ". Partial results are ready to download."
            )
        elif status == "error":
            message = cur.get("message") or "Marketplace check failed."
        else:
            message = (
                f"Checked {processed} SKU(s): {found} found, {not_found} not found"
                + (f", {errors} error(s)" if errors else "")
                + "."
            )
    return set_lookup_progress(
        store_id,
        active=False,
        cancel_requested=False,
        status=status,
        processed=processed,
        pct=100 if total and processed >= total and status == "done" else (
            max(0, min(100, round(100.0 * processed / total))) if total else 0
        ),
        current_sku="",
        message=message,
        finished_at=timezone.now().isoformat(),
    )


def public_lookup_progress(store_id) -> dict:
    """Progress payload for the UI (omit full rows to keep polls light)."""
    cur = get_lookup_progress(store_id)
    return {
        "active": bool(cur.get("active")),
        "cancel_requested": bool(cur.get("cancel_requested")),
        "status": cur.get("status") or "idle",
        "total": int(cur.get("total") or 0),
        "processed": int(cur.get("processed") or 0),
        "found": int(cur.get("found") or 0),
        "not_found": int(cur.get("not_found") or 0),
        "errors": int(cur.get("errors") or 0),
        "pct": int(cur.get("pct") or 0),
        "current_sku": cur.get("current_sku") or "",
        "message": cur.get("message") or "",
        "marketplace": cur.get("marketplace") or "",
        "environment": cur.get("environment") or "",
        "has_results": bool(cur.get("rows")),
        "started_at": cur.get("started_at"),
        "finished_at": cur.get("finished_at"),
        "store_id": str(store_id),
    }
