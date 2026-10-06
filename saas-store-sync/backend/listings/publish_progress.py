"""In-flight Created-products publish progress for the UI banner.

Celery MyDeal publish can take minutes. The banner must survive reload and
leaving Created products — same idea as listings scrape progress.
"""
from __future__ import annotations

import logging

from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

_TTL = 6 * 60 * 60  # 6 hours — matches frontend publish poll ceiling
_DONE_TTL = 10 * 60


def _sid(store_id) -> str:
    return str(store_id)


def _key(store_id, scope: str = "publish") -> str:
    # scope "publish" keeps the original cache key. "inventory_push" is separate
    # so a Manual sync banner does not clear a Created-products publish.
    return f"listings:{scope}_progress:{_sid(store_id)}"


def _empty_progress() -> dict:
    return {
        "active": False,
        "job_id": "",
        "queued": 0,
        "processed": 0,
        "failed": 0,
        "message": "",
        "started_at": "",
        "error": "",
        "result": None,
    }


def get_publish_progress(store_id, *, scope: str = "publish") -> dict:
    data = cache.get(_key(store_id, scope))
    if not isinstance(data, dict):
        return _empty_progress()
    out = _empty_progress()
    out.update(data)
    out["active"] = bool(out.get("active"))
    try:
        out["queued"] = int(out.get("queued") or 0)
    except (TypeError, ValueError):
        out["queued"] = 0
    out["job_id"] = str(out.get("job_id") or "")
    out["message"] = str(out.get("message") or "")
    out["error"] = str(out.get("error") or "")
    for count_key in ("processed", "failed"):
        try:
            out[count_key] = int(out.get(count_key) or 0)
        except (TypeError, ValueError):
            out[count_key] = 0
    return out


def begin_publish_progress(store_id, *, job_id, queued, message="", scope: str = "publish") -> dict:
    data = {
        "active": True,
        "job_id": str(job_id or ""),
        "queued": int(queued or 0),
        "processed": 0,
        "failed": 0,
        "message": message or "",
        "started_at": timezone.now().isoformat(),
        "error": "",
        "result": None,
    }
    cache.set(_key(store_id, scope), data, _TTL)
    return data


def tick_publish_progress(store_id, *, scope: str = "publish", **fields) -> dict:
    """Update an in-flight publish banner (chunk progress). Ignores idle jobs."""
    cur = get_publish_progress(store_id, scope=scope)
    if not cur.get("active"):
        return cur
    if fields.get("job_id") and cur.get("job_id") and str(fields["job_id"]) != str(cur["job_id"]):
        return cur
    cur.update(fields)
    cur["active"] = True
    cache.set(_key(store_id, scope), cur, _TTL)
    return cur


def finish_publish_progress(store_id, *, scope: str = "publish", **fields) -> dict:
    cur = get_publish_progress(store_id, scope=scope)
    if fields.get("job_id") and cur.get("job_id") and str(fields["job_id"]) != str(cur["job_id"]):
        return cur
    cur.update(fields)
    cur["active"] = False
    cache.set(_key(store_id, scope), cur, _DONE_TTL)
    return cur


def clear_publish_progress(store_id, *, scope: str = "publish") -> None:
    cache.delete(_key(store_id, scope))


def enrich_publish_progress(store_id, *, scope: str = "publish") -> dict:
    """Mark the banner idle if the Celery job already finished."""
    data = get_publish_progress(store_id, scope=scope)
    if not data.get("active"):
        return data
    job_id = (data.get("job_id") or "").strip()
    if not job_id:
        return finish_publish_progress(store_id, scope=scope, message="Publish job missing.")
    try:
        from celery.result import AsyncResult

        result = AsyncResult(job_id)
        if not result.ready():
            return data
        payload = result.result if result.successful() and isinstance(result.result, dict) else None
        err = ""
        if not result.successful():
            err = str(result.result) if result.result else "Publish failed"
        message = ""
        if payload:
            message = str(payload.get("message") or "")
        elif err:
            message = err
        slim = None
        if payload:
            slim = {
                "ok": payload.get("ok"),
                "published": payload.get("published") or payload.get("uploaded") or 0,
                "uploaded": payload.get("uploaded") or payload.get("published") or 0,
                "failed": payload.get("failed") or 0,
                "message": payload.get("message") or message,
            }
        return finish_publish_progress(
            store_id,
            scope=scope,
            job_id=job_id,
            message=message,
            error=err,
            result=slim,
        )
    except Exception:
        logger.debug("Publish progress celery check failed store=%s", store_id, exc_info=True)
        return data
