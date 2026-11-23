"""Check which scraper proxies actually work right now.

Run it inside the scraper worker, so it reads the same proxy lists the
scraper uses:

    docker compose -f docker-compose.au-scraper.prod.yml exec -T celery_worker_au python - au < scripts/check_proxies.py
    docker compose -f docker-compose.us-scraper.prod.yml exec -T celery_worker_us python - us < scripts/check_proxies.py

Add ``--no-sites`` after au/us to only test that each proxy connects.

For every proxy it:
  1. connects through it and reads the exit IP (is the proxy alive?)
  2. opens the home page of each store that uses it (does the store let it in?)

One request per proxy per store, the same as a single scrape, so it will not
get proxies blocked. Passwords are never printed, only host:port.
"""
from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from curl_cffi import requests as cr

TIMEOUT = 20

AMAZON_MARKERS = ("captchacharacters", "robot check", "api-services-support@amazon.com",
                  "sorry, we just need to make sure")
EBAY_MARKERS = ("pardon our interruption", "splashui/challenge", "access denied")
COSTCO_MARKERS = ("challenges.cloudflare.com", "cf-challenge-running", "cdn-cgi/challenge-platform",
                  "cf-chl-", "just a moment", "attention required", "you have been blocked",
                  "queue-it.net", "access denied")


def stores_for(region: str):
    """[(store name, home page, block markers, proxy list)]"""
    if region == "au":
        from scrapers import amazon_au_proxies as a, costco_au_proxies as c, ebay_au_proxies as e
        return [
            ("Costco AU", "https://www.costco.com.au/", COSTCO_MARKERS, c.load_proxy_urls()),
            ("Amazon AU", "https://www.amazon.com.au/", AMAZON_MARKERS, a.load_amazon_au_proxy_urls()),
            ("eBay AU", "https://www.ebay.com.au/", EBAY_MARKERS, e.load_ebay_au_proxy_urls()),
        ]
    if region == "us":
        from scrapers import amazon_us_proxies as a, ebay_us_proxies as e
        return [
            ("Amazon US", "https://www.amazon.com/", AMAZON_MARKERS, a.load_amazon_us_proxy_urls()),
            ("eBay US", "https://www.ebay.com/", EBAY_MARKERS, e.load_ebay_us_proxy_urls()),
        ]
    sys.exit("First argument must be au or us")


def label(url: str) -> str:
    p = urlparse(url)
    return f"{p.hostname}:{p.port}" if p.port else (p.hostname or "?")


def fetch(url: str, proxy: str):
    t = time.monotonic()
    r = cr.get(url, proxies={"http": proxy, "https": proxy}, impersonate="chrome131",
               timeout=TIMEOUT, allow_redirects=True)
    return r, time.monotonic() - t


def check_alive(proxy: str) -> tuple[bool, str]:
    try:
        r, secs = fetch("https://api.ipify.org", proxy)
        if r.status_code == 200 and r.text.strip():
            return True, f"exit IP {r.text.strip()} ({secs:.1f}s)"
        return False, f"HTTP {r.status_code}"
    except Exception as exc:  # noqa: BLE001
        return False, short_error(exc)


def check_store(proxy: str, home: str, markers) -> tuple[str, str]:
    try:
        r, secs = fetch(home, proxy)
    except Exception as exc:  # noqa: BLE001
        return "DEAD", short_error(exc)
    body = (r.text or "").lower()
    hit = next((m for m in markers if m in body), None)
    if hit:
        return "BLOCKED", f"'{hit}' page, HTTP {r.status_code}"
    if r.status_code in (403, 429, 503):
        return "BLOCKED", f"HTTP {r.status_code}"
    if r.status_code >= 400:
        return "ERROR", f"HTTP {r.status_code}"
    return "OK", f"{secs:.1f}s"


def short_error(exc: Exception) -> str:
    msg = str(exc).lower()
    if "timed out" in msg or "timeout" in msg:
        return "timed out"
    if "407" in msg or "proxy authentication" in msg:
        return "wrong proxy user/password (407)"
    if "connect" in msg or "refused" in msg or "resolve" in msg:
        return "cannot connect to proxy"
    return type(exc).__name__ + ": " + str(exc)[:80]


def main() -> None:
    args = [a.lower() for a in sys.argv[1:]]
    region = args[0] if args else ""
    sites = "--no-sites" not in args
    stores = stores_for(region)

    all_proxies = list(dict.fromkeys(u for *_, urls in stores for u in urls))
    if not all_proxies:
        sys.exit(f"No {region.upper()} proxies are set on this worker.")
    print(f"Testing {len(all_proxies)} unique {region.upper()} proxies...\n")

    with ThreadPoolExecutor(max_workers=10) as pool:
        alive = dict(zip(all_proxies, pool.map(check_alive, all_proxies)))

        results = {}
        for name, home, markers, urls in stores:
            if not sites:
                break
            todo = [u for u in urls if alive[u][0]]
            results[name] = dict(zip(todo, pool.map(lambda u: check_store(u, home, markers), todo)))

    print("Proxy alive check")
    for u in all_proxies:
        ok, info = alive[u]
        print(f"  {'OK  ' if ok else 'DEAD'}  {label(u):<28} {info}")
    n_alive = sum(ok for ok, _ in alive.values())
    print(f"  -> {n_alive} of {len(all_proxies)} proxies connect\n")

    if not sites:
        return
    summary = []
    for name, _, _, urls in stores:
        print(name)
        if not urls:
            print("  no proxies set\n")
            summary.append(f"{name}: no proxies")
            continue
        ok = 0
        for u in urls:
            status, info = results[name].get(u, ("DEAD", alive[u][1]))
            ok += status == "OK"
            print(f"  {status:<7} {label(u):<28} {info}")
        print(f"  -> {ok} of {len(urls)} working\n")
        summary.append(f"{name}: {ok}/{len(urls)} working")

    print("Summary: " + " | ".join(summary))


if __name__ == "__main__":
    main()
