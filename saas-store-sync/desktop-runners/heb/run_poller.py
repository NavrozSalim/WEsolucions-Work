#!/usr/bin/env python3
"""HEB desktop runner — poll SaaS for scrape jobs, scrape with Chrome + cookies, upload prices."""

from __future__ import annotations

import argparse
import sys
import time
import traceback

from heb_runner.api_client import IngestClient
from heb_runner.config import (
    COOKIES_FILE,
    POLLER_CHUNK_SIZE,
    POLLER_UPLOAD_EVERY,
    POLLER_WORKERS,
    POLL_INTERVAL_SEC,
)
from heb_runner.poller import _log, poll_forever, poll_next_job_safe, run_job, scrape_urls_once


def main() -> int:
    parser = argparse.ArgumentParser(description="HEB desktop runner (cookies + ingest API)")
    parser.add_argument(
        "--url",
        action="append",
        dest="urls",
        help="Scrape one URL locally (no API job). Can repeat.",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="With --url, POST results to ingest API after scraping",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Poll once for a job, run it, then exit",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help=f"Seconds between polls when idle (default {POLL_INTERVAL_SEC}).",
    )
    parser.add_argument(
        "--workers",
        "--max-folders",
        type=int,
        dest="workers",
        default=None,
        help=f"Parallel Chrome worker slots (default {POLLER_WORKERS}).",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=None,
        help=(
            f"URLs per chunk (default {POLLER_CHUNK_SIZE}). "
            "0 = legacy round-robin split across workers."
        ),
    )
    parser.add_argument(
        "--upload-every",
        type=int,
        default=None,
        help=f"Seconds between mid-run API uploads (default {POLLER_UPLOAD_EVERY}).",
    )
    args = parser.parse_args()

    if args.urls:
        results = scrape_urls_once(args.urls)
        if args.upload:
            client = IngestClient()
            stats = client.post_items_batched(results).get("stats") or {}
            print("Upload stats:", stats)
        return 0

    poll_kwargs = {
        "workers": args.workers,
        "chunk_size": args.chunk_size,
        "upload_every": args.upload_every,
        "interval": args.interval,
        "once": args.once,
    }

    if args.once:
        client = IngestClient()
        job = poll_next_job_safe(client)
        if not job:
            _log("no pending job")
            return 0
        run_job(
            client,
            job,
            workers=args.workers,
            chunk_size=args.chunk_size,
            upload_every=args.upload_every,
        )
        return 0

    _log(f"Using cookies file: {COOKIES_FILE}")
    poll_forever(**poll_kwargs)
    return 0


def _run_forever() -> int:
    restart_backoff = 30
    while True:
        try:
            return main()
        except KeyboardInterrupt:
            _log("interrupted — bye")
            return 130
        except SystemExit as exc:
            return int(exc.code) if exc.code is not None else 0
        except Exception as exc:
            _log(f"FATAL: {exc!r} — restarting in {restart_backoff}s")
            traceback.print_exc()
            try:
                time.sleep(restart_backoff)
            except KeyboardInterrupt:
                return 130


if __name__ == "__main__":
    sys.exit(_run_forever())
