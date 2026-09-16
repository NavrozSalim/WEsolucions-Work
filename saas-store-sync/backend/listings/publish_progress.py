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
_KEY = "listings:publish_progress:{store_id}"


def _sid(store_id) -> str:
    return str(store_id)


def _key(store_id) -> str:
    return _KEY.format(store_id=_sid(store_id))


def _empty_progress() -> dict:
    return {
        "active": False,
        "job_id": "",
        "queued": 0,
        "message": "",
        "started_at": "",
        "error": "",
        "result": None,
    }


def get_publish_progress(store_id) -> dict:
    data = cache.get(_key(store_id))
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
    return out


def begin_publish_progress(store_id, *, job_id, queued, message="") -> dict:
    data = {
        "active": True,
        "job_id": str(job_id or ""),
        "queued": int(queued or 0),
        "message": message or "",
        "started_at": timezone.now().isoformat(),
        "error": "",
        "result": None,
    }
    cache.set(_key(store_id), data, _TTL)
    return data


def finish_publish_progress(store_id, **fields) -> dict:
    cur = get_publish_progress(store_id)
    if fields.get("job_id") and cur.get("job_id") and str(fields["job_id"]) != str(cur["job_id"]):
        return cur
    cur.update(fields)
    cur["active"] = False
    cache.set(_key(store_id), cur, _DONE_TTL)
    return cur


def clear_publish_progress(store_id) -> None:
    cache.delete(_key(store_id))


def enrich_publish_progress(store_id) -> dict:
    """Mark the banner idle if the Celery job already finished."""
    data = get_publish_progress(store_id)
    if not data.get("active"):
        return data
    job_id = (data.get("job_id") or "").strip()
    if not job_id:
        return finish_publish_progress(store_id, message="Publish job missing.")
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
            job_id=job_id,
            message=message,
            error=err,
            result=slim,
        )
    except Exception:
        logger.debug("Publish progress celery check failed store=%s", store_id, exc_info=True)
        return data
