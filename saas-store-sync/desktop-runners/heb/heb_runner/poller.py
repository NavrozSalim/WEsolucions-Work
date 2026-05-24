from __future__ import annotations

import time
import traceback
from concurrent.futures import Future, ProcessPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime
from typing import Any

from .api_client import IngestClient
from .config import (
    POLLER_CANCEL_CHECK_EVERY,
    POLLER_CHUNK_SIZE,
    POLLER_UPLOAD_EVERY,
    POLLER_WORKERS,
    POLL_INTERVAL_SEC,
)
from .scraper import HebBrowserSession
from .worker import scrape_chunk


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [poller] {msg}", flush=True)


def _chunk_urls(urls: list[str], chunk_size: int) -> list[list[str]]:
    if chunk_size <= 0:
        return [list(urls)]
    return [urls[i : i + chunk_size] for i in range(0, len(urls), chunk_size)]


def _legacy_splits(urls: list[str], workers: int) -> list[list[str]]:
    """Round-robin split across ``workers`` slots (POLLER_CHUNK_SIZE=0)."""
    buckets: list[list[str]] = [[] for _ in range(workers)]
    for i, url in enumerate(urls):
        buckets[i % workers].append(url)
    return [b for b in buckets if b]


def _upload_new_items(
    client: IngestClient,
    all_items: list[dict[str, Any]],
    last_uploaded: int,
) -> tuple[dict[str, Any], int]:
    new_items = all_items[last_uploaded:]
    if not new_items:
        return {}, last_uploaded
    stats = client.post_items_batched(new_items).get("stats") or {}
    return stats, len(all_items)


def _count_ok_fail(items: list[dict[str, Any]]) -> tuple[int, int]:
    ok = sum(1 for it in items if it.get("price") is not None)
    return ok, len(items) - ok


def _run_chunk_pool(
    client: IngestClient,
    job_id: str,
    urls: list[str],
    *,
    workers: int,
    chunk_size: int,
    upload_every: int,
    cancel_check_every: int,
) -> dict[str, Any]:
    if chunk_size <= 0:
        chunks = _legacy_splits(urls, workers)
        mode = "legacy-split"
    else:
        chunks = _chunk_urls(urls, chunk_size)
        mode = "chunk-pool"

    total_chunks = len(chunks)
    _log(
        f"job {job_id}: {len(urls)} URL(s) -> {total_chunks} chunk(s), "
        f"{workers} worker slot(s), mode={mode}"
    )

    all_items: list[dict[str, Any]] = []
    last_uploaded = 0
    last_stats: dict[str, Any] = {}
    last_upload = time.time()
    last_cancel_check = 0.0
    completed_chunks = 0
    remote_cancelled = False

    pending: dict[Future, int] = {}
    queue = list(chunks)

    executor = ProcessPoolExecutor(max_workers=workers)
    try:
        def _submit(slot_idx: int, chunk: list[str]) -> None:
            fut = executor.submit(scrape_chunk, chunk)
            pending[fut] = slot_idx

        for i in range(min(workers, len(queue))):
            _submit(i, queue.pop(0))

        while pending:
            now = time.time()

            if cancel_check_every > 0 and now - last_cancel_check >= cancel_check_every:
                last_cancel_check = now
                try:
                    if client.job_status(job_id).get("cancelled"):
                        _log("job cancelled in app — stopping workers")
                        remote_cancelled = True
                        queue.clear()
                        for fut in list(pending):
                            fut.cancel()
                        pending.clear()
                        break
                except Exception as exc:
                    _log(f"cancel-check error: {exc}")

            if now - last_upload >= upload_every:
                _log("mid-run upload...")
                stats, last_uploaded = _upload_new_items(client, all_items, last_uploaded)
                if stats:
                    last_stats = stats
                    _log(f"  stats: {stats}")
                last_upload = now

            done, _ = wait(pending.keys(), timeout=5, return_when=FIRST_COMPLETED)

            for fut in done:
                slot = pending.pop(fut)
                try:
                    chunk_items = fut.result()
                except Exception as exc:
                    _log(f"  slot {slot + 1:02d} chunk failed: {exc!r}")
                    chunk_items = []
                all_items.extend(chunk_items)
                completed_chunks += 1
                ok, fail = _count_ok_fail(chunk_items)
                _log(
                    f"  slot {slot + 1:02d} chunk done "
                    f"({completed_chunks}/{total_chunks}, ok={ok} fail={fail})"
                )
                if queue:
                    _submit(slot, queue.pop(0))
    finally:
        if remote_cancelled:
            executor.shutdown(wait=False, cancel_futures=True)
        else:
            executor.shutdown(wait=True)

    if remote_cancelled:
        _log("cancelled — final partial upload...")
        stats, last_uploaded = _upload_new_items(client, all_items, last_uploaded)
        if stats:
            last_stats = stats
        _log(f"job {job_id} left cancelled on server (no /complete/)")
        return {"cancelled": True, **last_stats}

    _log(f"all slots idle — final upload ({completed_chunks}/{total_chunks} chunks)")
    stats, _ = _upload_new_items(client, all_items, last_uploaded)
    if stats:
        last_stats = stats

    ok, fail = _count_ok_fail(all_items)
    client.complete_job(
        job_id,
        status="done",
        stats=last_stats,
        note=f"chunk-pool: {completed_chunks}/{total_chunks} chunks, ok={ok} fail={fail}",
    )
    _log(
        f"job {job_id} done — applied={last_stats.get('applied', 0)} "
        f"matched={last_stats.get('matched', 0)}"
    )
    return {"ok": ok, "fail": fail, **last_stats}


def _run_sequential(
    client: IngestClient,
    job_id: str,
    urls: list[str],
    *,
    upload_every: int,
    cancel_check_every: int,
) -> dict[str, Any]:
    """Single Chrome session (workers=1 and chunk_size covers all URLs)."""
    all_items: list[dict[str, Any]] = []
    last_uploaded = 0
    last_stats: dict[str, Any] = {}
    last_upload = time.time()
    last_cancel_check = 0.0
    ok = fail = 0

    session = HebBrowserSession()
    try:
        session.start()
        for i, url in enumerate(urls, start=1):
            now = time.time()
            if cancel_check_every > 0 and now - last_cancel_check >= cancel_check_every:
                last_cancel_check = now
                if client.job_status(job_id).get("cancelled"):
                    _log("job cancelled — stopping")
                    stats, _ = _upload_new_items(client, all_items, last_uploaded)
                    if stats:
                        last_stats = stats
                    return {"cancelled": True, **last_stats}

            if now - last_upload >= upload_every:
                _log("mid-run upload...")
                stats, last_uploaded = _upload_new_items(client, all_items, last_uploaded)
                if stats:
                    last_stats = stats
                last_upload = now

            _log(f"[{i}/{len(urls)}] {url[:90]}")
            result = session.scrape(url)
            all_items.append(result)
            if result.get("price") is not None:
                ok += 1
                _log(
                    f"  OK price={result['price']} stock={result.get('stock')} "
                    f"title={str(result.get('title') or '')[:40]}"
                )
            else:
                fail += 1
                _log(f"  FAIL {result.get('error_code')}")

            from .config import MIN_GAP_SEC

            if i < len(urls) and MIN_GAP_SEC > 0:
                time.sleep(MIN_GAP_SEC)
    finally:
        session.close()

    stats, _ = _upload_new_items(client, all_items, last_uploaded)
    if stats:
        last_stats = stats
    client.complete_job(
        job_id,
        status="done",
        stats=last_stats,
        note=f"sequential ok={ok} fail={fail}",
    )
    return {"ok": ok, "fail": fail, **last_stats}


def run_job(
    client: IngestClient,
    job: dict[str, Any],
    *,
    workers: int | None = None,
    chunk_size: int | None = None,
    upload_every: int | None = None,
    cancel_check_every: int | None = None,
) -> dict[str, Any]:
    workers = workers if workers is not None else POLLER_WORKERS
    chunk_size = chunk_size if chunk_size is not None else POLLER_CHUNK_SIZE
    upload_every = upload_every if upload_every is not None else POLLER_UPLOAD_EVERY
    cancel_check_every = (
        cancel_check_every if cancel_check_every is not None else POLLER_CANCEL_CHECK_EVERY
    )

    job_id = job["job_id"]
    urls = list(job.get("urls") or [])
    _log(f"claimed job {job_id} — {len(urls)} URL(s), store={job.get('store_id') or '*'}")

    if not urls:
        client.complete_job(job_id, status="done", stats={"received": 0}, note="no urls")
        return {"received": 0, "applied": 0}

    try:
        if workers <= 1:
            return _run_sequential(
                client,
                job_id,
                urls,
                upload_every=upload_every,
                cancel_check_every=cancel_check_every,
            )
        return _run_chunk_pool(
            client,
            job_id,
            urls,
            workers=workers,
            chunk_size=chunk_size,
            upload_every=upload_every,
            cancel_check_every=cancel_check_every,
        )
    except Exception as exc:
        _log(f"job handler crashed: {exc!r}")
        traceback.print_exc()
        client.complete_job(job_id, status="failed", note=str(exc)[:300])
        raise


def poll_next_job_safe(client: IngestClient) -> dict[str, Any] | None:
    try:
        return client.next_job()
    except Exception as exc:
        _log(f"poll error: {exc}")
        return None


def poll_forever(
    *,
    workers: int | None = None,
    chunk_size: int | None = None,
    upload_every: int | None = None,
    cancel_check_every: int | None = None,
    interval: int | None = None,
    once: bool = False,
) -> None:
    workers = workers if workers is not None else POLLER_WORKERS
    chunk_size = chunk_size if chunk_size is not None else POLLER_CHUNK_SIZE
    upload_every = upload_every if upload_every is not None else POLLER_UPLOAD_EVERY
    cancel_check_every = (
        cancel_check_every if cancel_check_every is not None else POLLER_CANCEL_CHECK_EVERY
    )
    interval = interval if interval is not None else POLL_INTERVAL_SEC

    client = IngestClient()
    _log(
        f"polling {client.base} every {interval}s "
        f"(workers={workers}, chunk_size={chunk_size}, upload_every={upload_every}s, "
        f"cancel_check={cancel_check_every}s)"
    )

    idle_polls = 0
    heartbeat_every = 20

    while True:
        job = poll_next_job_safe(client)
        if job is None:
            if once:
                _log("--once and no pending job — exiting")
                return
            idle_polls += 1
            if idle_polls % heartbeat_every == 0:
                _log(f"still alive — {idle_polls} idle poll(s), no job pending")
            time.sleep(interval)
            continue

        idle_polls = 0
        try:
            run_job(
                client,
                job,
                workers=workers,
                chunk_size=chunk_size,
                upload_every=upload_every,
                cancel_check_every=cancel_check_every,
            )
        except KeyboardInterrupt:
            _log("interrupted during job — exiting")
            return
        except Exception as exc:
            _log(f"job failed: {exc!r}")

        if once:
            return


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
            from .config import MIN_GAP_SEC

            if i < len(urls) and MIN_GAP_SEC > 0:
                time.sleep(MIN_GAP_SEC)
    finally:
        session.close()
    return out
