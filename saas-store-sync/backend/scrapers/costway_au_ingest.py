"""
Costway AU ingest — downloads the dropship CSV and looks up price/stock per SKU.

Unlike Vevor (public S3 XLSX, fetched on the main ``light`` worker), the Costway
feed is **AU-IP only**:

  https://au.costway.com/media/feed/Dropship-AU.csv

Fetching it from the main/EU/US app returns HTML or 403. Catalog ingest therefore
runs on the AU Celery worker (``heavy-au``). The HTTP client **must not** use
``HTTP_PROXY`` / ``PROXY_URL`` — those would egress as a non-AU IP.

Feed columns (Dropship-AU.csv):

  SKU | Item NO. | Title | Description | Price | Category | Link | QTY | Weight | Image

Only **SKU** (match key), **Price** (vendor cost) and **QTY** (stock) are applied.
**Item NO.** and **Link** are secondary lookup keys. Weight is never treated as qty.
"""
from __future__ import annotations

import csv
import logging
import os
import re
import tempfile
from typing import Iterable

import requests

from scrapers.vevor_au_ingest import (
    clean_id,
    compact_id,
    lookup_sku,
    parse_inventory_value,
    parse_price_value,
    round_precise,
)

logger = logging.getLogger("scrapers.costway_au_ingest")

DEFAULT_COSTWAY_AU_FEED_URL = "https://au.costway.com/media/feed/Dropship-AU.csv"

# Screenshot / live Dropship-AU.csv layout when the header is unrecognizable.
# A=SKU, B=Item NO., E=Price, G=Link, H=QTY — not Vevor's A/G/I (G=Availability, I=weight).
COSTWAY_SKU_COL = 0
COSTWAY_ITEM_NO_COL = 1
COSTWAY_PRICE_COL = 4
COSTWAY_LINK_COL = 6
COSTWAY_QTY_COL = 7

_GEO_BLOCK_MSG = (
    "Costway AU feed is geo-restricted to Australian IPs. "
    "catalog.run_costway_au_ingest must run on the AU worker (queue heavy-au) "
    "with a direct connection (no HTTP_PROXY / PROXY_URL)."
)


def resolve_costway_au_feed_url(raw: str | None = None) -> str:
    """Return feed URL; treat unset/blank env as the public AU default."""
    if raw is None:
        raw = os.getenv("COSTWAY_AU_FEED_URL")
    url = (raw or "").strip()
    return url or DEFAULT_COSTWAY_AU_FEED_URL


COSTWAY_AU_FEED_URL = resolve_costway_au_feed_url()


def _ingest_only_result() -> dict:
    """Sentinel for scrapers.get_price_and_stock — force VendorPrice fallback."""
    return {
        "price": None,
        "inventory": None,
        "title": None,
        "error_code": "costway_ingest_only",
        "error_message": (
            "Costway AU is fed from the dropship CSV, not scraped per-URL. "
            "Run catalog.tasks.run_costway_au_ingest on the AU worker (heavy-au)."
        ),
    }


def is_costway_vendor_code(code: str | None) -> bool:
    """True for Costway / Costway AU codes. Never matches Costco."""
    c = (code or "").strip().lower().replace("-", "").replace("_", "").replace(" ", "")
    return c == "costway" or c.startswith("costway")


def is_costway_product_url(url: str | None) -> bool:
    u = (url or "").strip().lower()
    if not u or "costco." in u:
        return False
    return "costway.com" in u


def normalize_costway_product_url(url: str | None) -> str:
    """Strip scheme, www, query, and trailing slash for Link-column matching."""
    s = (url or "").strip()
    if not s:
        return ""
    s = s.split("#", 1)[0].split("?", 1)[0].rstrip("/").lower()
    s = re.sub(r"^https?://(www\.)?", "", s)
    return s


def costway_identity_candidates(
    *,
    vendor_id: str = "",
    sku: str = "",
    variant_key: str = "",
    product_key: str = "",
    vendor_url: str = "",
) -> list[str]:
    """Ordered product-ID guesses to match against feed SKU / Item NO."""
    from urllib.parse import parse_qs, unquote, urlparse

    keys: list[str] = []
    seen: set[str] = set()

    def add(val) -> None:
        s = clean_id(val)
        if not s:
            return
        marker = s.lower()
        if marker in seen:
            return
        seen.add(marker)
        keys.append(s)

    add(vendor_id)
    add(sku)
    add(variant_key)
    add(product_key)
    url = (vendor_url or "").strip()
    if url:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        for qk in ("sku", "SKU", "id", "product_id", "productId", "item_no", "item"):
            for val in qs.get(qk, []):
                add(unquote(val or ""))
        path = unquote(parsed.path or "").rstrip("/")
        last = path.split("/")[-1] if path else ""
        last = re.sub(r"\.(html?|php)$", "", last, flags=re.I)
        if last:
            add(last)
    return keys


def lookup_costway_price_stock(
    lookup: dict,
    lookup_compact: dict,
    lookup_by_url: dict | None = None,
    *,
    vendor_id: str = "",
    sku: str = "",
    variant_key: str = "",
    product_key: str = "",
    vendor_url: str = "",
) -> dict | None:
    """Find a feed row by Link, then by Vendor ID / SKU / Item NO. tokens."""
    url = (vendor_url or "").strip()
    if url and lookup_by_url:
        hit = lookup_by_url.get(normalize_costway_product_url(url))
        if hit:
            return hit
    for key in costway_identity_candidates(
        vendor_id=vendor_id,
        sku=sku,
        variant_key=variant_key,
        product_key=product_key,
        vendor_url=vendor_url,
    ):
        hit = lookup_sku(lookup, lookup_compact, key)
        if hit:
            return hit
    return None


def _normalize_header(value) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def resolve_costway_feed_columns(header_row) -> tuple[int, int, int, int | None, int | None, str]:
    """
    Map the CSV header to ``(sku, price, qty, item_no, link, mode)``.

    ``mode`` is ``"header"`` when SKU, Price, and QTY were found by name, else
    ``"positional"`` for the Dropship-AU layout A/E/H.
    """
    sku_idx = price_idx = qty_idx = None
    item_no_idx = link_idx = None
    for idx, cell in enumerate(header_row or ()):
        name = _normalize_header(cell)
        if not name:
            continue
        name_key = name.rstrip(".")
        if sku_idx is None and name_key == "sku":
            sku_idx = idx
        elif price_idx is None and name_key in ("price", "posted price"):
            price_idx = idx
        elif qty_idx is None and name_key in ("qty", "q'ty", "quantity"):
            qty_idx = idx
        elif item_no_idx is None and name_key in ("item no", "item number", "item_no"):
            item_no_idx = idx
        elif link_idx is None and name_key in ("link", "product link", "product url"):
            link_idx = idx
    if sku_idx is not None and price_idx is not None and qty_idx is not None:
        return sku_idx, price_idx, qty_idx, item_no_idx, link_idx, "header"
    return (
        COSTWAY_SKU_COL,
        COSTWAY_PRICE_COL,
        COSTWAY_QTY_COL,
        COSTWAY_ITEM_NO_COL,
        COSTWAY_LINK_COL,
        "positional",
    )


def _cell(row, idx):
    if idx is None or idx < 0 or idx >= len(row):
        return None
    return row[idx]


def load_costway_via_csv(path: str) -> tuple[dict, dict, int]:
    """
    Read the Costway AU CSV into SKU lookups.

    Returns ``(lookup, lookup_compact, data_rows_scanned)``.
    Each entry is ``{'Posted Price': float, 'Posted Inventory': int}`` and may
    include ``Product Link``.
    """
    lookup: dict[str, dict] = {}
    lookup_compact: dict[str, dict] = {}
    pos_rows = 0
    priced_rows = 0
    sku_idx = price_idx = qty_idx = None
    item_no_idx = link_idx = None
    mode = "positional"

    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(8192)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(handle, dialect)
        for idx, row in enumerate(reader):
            if idx == 0:
                sku_idx, price_idx, qty_idx, item_no_idx, link_idx, mode = (
                    resolve_costway_feed_columns(row)
                )
                if mode == "positional":
                    logger.warning(
                        "Costway AU feed header not recognized (%r...); using "
                        "positional SKU/Price/QTY columns A/E/H.",
                        list(row or ())[:8],
                    )
                continue
            if not row or not any(str(cell).strip() for cell in row if cell is not None):
                continue
            pos_rows += 1
            sku = clean_id(_cell(row, sku_idx))
            if not sku:
                continue
            price = round_precise(parse_price_value(_cell(row, price_idx)), 2)
            stock = parse_inventory_value(_cell(row, qty_idx))
            if price > 0:
                priced_rows += 1
            entry = {"Posted Price": price, "Posted Inventory": int(stock)}
            link_raw = _cell(row, link_idx)
            if link_raw is not None:
                link = str(link_raw).strip()
                if link:
                    entry["Product Link"] = link
            lookup[sku] = entry
            ckey = compact_id(sku)
            if ckey:
                lookup_compact[ckey] = entry
            item_no = clean_id(_cell(row, item_no_idx))
            if item_no and item_no.lower() != sku.lower():
                lookup.setdefault(item_no, entry)
                citem = compact_id(item_no)
                if citem:
                    lookup_compact.setdefault(citem, entry)

    if lookup and priced_rows == 0:
        logger.warning(
            "Costway AU feed parsed %s SKUs but every price is 0 "
            "(mode=%s, price column index=%s) — feed layout may have changed.",
            len(lookup), mode, price_idx,
        )
    else:
        logger.info(
            "Costway AU feed parsed: %s SKUs, %s with price > 0 (mode=%s, "
            "sku=%s price=%s qty=%s).",
            len(lookup), priced_rows, mode, sku_idx, price_idx, qty_idx,
        )
    return lookup, lookup_compact, pos_rows


def _looks_like_html(chunk: bytes) -> bool:
    head = (chunk or b"").lstrip()[:512].lower()
    return head.startswith(b"<!doctype") or head.startswith(b"<html") or b"<html" in head[:200]


def fetch_costway_feed(url: str | None = None, timeout: int = 90) -> str:
    """Download the Costway AU CSV with **no proxy** and return a temp file path."""
    feed_url = resolve_costway_au_feed_url(url)
    if not feed_url.startswith(("http://", "https://")):
        raise ValueError(f"COSTWAY_AU_FEED_URL must be an http(s) URL; got {feed_url!r}")

    session = requests.Session()
    session.trust_env = False
    session.proxies = {"http": None, "https": None}

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/csv,text/plain,application/octet-stream,*/*;q=0.8",
    }
    try:
        resp = session.get(
            feed_url,
            timeout=timeout,
            stream=True,
            allow_redirects=True,
            headers=headers,
            proxies={"http": None, "https": None},
        )
        resp.raise_for_status()
    except requests.HTTPError as exc:
        status = getattr(exc.response, "status_code", None)
        if status in (401, 403, 451):
            raise RuntimeError(f"{_GEO_BLOCK_MSG} HTTP {status}.") from exc
        raise

    tmp = tempfile.NamedTemporaryFile(prefix="costway_au_", suffix=".csv", delete=False)
    first = b""
    try:
        for chunk in resp.iter_content(chunk_size=65536):
            if not chunk:
                continue
            if not first:
                first = chunk
                if _looks_like_html(first):
                    raise RuntimeError(
                        f"{_GEO_BLOCK_MSG} Server returned HTML instead of CSV."
                    )
            tmp.write(chunk)
    finally:
        tmp.close()

    if not first.strip():
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise RuntimeError("Costway AU feed download was empty.")
    return tmp.name


def load_costway_feed_lookups() -> dict:
    """Download and parse the live Costway AU CSV once per scrape job."""
    csv_path = fetch_costway_feed(COSTWAY_AU_FEED_URL)
    try:
        lookup, lookup_compact, pos_rows = load_costway_via_csv(csv_path)
    finally:
        try:
            os.unlink(csv_path)
        except OSError:
            pass
    lookup_by_url: dict[str, dict] = {}
    for entry in lookup.values():
        link = (entry.get("Product Link") or "").strip()
        if not link:
            continue
        key = normalize_costway_product_url(link)
        if key:
            lookup_by_url[key] = entry
    return {
        "lookup": lookup,
        "lookup_compact": lookup_compact,
        "lookup_by_url": lookup_by_url,
        "feed_rows": pos_rows,
    }


def iter_costway_entries(lookup: dict) -> Iterable[tuple[str, dict]]:
    return lookup.items()


__all__ = [
    "DEFAULT_COSTWAY_AU_FEED_URL",
    "COSTWAY_AU_FEED_URL",
    "COSTWAY_SKU_COL",
    "COSTWAY_PRICE_COL",
    "COSTWAY_QTY_COL",
    "resolve_costway_au_feed_url",
    "_ingest_only_result",
    "is_costway_vendor_code",
    "is_costway_product_url",
    "normalize_costway_product_url",
    "costway_identity_candidates",
    "lookup_costway_price_stock",
    "load_costway_feed_lookups",
    "resolve_costway_feed_columns",
    "load_costway_via_csv",
    "fetch_costway_feed",
]
