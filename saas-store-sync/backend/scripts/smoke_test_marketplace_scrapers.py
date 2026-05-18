#!/usr/bin/env python
"""Smoke-test Amazon/eBay US+AU scrapers (run from backend/: python scripts/smoke_test_marketplace_scrapers.py)."""
from __future__ import annotations

import json
import sys
import time

sys.path.insert(0, ".")

from scrapers import close_amazon_session, get_price_and_stock

CASES = [
    {
        "label": "Amazon US",
        "url": "https://www.amazon.com/dp/B08QF7YH9G",
        "region": "USA",
    },
    {
        "label": "Amazon AU",
        "url": "https://www.amazon.com.au/dp/B08QF7YH9G",
        "region": "AU",
    },
    {
        "label": "eBay US",
        "url": "https://www.ebay.com/itm/182391181967",
        "region": "USA",
    },
    {
        "label": "eBay AU",
        "url": "https://www.ebay.com.au/itm/182391181967",
        "region": "AU",
    },
]


def main() -> int:
    results = []
    failed = 0

    for case in CASES:
        label = case["label"]
        url = case["url"]
        region = case["region"]
        session = {}
        t0 = time.perf_counter()
        print(f"\n{'=' * 60}\n{label}\nURL: {url}\nRegion: {region}\n", flush=True)
        try:
            out = get_price_and_stock(url, region, session)
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            row = {"label": label, "ok": False, "elapsed_sec": round(elapsed, 1), "error": str(exc)}
            results.append(row)
            failed += 1
            print(f"EXCEPTION: {exc}\n", flush=True)
        else:
            elapsed = time.perf_counter() - t0
            price = out.get("price")
            stock = out.get("inventory")
            if stock is None:
                stock = out.get("stock")
            ok = price is not None
            row = {
                "label": label,
                "ok": ok,
                "elapsed_sec": round(elapsed, 1),
                "price": price,
                "stock": stock,
                "title": (out.get("title") or "")[:100] or None,
                "error_code": out.get("error_code"),
                "error_message": out.get("error_message"),
            }
            results.append(row)
            if not ok:
                failed += 1
            print(json.dumps(row, indent=2, default=str), flush=True)
        finally:
            close_amazon_session(session)

    print(f"\n{'=' * 60}\nSUMMARY\n", flush=True)
    for r in results:
        status = "PASS" if r.get("ok") else "FAIL"
        extra = ""
        if r.get("price") is not None:
            extra = f" price={r['price']} stock={r.get('stock')}"
        elif r.get("error_code"):
            extra = f" ({r['error_code']})"
        elif r.get("error"):
            extra = f" ({r['error'][:80]})"
        print(f"  [{status}] {r['label']} — {r.get('elapsed_sec')}s{extra}", flush=True)

    print(f"\n{len(results) - failed}/{len(results)} returned a price.\n", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
