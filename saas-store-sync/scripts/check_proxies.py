"""Check which scraper proxies actually work right now.

Run it inside the scraper worker, so it reads the same proxy lists and the
same database the scraper uses:

    docker compose -f docker-compose.au-scraper.prod.yml exec -T celery_worker_au python - au < scripts/check_proxies.py
    docker compose -f docker-compose.us-scraper.prod.yml exec -T celery_worker_us python - us < scripts/check_proxies.py

Options after au/us:
    --no-sites           only test that each proxy connects
    amazon=<url>         product page to test Amazon with (same for ebay=, costco=)

For every proxy it:
  1. connects through it and reads the exit IP (is the proxy alive?)
  2. opens a real product page of each store that uses it, the way the
     scraper does (does the store give it the real page?)

Product pages come from listings in your catalog that scraped fine most
recently. Store home pages are no good for this: Amazon answers them with a
small bot-check page and eBay with a 403 even when product pages load.

One request per proxy per store, the same as a single scrape, so it will not
get proxies blocked. Passwords are never printed, only host:port.
"""
from __future__ import annotations

import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from itertools import cycle
from urllib.parse import urlparse

import requests
from curl_cffi import requests as cr

TIMEOUT = 25
MAX_PAGES = 5  # different products per store, rotated across proxies

AMAZON_BLOCK = ("captchacharacters", "robot check", "api-services-support@amazon.com",
                "sorry, we just need to make sure", "to discuss automated access")
EBAY_BLOCK = ("pardon our interruption", "splashui/challenge", "checking your browser")
COSTCO_BLOCK = ("challenges.cloudflare.com", "cf-challenge-running", "cdn-cgi/challenge-platform",
                "cf-chl-", "<title>just a moment", "attention required", "you have been blocked",
                "queue-it.net")

# store key -> (url pattern in the catalog, block markers, "real page" markers, how to fetch)
STORES = {
    "Costco AU": ("costco", r"costco\.com\.au/", COSTCO_BLOCK, ("costco",), "cffi"),
    "Amazon AU": ("amazon", r"amazon\.com\.au/", AMAZON_BLOCK, ("producttitle",), "requests"),
    "eBay AU": ("ebay", r"ebay\.com\.au/", EBAY_BLOCK, ("x-item-title",), "ebay_au"),
    "Amazon US": ("amazon", r"amazon\.com/", AMAZON_BLOCK, ("producttitle",), "requests"),
    "eBay US": ("ebay", r"ebay\.com/", EBAY_BLOCK, ("x-item-title",), "ebay_us"),
}


def proxy_lists(region: str) -> dict[str, list[str]]:
    if region == "au":
        from scrapers import amazon_au_proxies as a, costco_au_proxies as c, ebay_au_proxies as e
        return {"Costco AU": c.load_proxy_urls(), "Amazon AU": a.load_amazon_au_proxy_urls(),
                "eBay AU": e.load_ebay_au_proxy_urls()}
    if region == "us":
        from scrapers import amazon_us_proxies as a, ebay_us_proxies as e
        return {"Amazon US": a.load_amazon_us_proxy_urls(), "eBay US": e.load_ebay_us_proxy_urls()}
    sys.exit("First argument must be au or us")


def sample_pages(pattern: str) -> list[str]:
    """Product URLs from listings of this store that scraped fine most recently."""
    try:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
        import django

        django.setup()
        from django.db.models import Q

        from catalog.models import ProductMapping

        urls = (
            ProductMapping.objects.filter(
                is_active=True,
                last_scrape_time__isnull=False,
                product__vendor_url__iregex=pattern,
            )
            .filter(Q(scrape_error__isnull=True) | Q(scrape_error=""))
            .order_by("-last_scrape_time")
            .values_list("product__vendor_url", flat=True)[:50]
        )
        return list(dict.fromkeys(urls))[:MAX_PAGES]
    except Exception as exc:  # noqa: BLE001
        print(f"  (could not read the catalog: {type(exc).__name__}: {str(exc)[:100]})")
        return []


def label(url: str) -> str:
    p = urlparse(url)
    return f"{p.hostname}:{p.port}" if p.port else (p.hostname or "?")


def short_error(exc: Exception) -> str:
    msg = str(exc).lower()
    if "timed out" in msg or "timeout" in msg:
        return "timed out"
    if "407" in msg or "proxy authentication" in msg:
        return "wrong proxy user/password (407)"
    if "connect" in msg or "refused" in msg or "resolve" in msg:
        return "cannot connect to proxy"
    return type(exc).__name__ + ": " + str(exc)[:80]


def check_alive(proxy: str) -> tuple[bool, str]:
    try:
        t = time.monotonic()
        r = cr.get("https://api.ipify.org", proxies={"http": proxy, "https": proxy},
                   impersonate="chrome131", timeout=TIMEOUT)
        if r.status_code == 200 and r.text.strip():
            return True, f"exit IP {r.text.strip()} ({time.monotonic() - t:.1f}s)"
        return False, f"HTTP {r.status_code}"
    except Exception as exc:  # noqa: BLE001
        return False, short_error(exc)


def fetch(how: str, url: str, proxy: str):
    proxies = {"http": proxy, "https": proxy}
    if how == "requests":  # Amazon scrapers: plain requests + browser-like headers
        from scrapers.core import get_random_headers

        s = requests.Session()
        s.headers.update(get_random_headers(f"https://{urlparse(url).hostname}/"))
        return s.get(url, proxies=proxies, timeout=TIMEOUT, allow_redirects=True)
    headers = None
    if how.startswith("ebay_"):  # eBay HTTP path: curl_cffi + the scraper's own headers
        from scrapers.ebay_common import EbayHTTP

        market = how[-2:]
        headers = EbayHTTP._get_headers(url, market.upper(), {}, market)  # noqa: SLF001
    return cr.get(url, headers=headers, proxies=proxies, impersonate="chrome131",
                  timeout=TIMEOUT, allow_redirects=True)


def page_title(body: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", body, re.S | re.I)
    return re.sub(r"\s+", " ", m.group(1)).strip()[:45] if m else ""


def check_store(proxy: str, page: str, block, real, how) -> tuple[str, str]:
    try:
        t = time.monotonic()
        r = fetch(how, page, proxy)
        secs = time.monotonic() - t
    except Exception as exc:  # noqa: BLE001
        return "DEAD", short_error(exc)
    body = r.text or ""
    low = body.lower()
    size = f"{len(body) // 1024} KB"
    hit = next((m for m in block if m in low), None)
    if hit:
        return "BLOCKED", f"'{hit}' page, HTTP {r.status_code}, {size}"
    if r.status_code in (403, 429, 503):
        return "BLOCKED", f"HTTP {r.status_code}, {size}"
    if r.status_code == 200 and any(m in low for m in real):
        return "OK", f"real page, {size}, {secs:.1f}s"
    if r.status_code == 404:
        return "OK?", f"HTTP 404 (product gone, proxy got through), {size}"
    return "UNSURE", f"HTTP {r.status_code}, {size}, title '{page_title(body)}'"


def main() -> None:
    args = sys.argv[1:]
    region = (args[0] if args else "").lower()
    sites = "--no-sites" not in args
    overrides = dict(a.split("=", 1) for a in args[1:] if "=" in a)
    lists = proxy_lists(region)

    all_proxies = list(dict.fromkeys(u for urls in lists.values() for u in urls))
    if not all_proxies:
        sys.exit(f"No {region.upper()} proxies are set on this worker.")
    print(f"Testing {len(all_proxies)} unique {region.upper()} proxies...\n")

    pages = {}
    if sites:
        for name in lists:
            key, pattern = STORES[name][0], STORES[name][1]
            pages[name] = [overrides[key]] if key in overrides else sample_pages(pattern)

    with ThreadPoolExecutor(max_workers=10) as pool:
        alive = dict(zip(all_proxies, pool.map(check_alive, all_proxies)))
        results = {}
        for name, urls in lists.items():
            if not sites or not pages.get(name):
                continue
            _, _, block, real, how = STORES[name]
            todo = [u for u in urls if alive[u][0]]
            jobs = list(zip(todo, cycle(pages[name])))
            results[name] = dict(zip(todo, pool.map(
                lambda job: check_store(job[0], job[1], block, real, how), jobs)))

    print("Proxy alive check")
    for u in all_proxies:
        ok, info = alive[u]
        print(f"  {'OK  ' if ok else 'DEAD'}  {label(u):<28} {info}")
    n_alive = sum(ok for ok, _ in alive.values())
    print(f"  -> {n_alive} of {len(all_proxies)} proxies connect\n")
    if not sites:
        return

    summary = []
    for name, urls in lists.items():
        print(name)
        if not urls:
            print("  no proxies set\n")
            summary.append(f"{name}: no proxies")
            continue
        if not pages.get(name):
            key = STORES[name][0]
            print(f"  no recently scraped product found to test with; pass {key}=<product url>\n")
            summary.append(f"{name}: not tested")
            continue
        print(f"  testing with {len(pages[name])} product page(s), e.g. {pages[name][0][:70]}")
        ok = 0
        for u in urls:
            status, info = results[name].get(u, ("DEAD", alive[u][1]))
            ok += status in ("OK", "OK?")
            print(f"  {status:<7} {label(u):<28} {info}")
        print(f"  -> {ok} of {len(urls)} working\n")
        if name == "eBay US":
            print("  Note: eBay US scrapes in real Chrome by default, which this test does not use.\n"
                  "  A 403 here means a plain request is refused; Chrome on the same proxy may still work.\n")
        summary.append(f"{name}: {ok}/{len(urls)} working")

    print("Summary: " + " | ".join(summary))


if __name__ == "__main__":
    main()
