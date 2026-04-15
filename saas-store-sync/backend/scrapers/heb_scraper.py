"""
HEB product scraper (optimized Selenium flow).

Goals:
- Fast page load and extraction (minimal sleeps, explicit waits)
- Reuse one driver across rows in the same sync run
- Early block/captcha detection
- Return the same shape used by the app: {"price": float|None, "stock": int|None, "title": str|None}
"""
import logging
import importlib
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import zipfile
from typing import Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .core import ScrapeResult, detect_block, parse_price_text, random_delay

logger = logging.getLogger("scrapers.heb")

RETRY_LIMIT = 3
PAGE_TIMEOUT = 25
PRICE_WAIT_TIMEOUT = 10
PDP_READY_TIMEOUT = 24
DEBUG_DUMP_DIR = os.environ.get("HEB_DEBUG_DIR", "/tmp/heb_debug")
HEB_HOME = os.environ.get("HEB_HOME_URL", "https://www.heb.com/")
COOKIES_FILE = os.environ.get("HEB_COOKIES_FILE", "cookies.json")
HEB_HEADLESS = os.environ.get("HEB_HEADLESS", "1").strip().lower() not in ("0", "false", "no")
HEB_USE_UNDETECTED = os.environ.get("HEB_USE_UNDETECTED", "1").strip().lower() not in ("0", "false", "no")

# Junk/interstitial responses (~650 B) vs real PDPs; avoid 3× no_price retries on those.
TINY_PDP_HTML_MAX_LEN = 2000
# 0-based: at most attempts 0 and 1 (one retry) for tiny invalid PDPs.
INVALID_PDP_LAST_ATTEMPT_INDEX = 1

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_NEXTDATA_PRICE_PATHS = [
    lambda d: d["props"]["pageProps"]["pdpData"]["product"]["price"]["value"],
    lambda d: d["props"]["pageProps"]["pdpData"]["product"]["lowPrice"],
    lambda d: d["props"]["pageProps"]["pdpData"]["product"]["price"],
    lambda d: d["props"]["pageProps"]["productData"]["price"]["value"],
    lambda d: d["props"]["pageProps"]["productData"]["price"],
    lambda d: d["props"]["pageProps"]["initialData"]["product"]["price"]["value"],
]

_NEXTDATA_TITLE_PATHS = [
    lambda d: d["props"]["pageProps"]["pdpData"]["product"]["name"],
    lambda d: d["props"]["pageProps"]["pdpData"]["product"]["title"],
    lambda d: d["props"]["pageProps"]["productData"]["name"],
    lambda d: d["props"]["pageProps"]["productData"]["title"],
    lambda d: d["props"]["pageProps"]["seo"]["title"],
    lambda d: d["props"]["pageProps"]["seoTitle"],
]

_NEXTDATA_TITLE_BRAND_FALLBACK = [
    lambda d: d["props"]["pageProps"]["pdpData"]["product"]["brand"]["name"],
]


def _next_data_from_soup(soup: BeautifulSoup) -> dict:
    """Parse embedded Next.js payload when window.__NEXT_DATA__ was not available yet."""
    for sel in ("script#__NEXT_DATA__", "script[id='__NEXT_DATA__']"):
        el = soup.select_one(sel)
        if not el:
            continue
        raw = (el.string or el.get_text() or "").strip()
        if not raw:
            continue
        try:
            return json.loads(raw)
        except Exception:
            continue
    return {}


def _merge_next_data(primary: Optional[dict], soup: BeautifulSoup) -> dict:
    """Prefer driver-injected __NEXT_DATA__, fall back to HTML script tag."""
    if primary and isinstance(primary, dict) and primary.get("props"):
        return primary
    from_html = _next_data_from_soup(soup)
    if from_html:
        return from_html
    return primary if isinstance(primary, dict) else {}


def _deep_extract_price_from_obj(obj, depth: int = 0, max_depth: int = 28) -> Optional[float]:
    if depth > max_depth or obj is None:
        return None

    if isinstance(obj, dict):
        for cents_key in (
            "priceInCents",
            "finalPriceInCents",
            "salePriceInCents",
            "listPriceInCents",
            "nowPriceInCents",
            "itemPriceInCents",
        ):
            if cents_key in obj:
                v = obj[cents_key]
                if isinstance(v, (int, float)) and 1 <= v < 10_000_000:
                    return round(float(v) / 100.0, 2)

        for key in ("price", "finalPrice", "salePrice", "listPrice", "lowPrice", "displayPrice", "nowPrice"):
            if key not in obj:
                continue
            v = obj[key]
            if isinstance(v, dict):
                for subk in ("value", "amount", "display", "formatted"):
                    if subk in v:
                        p = parse_price_text(str(v[subk]))
                        if p is not None:
                            return p
                p = _deep_extract_price_from_obj(v, depth + 1, max_depth)
                if p is not None:
                    return p
            elif isinstance(v, (int, float, str)):
                p = parse_price_text(str(v))
                if p is not None:
                    return p

        for v in obj.values():
            p = _deep_extract_price_from_obj(v, depth + 1, max_depth)
            if p is not None:
                return p

    elif isinstance(obj, list):
        for item in obj:
            p = _deep_extract_price_from_obj(item, depth + 1, max_depth)
            if p is not None:
                return p

    return None


def _deep_extract_title_from_obj(obj, depth: int = 0, max_depth: int = 22) -> Optional[str]:
    if depth > max_depth or obj is None:
        return None
    if isinstance(obj, dict):
        for key in ("name", "title", "productName", "displayName"):
            if key in obj and isinstance(obj[key], str):
                t = obj[key].strip()
                if 3 < len(t) < 600:
                    return t[:500]
        desc = obj.get("description")
        if isinstance(desc, str):
            t = desc.strip()
            if 10 < len(t) < 200:
                return t[:500]
        for v in obj.values():
            t = _deep_extract_title_from_obj(v, depth + 1, max_depth)
            if t:
                return t
    elif isinstance(obj, list):
        for item in obj:
            t = _deep_extract_title_from_obj(item, depth + 1, max_depth)
            if t:
                return t
    return None


def _normalize_document_title(raw: str) -> Optional[str]:
    t = (raw or "").strip()
    if len(t) < 4:
        return None
    low = t.lower()
    if low in ("heb", "heb.com", "error", "access denied", "page not found"):
        return None

    base = re.split(r"\s*[|\u2013\u2014]\s*", t, maxsplit=1)[0].strip()
    if len(base) < 4:
        return None
    blo = base.lower()
    if blo in ("heb", "shop", "products"):
        return None
    return base[:500]


def _title_from_json_ld(soup: BeautifulSoup) -> Optional[str]:
    for script in soup.select("script[type='application/ld+json']"):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue

        def walk(node):
            if isinstance(node, dict):
                typ = node.get("@type")
                types = typ if isinstance(typ, list) else ([typ] if typ else [])
                if "Product" in types:
                    for key in ("name", "title"):
                        v = node.get(key)
                        if isinstance(v, str) and len(v.strip()) > 3:
                            return v.strip()[:500]
                for v in node.values():
                    got = walk(v)
                    if got:
                        return got
            elif isinstance(node, list):
                for it in node:
                    got = walk(it)
                    if got:
                        return got
            return None

        found = walk(data)
        if found:
            return found
    return None


def _js_string(value: str) -> str:
    return json.dumps(value)


def _split_proxy_env_list(raw: str) -> list[str]:
    """Split comma/newline/semicolon-separated proxy entries from env."""
    if not raw or not str(raw).strip():
        return []
    parts: list[str] = []
    for line in raw.replace(";", "\n").split("\n"):
        for chunk in line.split(","):
            c = chunk.strip()
            if c:
                parts.append(c)
    return parts


def _parse_proxy_configs() -> list[dict]:
    """
    Build ordered proxy pool. Precedence:
    1) PROXY_URLS — multiple full URLs (http://user:pass@host:port), comma or newline separated
    2) PROXY_ENDPOINTS — host:port,host:port,... with shared PROXY_USER / PROXY_PASS / PROXY_SCHEME
    3) PROXY_URL — single URL
    4) PROXY_HOST + PROXY_PORT (+ optional PROXY_USER, PROXY_PASS, PROXY_SCHEME)
    """
    urls_raw = (os.environ.get("PROXY_URLS") or "").strip()
    if urls_raw:
        out: list[dict] = []
        for item in _split_proxy_env_list(urls_raw):
            parsed = urlparse(item)
            if not parsed.hostname or not parsed.port:
                raise ValueError(
                    "Invalid PROXY_URLS entry. Each value must look like "
                    "http://user:pass@host:port — got %r" % (item,)
                )
            out.append(
                {
                    "scheme": (parsed.scheme or "http").strip() or "http",
                    "host": parsed.hostname,
                    "port": int(parsed.port),
                    "username": (parsed.username or "").strip(),
                    "password": (parsed.password or "").strip(),
                }
            )
        return out

    eps_raw = (os.environ.get("PROXY_ENDPOINTS") or "").strip()
    if eps_raw:
        username = (os.environ.get("PROXY_USER") or "").strip()
        password = (os.environ.get("PROXY_PASS") or "").strip()
        scheme = (os.environ.get("PROXY_SCHEME") or "http").strip() or "http"
        if not username or not password:
            raise ValueError("PROXY_ENDPOINTS requires PROXY_USER and PROXY_PASS")
        out_eps: list[dict] = []
        for item in _split_proxy_env_list(eps_raw):
            if ":" not in item:
                raise ValueError(
                    "Invalid PROXY_ENDPOINTS entry %r — expected host:port" % (item,)
                )
            host, _, port_s = item.rpartition(":")
            host = host.strip()
            port_s = port_s.strip()
            if not host or not port_s.isdigit():
                raise ValueError(
                    "Invalid PROXY_ENDPOINTS entry %r — expected host:port" % (item,)
                )
            out_eps.append(
                {
                    "scheme": scheme,
                    "host": host,
                    "port": int(port_s),
                    "username": username,
                    "password": password,
                }
            )
        return out_eps

    proxy_url = (os.environ.get("PROXY_URL") or "").strip()
    if proxy_url:
        parsed = urlparse(proxy_url)
        if not parsed.hostname or not parsed.port:
            raise ValueError("Invalid PROXY_URL. Expected format: http://user:pass@host:port")
        return [
            {
                "scheme": parsed.scheme or "http",
                "host": parsed.hostname,
                "port": int(parsed.port),
                "username": parsed.username or "",
                "password": parsed.password or "",
            }
        ]

    host = (os.environ.get("PROXY_HOST") or "").strip()
    port = (os.environ.get("PROXY_PORT") or "").strip()
    username = (os.environ.get("PROXY_USER") or "").strip()
    password = (os.environ.get("PROXY_PASS") or "").strip()
    scheme = (os.environ.get("PROXY_SCHEME") or "http").strip() or "http"

    if not host or not port:
        return []

    return [
        {
            "scheme": scheme,
            "host": host,
            "port": int(port),
            "username": username,
            "password": password,
        }
    ]


_proxy_pool_cache: Optional[list] = None
_proxy_rr_lock = threading.Lock()
_proxy_rr_index = 0


def _proxy_pool() -> list:
    global _proxy_pool_cache
    if _proxy_pool_cache is None:
        _proxy_pool_cache = _parse_proxy_configs()
    return _proxy_pool_cache


def _next_proxy_conf() -> Optional[dict]:
    """Round-robin across the pool (each new Chrome session gets the next endpoint)."""
    pool = _proxy_pool()
    if not pool:
        return None
    global _proxy_rr_index
    with _proxy_rr_lock:
        conf = pool[_proxy_rr_index % len(pool)]
        _proxy_rr_index += 1
    return conf


def _create_proxy_extension(proxy_conf: dict) -> str:
    manifest = {
        "version": "1.0.0",
        "manifest_version": 3,
        "name": "Chrome Proxy Auth Extension",
        "permissions": [
            "proxy",
            "storage",
            "tabs",
            "webRequest",
            "webRequestAuthProvider",
        ],
        "host_permissions": ["<all_urls>"],
        "background": {
            "service_worker": "background.js"
        },
        "minimum_chrome_version": "110",
    }

    background_js = f"""
const config = {{
  mode: "fixed_servers",
  rules: {{
    singleProxy: {{
      scheme: {_js_string(proxy_conf["scheme"])},
      host: {_js_string(proxy_conf["host"])},
      port: {int(proxy_conf["port"])}
    }},
    bypassList: ["localhost", "127.0.0.1"]
  }}
}};

function applyProxy() {{
  chrome.proxy.settings.set({{ value: config, scope: "regular" }}, () => {{}});
}}

chrome.runtime.onInstalled.addListener(applyProxy);
chrome.runtime.onStartup.addListener(applyProxy);

chrome.webRequest.onAuthRequired.addListener(
  (details, callback) => {{
    callback({{
      authCredentials: {{
        username: {_js_string(proxy_conf["username"])},
        password: {_js_string(proxy_conf["password"])}
      }}
    }});
  }},
  {{ urls: ["<all_urls>"] }},
  ["asyncBlocking"]
);
""".strip()

    temp_dir = tempfile.mkdtemp(prefix="chrome_proxy_ext_")
    ext_path = os.path.join(temp_dir, "proxy_auth_extension.zip")

    with zipfile.ZipFile(ext_path, "w", zipfile.ZIP_DEFLATED) as zp:
        zp.writestr("manifest.json", json.dumps(manifest, indent=2))
        zp.writestr("background.js", background_js)

    return ext_path


def _load_cookies(driver, cookies_file: str = COOKIES_FILE):
    """
    Load persisted cookies for heb.com to reduce store-gate/challenge frequency.
    Safe no-op when file is absent or invalid.
    """
    if not cookies_file or not os.path.exists(cookies_file):
        return
    try:
        with open(cookies_file, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        added = 0
        skipped_expired = 0
        skipped_non_heb = 0
        skipped_noise = 0
        now_ts = int(time.time())
        # Keep high-signal cookies only; ignore telemetry/noise cookies.
        ignore_prefixes = (
            "_ga",
            "_gcl_",
            "AMP_",
            "AMP_MKTG_",
            "Optanon",
        )
        for cookie in cookies if isinstance(cookies, list) else []:
            try:
                allowed = {
                    "name", "value", "domain", "path",
                    "expiry", "secure", "httpOnly", "sameSite"
                }
                c = {k: v for k, v in cookie.items() if k in allowed}
                dom = str(c.get("domain", "")).lower()
                if dom and "heb.com" not in dom:
                    skipped_non_heb += 1
                    continue

                exp = c.get("expiry")
                if isinstance(exp, (int, float)) and int(exp) <= now_ts:
                    skipped_expired += 1
                    continue

                name = str(c.get("name", ""))
                low_name = name.lower()
                if low_name.startswith(tuple(p.lower() for p in ignore_prefixes)):
                    skipped_noise += 1
                    continue
                driver.add_cookie(c)
                added += 1
            except Exception:
                continue
        logger.info(
            "HEB cookies: loaded=%d skipped_expired=%d skipped_non_heb=%d skipped_noise=%d file=%s",
            added,
            skipped_expired,
            skipped_non_heb,
            skipped_noise,
            cookies_file,
        )
    except Exception as exc:
        logger.warning("HEB cookie load failed (%s): %s", cookies_file, exc)


def _safe_quit_and_recreate_driver(driver, session: dict):
    try:
        HebDriver.quit_safe(driver)
    except Exception:
        pass
    new_driver = HebDriver.create()
    session["heb_driver"] = new_driver
    return new_driver


def _is_tiny_invalid_pdp(
    next_data: Optional[dict],
    merged_nd: dict,
    page_title: str,
    title: Optional[str],
    base_html_len: int,
) -> bool:
    """
    Empty/interstitial/junk: tiny raw HTML, no document title, no product title,
    and no __NEXT_DATA__ props from runtime or from HTML.
    """
    if base_html_len >= TINY_PDP_HTML_MAX_LEN:
        return False
    if (page_title or "").strip():
        return False
    if title is not None:
        return False
    run_props = isinstance(next_data, dict) and bool(next_data.get("props"))
    mer_props = isinstance(merged_nd, dict) and bool(merged_nd.get("props"))
    if run_props or mer_props:
        return False
    return True


def _dump_debug_files(
    vendor_url: str,
    current_url: str,
    page_title: str,
    title,
    next_data,
    html: str,
    attempt: int,
    failure_label: str = "fail",
):
    try:
        os.makedirs(DEBUG_DUMP_DIR, exist_ok=True)
        ts = int(time.time() * 1000)
        safe_label = re.sub(r"[^\w\-]+", "_", failure_label)[:48]
        base = os.path.join(DEBUG_DUMP_DIR, f"heb_{safe_label}_{ts}_attempt{attempt}")

        with open(f"{base}.html", "w", encoding="utf-8") as f:
            f.write(html or "")

        meta = {
            "failure_label": failure_label,
            "vendor_url": vendor_url,
            "current_url": current_url,
            "page_title": page_title,
            "title_extracted": title,
            "has_next_data": bool(next_data),
            "next_data_keys": list(next_data.keys()) if isinstance(next_data, dict) else [],
            "html_len": len(html or ""),
        }
        with open(f"{base}.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        logger.warning("HEB debug dumped: %s(.html/.json)", base)
    except Exception as exc:
        logger.warning("HEB debug dump failed: %s", exc)


class HebParser:
    TITLE_SELECTORS = (
        "h1[data-testid*='title']",
        "h1.product-title",
        "[data-testid='product-title']",
        "[data-qe-id='product-name']",
        "[data-qe-id*='product-name']",
        "h1",
        "meta[name='twitter:title']",
        "meta[property='og:title']",
    )
    PRICE_SELECTORS = (
        "[data-qe-id='price-label']",
        "[data-qe-id*='price']",
        "[data-testid='product-price']",
        "[data-testid*='price']",
        "[data-component*='price' i]",
        "[data-component*='Price']",
        "meta[itemprop='price']",
        "meta[property='product:price:amount']",
        "[itemprop='price']",
        "[aria-label*='price' i]",
        "span[class*='price' i]",
        "p[class*='price' i]",
        ".price",
    )
    STOCK_HINTS_IN = (
        "add to cart",
        "add to bag",
        "add to list",
        "in stock",
        "available for pickup",
        "available for delivery",
    )
    STOCK_HINTS_OUT_STRONG = (
        "out of stock",
        "sold out",
        "currently out of stock",
        "unavailable for purchase",
        "not available for delivery",
        "not available for pickup",
    )

    @classmethod
    def _select_text(cls, soup: BeautifulSoup, selectors) -> str:
        for sel in selectors:
            el = soup.select_one(sel)
            if not el:
                continue
            if el.name == "meta":
                text = (el.get("content") or "").strip()
            else:
                text = el.get_text(separator=" ", strip=True)
            if text:
                return text
        return ""

    @classmethod
    def extract_title(
        cls,
        soup: BeautifulSoup,
        next_data: dict = None,
        page_title: Optional[str] = None,
    ) -> Optional[str]:
        if next_data:
            for path_fn in _NEXTDATA_TITLE_PATHS:
                try:
                    val = path_fn(next_data)
                    if val and isinstance(val, str):
                        t = val.strip()
                        if len(t) > 3:
                            return t[:500]
                except (KeyError, TypeError):
                    continue
            props = next_data.get("props") if isinstance(next_data, dict) else None
            if isinstance(props, dict):
                page_props = props.get("pageProps")
                if isinstance(page_props, dict):
                    t_deep = _deep_extract_title_from_obj(page_props, max_depth=14)
                    if t_deep:
                        return t_deep[:500]

        t = cls._select_text(soup, cls.TITLE_SELECTORS)
        if t and len(t.strip()) > 3:
            return t[:500]

        ld = _title_from_json_ld(soup)
        if ld:
            return ld

        if page_title:
            doc = _normalize_document_title(page_title)
            if doc:
                return doc

        if next_data:
            for path_fn in _NEXTDATA_TITLE_BRAND_FALLBACK:
                try:
                    val = path_fn(next_data)
                    if val and isinstance(val, str):
                        t = val.strip()
                        if len(t) > 3:
                            return t[:500]
                except (KeyError, TypeError):
                    continue
        return None

    @classmethod
    def extract_price(cls, soup: BeautifulSoup, html: str, next_data: dict = None) -> Optional[float]:
        if next_data:
            for path_fn in _NEXTDATA_PRICE_PATHS:
                try:
                    val = path_fn(next_data)
                    if val is not None:
                        p = parse_price_text(str(val))
                        if p is not None:
                            logger.debug("Price from __NEXT_DATA__: %s", p)
                            return p
                except (KeyError, TypeError):
                    continue

            p_deep = _deep_extract_price_from_obj(next_data)
            if p_deep is not None:
                logger.debug("Price from __NEXT_DATA__ deep walk: %s", p_deep)
                return p_deep

        txt = cls._select_text(soup, cls.PRICE_SELECTORS)
        p = parse_price_text(txt)
        if p is not None:
            return p

        for script in soup.select("script[type='application/ld+json']"):
            raw = (script.string or script.get_text() or "").strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue
            p = cls._extract_price_from_json(data)
            if p is not None:
                return p

        html = html or ""
        normalized_html = html.replace("\\u0024", "$").replace("&dollar;", "$")

        for cents_pat in (
            r'"priceInCents"\s*:\s*(\d{1,6})',
            r'"finalPriceInCents"\s*:\s*(\d{1,6})',
            r'"salePriceInCents"\s*:\s*(\d{1,6})',
            r'"listPriceInCents"\s*:\s*(\d{1,6})',
            r'"nowPriceInCents"\s*:\s*(\d{1,6})',
            r'"itemPriceInCents"\s*:\s*(\d{1,6})',
        ):
            m = re.search(cents_pat, normalized_html, re.IGNORECASE)
            if m:
                try:
                    return round(float(m.group(1)) / 100.0, 2)
                except Exception:
                    continue

        for pat in (
            r'"finalPrice"\s*:\s*"?\$?(\d+(?:\.\d{1,3})?)',
            r'"salePrice"\s*:\s*"?\$?(\d+(?:\.\d{1,3})?)',
            r'"regularPrice"\s*:\s*"?\$?(\d+(?:\.\d{1,3})?)',
            r'"listPrice"\s*:\s*"?\$?(\d+(?:\.\d{1,3})?)',
            r'"displayPrice"\s*:\s*"?\$?(\d+(?:\.\d{1,3})?)',
            r'"nowPrice"\s*:\s*"?\$?(\d+(?:\.\d{1,3})?)',
            r'"unitPrice"\s*:\s*"?\$?(\d+(?:\.\d{1,3})?)',
            r'"priceValue"\s*:\s*"?\$?(\d+(?:\.\d{1,3})?)',
            r'"integer"\s*:\s*(\d{1,4})\s*,\s*"fractional"\s*:\s*"(\d{1,3})"',
            r'"[A-Za-z_]*[Pp]rice[A-Za-z_]*"\s*:\s*"?\$?(\d{1,4}(?:\.\d{1,3})?)',
            r'"amount"\s*:\s*"?\$?(\d+(?:\.\d{1,3})?)',
            r'"value"\s*:\s*(\d{1,4}\.\d{2})',
        ):
            m = re.search(pat, normalized_html, re.IGNORECASE)
            if m:
                if len(m.groups()) == 2:
                    p = parse_price_text(f"{m.group(1)}.{m.group(2)}")
                else:
                    p = parse_price_text(m.group(1))
                if p is not None:
                    return p

        text = soup.get_text(" ", strip=True) or ""
        for pat in (
            r'(\d{1,4}(?:\.\d{1,3})?)\s*(?:/|per)?\s*(?:ea|each)\b',
            r'(?:now|price|our price)\s*\$?\s*(\d{1,4}(?:\.\d{1,3})?)',
        ):
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                p = parse_price_text(m.group(1))
                if p is not None:
                    return p

        return None

    @classmethod
    def _extract_price_from_json(cls, data) -> Optional[float]:
        if isinstance(data, list):
            for it in data:
                p = cls._extract_price_from_json(it)
                if p is not None:
                    return p
            return None
        if not isinstance(data, dict):
            return None
        for key in ("price", "value", "salePrice", "finalPrice", "amount", "lowPrice"):
            if key in data:
                p = parse_price_text(str(data[key]))
                if p is not None:
                    return p
        offers = data.get("offers")
        if offers is not None:
            p = cls._extract_price_from_json(offers)
            if p is not None:
                return p
        for v in data.values():
            if isinstance(v, (dict, list)):
                p = cls._extract_price_from_json(v)
                if p is not None:
                    return p
        return None

    @classmethod
    def extract_stock(cls, soup: BeautifulSoup, html: str) -> int:
        text = (soup.get_text(" ", strip=True) or "").lower()
        if not text:
            text = (html or "").lower()
        has_in = any(k in text for k in cls.STOCK_HINTS_IN)
        has_out = any(k in text for k in cls.STOCK_HINTS_OUT_STRONG)
        if has_in and not has_out:
            return 3
        if has_out and not has_in:
            return 0
        if has_in and has_out:
            return 3
        return 3


class HebDriver:
    @staticmethod
    def create(proxy_conf: Optional[dict] = None):
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options

        uc_module = None
        if HEB_USE_UNDETECTED:
            try:
                uc_module = importlib.import_module("undetected_chromedriver")
            except Exception:
                uc_module = None

        use_uc = bool(HEB_USE_UNDETECTED and uc_module is not None)
        opts = uc_module.ChromeOptions() if use_uc else Options()
        if HEB_HEADLESS:
            opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--window-size=1366,900")
        opts.add_argument("--disable-notifications")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
        opts.add_argument(f"--user-agent={_USER_AGENT}")

        chrome_bin = os.environ.get("CHROME_BIN")
        if chrome_bin:
            opts.binary_location = chrome_bin

        proxy_ext_path = None
        try:
            if proxy_conf is None:
                proxy_conf = _next_proxy_conf()
        except ValueError as exc:
            logger.error("HEB proxy config: %s", exc)
            raise

        if proxy_conf:
            pool = _proxy_pool()
            if len(pool) > 1:
                logger.info(
                    "Using proxy %s://%s:%s (pool size %d)",
                    proxy_conf["scheme"],
                    proxy_conf["host"],
                    proxy_conf["port"],
                    len(pool),
                )
            else:
                logger.info(
                    "Using proxy %s://%s:%s",
                    proxy_conf["scheme"],
                    proxy_conf["host"],
                    proxy_conf["port"],
                )
            if proxy_conf["username"] and proxy_conf["password"]:
                proxy_ext_path = _create_proxy_extension(proxy_conf)
                opts.add_extension(proxy_ext_path)
            else:
                opts.add_argument(
                    f'--proxy-server={proxy_conf["scheme"]}://{proxy_conf["host"]}:{proxy_conf["port"]}'
                )

        if use_uc:
            logger.info("HEB using undetected_chromedriver")
            driver = uc_module.Chrome(options=opts, use_subprocess=True)
        else:
            driver = webdriver.Chrome(options=opts)
        driver.set_page_load_timeout(PAGE_TIMEOUT)

        if proxy_ext_path:
            try:
                driver._heb_proxy_ext_dir = os.path.dirname(proxy_ext_path)
            except Exception:
                pass

        if sys.platform.startswith("win"):
            plat = "Win32"
            webgl_vendor = "Intel Inc."
            renderer = "Intel Iris OpenGL Engine"
        else:
            plat = "Linux x86_64"
            webgl_vendor = "Google Inc. (Google)"
            renderer = "ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (LLVM 10.0.0) (0x0000C0DE)))"

        try:
            stealth = importlib.import_module("selenium_stealth").stealth
            stealth(
                driver,
                languages=["en-US", "en"],
                vendor="Google Inc.",
                platform=plat,
                webgl_vendor=webgl_vendor,
                renderer=renderer,
                fix_hairline=True,
            )
        except Exception:
            pass

        # Optional warm-up + cookie injection. Helps bypass store selector/challenges.
        try:
            driver.get(HEB_HOME)
            time.sleep(0.8)
            _load_cookies(driver)
            driver.get(HEB_HOME)
            time.sleep(0.6)
        except Exception:
            pass
        return driver

    @staticmethod
    def quit_safe(driver):
        if not driver:
            return
        ext_dir = getattr(driver, "_heb_proxy_ext_dir", None)
        try:
            driver.quit()
        except Exception:
            pass
        if ext_dir and os.path.isdir(ext_dir):
            try:
                shutil.rmtree(ext_dir, ignore_errors=True)
            except Exception:
                pass


def _heb_next_data_has_product(driver) -> bool:
    try:
        return bool(
            driver.execute_script(
                """
                try {
                  var d = window.__NEXT_DATA__;
                  if (!d || typeof d !== 'object' || !d.props || !d.props.pageProps) return false;
                  var pp = d.props.pageProps;
                  if (pp.pdpData && pp.pdpData.product) return true;
                  if (pp.productData) return true;
                  if (pp.initialData && pp.initialData.product) return true;
                  return false;
                } catch (e) { return false; }
                """
            )
        )
    except Exception:
        return False


def _fetch_html(driver, url: str) -> str:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    driver.get(url)

    try:
        driver.execute_script("window.scrollTo(0, 400);")
        time.sleep(0.35)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.35);")
    except Exception:
        pass

    try:
        WebDriverWait(driver, 10).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
    except Exception:
        pass

    waited_pdp = False
    try:
        WebDriverWait(driver, PDP_READY_TIMEOUT).until(lambda d: _heb_next_data_has_product(d))
        waited_pdp = True
        logger.debug("HEB __NEXT_DATA__ PDP ready")
    except Exception:
        pass

    waited = waited_pdp
    if not waited:
        for sel in (
            "script#__NEXT_DATA__",
            "[data-qe-id*='price']",
            "[data-testid*='price']",
            "meta[itemprop='price']",
            "[itemprop='price']",
            "span[class*='price' i]",
            "h1",
        ):
            try:
                WebDriverWait(driver, min(PRICE_WAIT_TIMEOUT, 7)).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, sel))
                )
                waited = True
                break
            except Exception:
                continue

    if waited_pdp:
        time.sleep(0.45)
    elif waited:
        time.sleep(0.65)
    else:
        time.sleep(1.0)

    return driver.page_source or ""


def _fetch_runtime_json(driver) -> tuple[str, dict]:
    next_data = {}
    payloads = []

    scripts = {
        "__NEXT_DATA__": "return window.__NEXT_DATA__ || null;",
        "__APOLLO_STATE__": "return window.__APOLLO_STATE__ || null;",
        "__INITIAL_STATE__": "return window.__INITIAL_STATE__ || null;",
    }
    for key, script in scripts.items():
        try:
            obj = driver.execute_script(script)
            if obj:
                if key == "__NEXT_DATA__" and isinstance(obj, dict):
                    next_data = obj
                payloads.append(json.dumps(obj, ensure_ascii=False))
        except Exception:
            continue

    return "\n".join(payloads), next_data


def _is_block(html: str) -> tuple[bool, str]:
    blocked, reason = detect_block(html)
    if blocked:
        return True, reason

    lower = html.lower()

    for needle in (
        "incapsula incident id",
        "pardon our interruption",
        "request unsuccessful",
        "errors.edgesuite.net",
        "access denied",
        "service unavailable",
        "temporarily unavailable",
    ):
        if needle in lower:
            return True, "waf_challenge"

    if "select your store" in lower:
        if not any(
            x in lower
            for x in ("add to cart", "add to bag", "add to trolley", "add to list")
        ):
            return True, "store_gate"

    if (
        "__next_data__" in lower
        and "product-detail" in lower
        and not any(x in lower for x in (
            "add to cart",
            "add to bag",
            "available for pickup",
            "available for delivery",
            'itemprop="price"',
            "product:price:amount",
            "priceincents",
            '"price":',
            '"lowprice":',
        ))
    ):
        return True, "soft_empty_pdp"

    return False, ""


def scrape_heb(vendor_url: str, region: str, session: dict = None) -> dict:
    if session is None:
        session = {}

    driver = session.get("heb_driver")
    created = False

    if driver is None:
        driver = HebDriver.create()
        session["heb_driver"] = driver
        created = True

    last = None

    try:
        for attempt in range(RETRY_LIMIT):
            if attempt:
                random_delay(1.0, 2.5)

            try:
                html = _fetch_html(driver, vendor_url)
            except Exception as exc:
                logger.warning("HEB fetch error attempt %d: %s", attempt, exc)
                last = ScrapeResult.fail("fetch_error", str(exc), "", "heb", vendor_url)
                driver = _safe_quit_and_recreate_driver(driver, session)
                continue

            page_title = ""
            current_url = vendor_url
            try:
                page_title = (driver.title or "").strip()
            except Exception:
                pass
            try:
                current_url = driver.current_url or vendor_url
            except Exception:
                pass

            runtime_json, next_data = _fetch_runtime_json(driver)
            base_html_len = len(html or "")
            if runtime_json:
                html = f"{html}\n<!--runtime-json-->\n{runtime_json}"

            blocked, reason = _is_block(html)
            if blocked:
                logger.warning("HEB blocked (%s) attempt %d url=%s", reason, attempt, current_url)
                _dump_debug_files(
                    vendor_url, current_url, page_title, None, next_data, html, attempt, f"blocked_{reason}",
                )
                last = ScrapeResult.fail(
                    f"blocked_{reason}",
                    f"Blocked: {reason}",
                    html,
                    "heb",
                    vendor_url,
                )
                driver = _safe_quit_and_recreate_driver(driver, session)
                continue

            soup = BeautifulSoup(html, "lxml")
            merged_nd = _merge_next_data(next_data, soup)
            title = HebParser.extract_title(soup, merged_nd, page_title=page_title)

            if _is_tiny_invalid_pdp(next_data, merged_nd, page_title, title, base_html_len):
                logger.warning(
                    "HEB invalid_or_blocked_pdp (tiny/non-product) attempt=%d base_html_len=%d url=%s",
                    attempt,
                    base_html_len,
                    vendor_url,
                )
                _dump_debug_files(
                    vendor_url,
                    current_url,
                    page_title,
                    title,
                    next_data,
                    html,
                    attempt,
                    "invalid_or_blocked_pdp",
                )
                last = ScrapeResult.fail(
                    "invalid_or_blocked_pdp",
                    "HEB empty or non-product page (tiny HTML, no PDP payload)",
                    html,
                    "heb",
                    vendor_url,
                )
                driver = _safe_quit_and_recreate_driver(driver, session)
                if attempt >= INVALID_PDP_LAST_ATTEMPT_INDEX:
                    break
                continue

            price = HebParser.extract_price(soup, html, merged_nd)
            stock = HebParser.extract_stock(soup, html)

            if price is None:
                logger.warning(
                    "HEB price not found attempt %d — title=%s page_title=%s current_url=%s url=%s next_data_present=%s html_len=%s",
                    attempt,
                    title,
                    page_title,
                    current_url,
                    vendor_url,
                    bool(next_data),
                    len(html or ""),
                )
                _dump_debug_files(
                    vendor_url, current_url, page_title, title, next_data, html, attempt, "no_price",
                )
                last = ScrapeResult.fail("no_price", "Price not found on HEB page", html, "heb", vendor_url)
                driver = _safe_quit_and_recreate_driver(driver, session)
                continue

            logger.info("HEB ok price=%.2f stock=%d title=%s", price, stock, title)
            return ScrapeResult.ok(price=price, stock=stock, title=title).to_legacy()

    except Exception as exc:
        logger.exception("HEB scrape exception: %s", exc)
        last = ScrapeResult.fail("exception", str(exc), "", "heb", vendor_url)

    finally:
        if created and session.get("heb_driver") is not driver:
            HebDriver.quit_safe(driver)

    return (
        last or ScrapeResult.fail("max_retries", "HEB retries exhausted", "", "heb", vendor_url)
    ).to_legacy()


def close_heb_session(session):
    if session is None:
        return
    drv = session.pop("heb_driver", None)
    HebDriver.quit_safe(drv)
