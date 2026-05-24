from __future__ import annotations

from typing import Any, Optional

import requests

from .config import API_BASE_URL, INGEST_BATCH_SIZE, INGEST_TOKEN


class IngestClient:
    def __init__(self, base_url: str = API_BASE_URL, token: str = INGEST_TOKEN) -> None:
        if not base_url:
            raise ValueError("API_BASE_URL is required")
        if not token:
            raise ValueError("INGEST_TOKEN is required")
        self.base = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    def next_job(self) -> Optional[dict[str, Any]]:
        r = self.session.get(f"{self.base}/ingest/heb/next-job/", timeout=60)
        r.raise_for_status()
        data = r.json()
        if not data.get("job_id"):
            return None
        return data

    def job_status(self, job_id: str) -> dict[str, Any]:
        r = self.session.get(f"{self.base}/ingest/heb/jobs/{job_id}/", timeout=30)
        r.raise_for_status()
        return r.json()

    def post_items(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        if not items:
            return {"stats": {}, "results": []}
        r = self.session.post(
            f"{self.base}/ingest/heb/",
            json={"items": items},
            timeout=120,
        )
        r.raise_for_status()
        return r.json()

    def post_items_batched(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        totals = {"received": 0, "matched": 0, "applied": 0, "skipped": 0, "errors": 0}
        for i in range(0, len(items), INGEST_BATCH_SIZE):
            chunk = items[i : i + INGEST_BATCH_SIZE]
            resp = self.post_items(chunk)
            stats = resp.get("stats") or {}
            for k in totals:
                totals[k] += int(stats.get(k) or 0)
        return {"stats": totals}

    def complete_job(
        self,
        job_id: str,
        *,
        status: str = "done",
        stats: Optional[dict] = None,
        note: str = "",
    ) -> dict[str, Any]:
        r = self.session.post(
            f"{self.base}/ingest/heb/jobs/{job_id}/complete/",
            json={"status": status, "stats": stats or {}, "note": note},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def fetch_urls(self, store_id: Optional[str] = None) -> list[str]:
        params = {}
        if store_id:
            params["store_id"] = store_id
        r = self.session.get(f"{self.base}/ingest/heb/urls/", params=params, timeout=60)
        r.raise_for_status()
        return list(r.json().get("urls") or [])
