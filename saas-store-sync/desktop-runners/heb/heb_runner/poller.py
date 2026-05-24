from __future__ import annotations

import time
import traceback
from typing import Any

from .api_client import IngestClient
from .config import MIN_GAP_SEC, POLL_INTERVAL_SEC
from .scraper import HebBrowserSession


def run_job(client: IngestClient, job: dict[str, Any]) -> dict[str, Any]:
    job_id = job["job_id"]
    urls = list(job.get("urls") or [])
    print(f"Claimed job {job_id} — {len(urls)} URL(s)")

    if not urls:
        client.complete_job(job_id, status="done", note="no urls")
        return {"received": 0, "applied": 0}

    session = HebBrowserSession()
    items: list[dict[str, Any]] = []
    ok = 0
    fail = 0

    try:
        session.start()
        for i, url in enumerate(urls, start=1):
            if client.job_status(job_id).get("cancelled"):
                print("Job cancelled — stopping")
                client.complete_job(job_id, status="failed", note="cancelled")
                return {"received": len(items), "applied": ok, "cancelled": True}

            print(f"[{i}/{len(urls)}] {url[:90]}")
            result = session.scrape(url)
            items.append(result)
            if result.get("price") is not None:
                ok += 1
                print(f"  OK price={result['price']} stock={result.get('stock')} title={str(result.get('title') or '')[:40]}")
            else:
                fail += 1
                print(f"  FAIL {result.get('error_code')}")

            if i < len(urls) and MIN_GAP_SEC > 0:
                time.sleep(MIN_GAP_SEC)
    except Exception as exc:
        print(f"Job failed: {exc}")
        traceback.print_exc()
        client.complete_job(job_id, status="failed", note=str(exc)[:300])
        raise
    finally:
        session.close()

    ingest_stats = client.post_items_batched(items).get("stats") or {}
    client.complete_job(
        job_id,
        status="done",
        stats=ingest_stats,
        note=f"scraped ok={ok} fail={fail}",
    )
    print(f"Job {job_id} done — applied={ingest_stats.get('applied', 0)} matched={ingest_stats.get('matched', 0)}")
    return {"ok": ok, "fail": fail, **ingest_stats}


def poll_forever() -> None:
    client = IngestClient()
    print(f"Polling every {POLL_INTERVAL_SEC}s for HEB jobs ({client.base})")
    while True:
        try:
            job = client.next_job()
            if job:
                run_job(client, job)
            else:
                print("No pending job — sleeping")
        except KeyboardInterrupt:
            print("Stopped.")
            return
        except Exception as exc:
            print(f"Poll error: {exc}")
            traceback.print_exc()
        time.sleep(POLL_INTERVAL_SEC)


def scrape_urls_once(urls: list[str]) -> list[dict[str, Any]]:
    session = HebBrowserSession()
    out: list[dict[str, Any]] = []
    try:
        session.start()
        for i, url in enumerate(urls, start=1):
            print(f"[{i}/{len(urls)}] {url}")
            result = session.scrape(url)
            out.append(result)
            print(result)
            if i < len(urls) and MIN_GAP_SEC > 0:
                time.sleep(MIN_GAP_SEC)
    finally:
        session.close()
    return out
