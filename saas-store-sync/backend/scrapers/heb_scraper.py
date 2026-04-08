"""
HEB product scraper (optimized Selenium flow) - fixed version.

Return shape used by the app: {"price": float|None, "stock": int|None, "title": str|None}
"""
import logging
import json
import re
import time
from typing import Optional, Tuple, Dict, Any

from bs4 import BeautifulSoup

from .core import ScrapeResult, detect_block, parse_price_text, random_delay

logger = logging.getLogger("scrapers.heb")

RETRY_LIMIT = 3          # was 2 → gives 3 real attempts
PAGE_TIMEOUT = 25        # was 18 → HEB React hydration is slow
PRICE_WAIT_TIMEOUT = 10  # seconds to wait for price element

# Realistic UA — headless Chrome without this gets flagged instantly
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# HEB-specific Next.js data paths for price (ordered by reliability)
_NEXTDATA_PRICE_PATHS = [
    # pdpData path (most common)
    lambda d: d["props"]["pageProps"]["pdpData"]["product"]["price"]["value"],
    lambda d: d["props"]["pageProps"]["pdpData"]["product"]["lowPrice"],
    lambda d: d["props"]["pageProps"]["pdpData"]["product"]["price"],
    # productData path
    lambda d: d["props"]["pageProps"]["productData"]["price"]["value"],
    lambda d: d["props"]["pageProps"]["productData"]["price"],
    # initialData path
    lambda d: d["props"]["pageProps"]["initialData"]["product"]["price"]["value"],
]

_NEXTDATA_TITLE_PATHS = [
    lambda d: d["props"]["pageProps"]["pdpData"]["product"]["name"],
    lambda d: d["props"]["pageProps"]["productData"]["name"],
    lambda d: d["props"]["pageProps"]["pdpData"]["product"]["brand"]["name"],  # fallback to brand
]


class HebParser:
    TITLE_SELECTORS = (
        "h1[data-testid*='title']",
        "h1.product-title",
        "[data-testid='product-title']",
        "[data-qe-id='product-name']",
        "h1",
        "meta[property='og:title']",
    )
    PRICE_SELECTORS = (
        # Most specific HEB selectors first
        "[data-qe-id='price-label']",
        "[data-qe-id*='price']",
        "[data-testid='product-price']",
        "[data-testid*='price']",
        # Schema.org
        "meta[itemprop='price']",
        "meta[property='product:price:amount']",
        "[itemprop='price']",
        # Generic
        "[aria-label*='price' i]",
        "span[class*='price' i]",
        ".price",
    )
    STOCK_HINTS_IN = ("in stock", "available", "add to cart", "add to bag")
    STOCK_HINTS_OUT = ("out of stock", "unavailable", "sold out", "not available")

    @classmethod
    def _select_text(cls, soup: BeautifulSoup, selectors) -> str:
        for sel in selectors:
            try:
                el = soup.select_one(sel)
            except Exception:
                continue
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
    def extract_title(cls, soup: BeautifulSoup, next_data: Optional[Dict[str, Any]] = None) -> Optional[str]:
        # Try Next.js data first — most reliable
        if next_data:
            for path_fn in _NEXTDATA_TITLE_PATHS:
                try:
                    val = path_fn(next_data)
                    if val and isinstance(val, str):
                        return val[:500]
                except (KeyError, TypeError):
                    continue
        t = cls._select_text(soup, cls.TITLE_SELECTORS)
        return t[:500] if t else None

    @classmethod
    def extract_price(
        cls,
        soup: BeautifulSoup,
        html: str,
        next_data: Optional[Dict[str, Any]] = None,
    ) -> Optional[float]:
        # --- 1. Try Next.js structured data first (most reliable for HEB) ---
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

        # --- 2. CSS selectors ---
        txt = cls._select_text(soup, cls.PRICE_SELECTORS)
        p = parse_price_text(txt)
        if p is not None:
            return p

        # --- 3. JSON-LD product schema ---
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

        # --- 4. Regex across full HTML (including injected runtime JSON) ---
        html = html or ""
        normalized_html = html.replace("\\u0024", "$").replace("&dollar;", "$")

        # Cents-based fields first (very specific, no false positives)
        for cents_pat in (
            r'"priceInCents"\s*:\s*(\d{1,6})',
            r'"finalPriceInCents"\s*:\s*(\d{1,6})',
            r'"salePriceInCents"\s*:\s*(\d{1,6})',
        ):
            m = re.search(cents_pat, normalized_html, re.IGNORECASE)
            if m:
                try:
                    return round(float(m.group(1)) / 100.0, 2)
                except Exception:
                    continue

        # Dollar-value patterns (ordered most→least specific)
        for pat in (
            r'"finalPrice"\s*:\s*"?\$?(\d+(?:\.\d{1,3})?)',
            r'"salePrice"\s*:\s*"?\$?(\d+(?:\.\d{1,3})?)',
            r'"regularPrice"\s*:\s*"?\$?(\d+(?:\.\d{1,3})?)',
            r'"listPrice"\s*:\s*"?\$?(\d+(?:\.\d{1,3})?)',
            r'"unitPrice"\s*:\s*"?\$?(\d+(?:\.\d{1,3})?)',
            r'"priceValue"\s*:\s*"?\$?(\d+(?:\.\d{1,3})?)',
            r'"integer"\s*:\s*(\d{1,4})\s*,\s*"fractional"\s*:\s*"(\d{1,3})"',
            r'"[A-Za-z_]*[Pp]rice[A-Za-z_]*"\s*:\s*"?\$?(\d{1,4}(?:\.\d{1,3})?)',
            r'"amount"\s*:\s*"?(\d+(?:\.\d{1,3})?)',
            r'"value"\s*:\s*(\d{1,4}\.\d{2})',  # require decimal — "value":5 is too vague
        ):
            m = re.search(pat, normalized_html, re.IGNORECASE)
            if m:
                if len(m.groups()) == 2:
                    p = parse_price_text(f"{m.group(1)}.{m.group(2)}")
                else:
                    p = parse_price_text(m.group(1))
                if p is not None:
                    return p

        # --- 5. Visible text "each/ea" and "now price" patterns ---
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
        """Always returns an int — never None."""
        text = (soup.get_text(" ", strip=True) or "").lower()
        if not text:
            text = (html or "").lower()
        if any(k in text for k in cls.STOCK_HINTS_OUT):
            return 0
        if any(k in text for k in cls.STOCK_HINTS_IN):
            return 3
        return 3  # default: assume in stock rather than blocking on missing signal


class HebDriver:
    @staticmethod
    def create():
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options

        opts = Options()
        opts.add_argument("--headless=new")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--window-size=1366,900")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_argument("--disable-notifications")
        opts.add_argument("--disable-popup-blocking")
        opts.add_argument(f"--user-agent={_USER_AGENT}")
        # Prevent navigator.webdriver flag being visible to the page
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
        driver = webdriver.Chrome(options=opts)
        driver.set_page_load_timeout(PAGE_TIMEOUT)
        # Mask webdriver property in JS (must run before first navigation for full effect)
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
        )
        return driver

    @staticmethod
    def quit_safe(driver):
        if not driver:
            return
        try:
            driver.quit()
        except Exception:
            pass


def _fetch_html(driver, url: str) -> str:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    driver.get(url)

    # Scroll gently to trigger lazy-load price elements
    try:
        driver.execute_script("window.scrollTo(0, 400);")
        time.sleep(0.3)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.35);")
    except Exception:
        pass

    # Wait for document ready
    try:
        WebDriverWait(driver, 10).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
    except Exception:
        pass

    # Wait for React hydration — look for any price-like or product signal
    waited = False
    for sel in (
        "[data-qe-id*='price']",
        "[data-testid*='price']",
        "meta[itemprop='price']",
        "[itemprop='price']",
        "span[class*='price' i]",
        "h1",
    ):
        try:
            WebDriverWait(driver, PRICE_WAIT_TIMEOUT).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, sel))
            )
            waited = True
            break
        except Exception:
            continue

    # Extra settle time after React hydration — price often renders ~500ms after h1
    if waited:
        time.sleep(0.8)

    return driver.page_source or ""


def _fetch_runtime_json(driver) -> Tuple[str, Dict[str, Any]]:
    """
    Returns (raw_json_string, parsed_next_data_dict).
    Tries window.__NEXT_DATA__ first (most useful), then other global stores.
    """
    next_data: Dict[str, Any] = {}
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


def _is_block(html: str, url: str) -> Tuple[bool, str]:
    """
    Wraps detect_block but also checks for HEB-specific soft blocks
    (location gate, age verification) that wouldn't trip generic detectors.
    """
    blocked, reason = detect_block(html)
    if blocked:
        return True, reason
    lower = html.lower()
    # HEB sometimes shows a store-selector interstitial before product content
    if (
        "select your store" in lower
        and "add to cart" not in lower
        and "add to bag" not in lower
    ):
        return True, "store_gate"
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
                random_delay(1.0, 2.5)  # slightly longer back-off on retry
            try:
                html = _fetch_html(driver, vendor_url)
            except Exception as exc:
                logger.warning("HEB fetch error attempt %d: %s", attempt, exc)
                last = ScrapeResult.fail("fetch_error", str(exc), "", "heb", vendor_url)
                continue

            runtime_json, next_data = _fetch_runtime_json(driver)
            if runtime_json:
                html = f"{html}\n<!--runtime-json-->\n{runtime_json}"

            blocked, reason = _is_block(html, vendor_url)
            if blocked:
                logger.warning("HEB blocked (%s) attempt %d", reason, attempt)
                last = ScrapeResult.fail(f"blocked_{reason}", f"Blocked: {reason}", html, "heb", vendor_url)
                continue

            soup = BeautifulSoup(html, "lxml")
            title = HebParser.extract_title(soup, next_data)
            price = HebParser.extract_price(soup, html, next_data)
            stock = HebParser.extract_stock(soup, html)  # always returns int now

            if price is None:
                logger.warning(
                    "HEB price not found attempt %d — title=%s url=%s",
                    attempt, title, vendor_url,
                )
                last = ScrapeResult.fail("no_price", "Price not found on HEB page", html, "heb", vendor_url)
                continue

            logger.info("HEB ok price=%.2f stock=%d title=%s", price, stock, title)
            return ScrapeResult.ok(price=price, stock=stock, title=title).to_legacy()

    except Exception as exc:
        logger.exception("HEB scrape exception: %s", exc)
        last = ScrapeResult.fail("exception", str(exc), "", "heb", vendor_url)
    finally:
        # Session owns driver lifetime; close_heb_session quits when the run ends.
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
