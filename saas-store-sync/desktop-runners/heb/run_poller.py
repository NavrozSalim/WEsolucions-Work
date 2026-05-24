#!/usr/bin/env python3
"""HEB desktop runner — poll SaaS for scrape jobs, scrape with Chrome + cookies, upload prices."""

from __future__ import annotations

import argparse
import sys

from heb_runner.api_client import IngestClient
from heb_runner.config import COOKIES_FILE
from heb_runner.poller import poll_forever, run_job, scrape_urls_once


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
    args = parser.parse_args()

    if args.urls:
        results = scrape_urls_once(args.urls)
        if args.upload:
            client = IngestClient()
            stats = client.post_items_batched(results).get("stats") or {}
            print("Upload stats:", stats)
        return 0

    if args.once:
        client = IngestClient()
        job = client.next_job()
        if not job:
            print("No pending job")
            return 0
        run_job(client, job)
        return 0

    print(f"Using cookies file: {COOKIES_FILE}")
    poll_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
