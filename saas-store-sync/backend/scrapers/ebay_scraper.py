"""
eBay product scraper (curl_cffi optional fast path + Selenium + BeautifulSoup).

eBay often serves an Argon2 / bot challenge to plain HTTP clients. Strategy:
  1) Optional HTTP GET via curl_cffi (browser TLS) with regional Referer — when it
     returns real HTML, parse without launching Chrome (set EBAY_HTTP_FIRST=0 to skip).
  2) Selenium headless Chromium for challenge resolution and client-rendered DOM.

URL normalization maps listings to www.ebay.com (USA) or www.ebay.com.au (AU).

Architecture:
  EbayParser     — HTML→data extraction (Replit/aiohttp-aligned selectors + madrona JSON)
  EbayDriver     — headless Chromium via Selenium
  EbayFetcher    — fetch with retry, block detection, WebDriverWait (any_of locators)

Public API:
  scrape_ebay(vendor_url, region, session) -> {"price": float|None, "stock": int|None, ...}
"""
import os
import re
import json
import time
import random
import logging
from urllib.parse import urlparse
from typing import Optional, Tuple

from bs4 import BeautifulSoup

from .core import (
    ScrapeResult,
    random_delay, backoff_delay, parse_price_text,
)

logger = logging.getLogger("scrapers.ebay")

TIMEOUT_SEC = 50        # VPS + bot checks can be slow
RETRY_LIMIT = 3
PAGE_LOAD_WAIT = 14     # eBay Argon2 / cookie wall needs headroom
EBAY_HTTP_TIMEOUT_SEC = 25

# Strip trailing BIN / offer labels so parse_price_text sees a numeric price (Replit parity).
PRICE_SUFFIX_PATTERN = re.compile(
    r"(or Best Offer|Buy It Now|Best Offer|Make Offer|each|/ea).*$",
    re.IGNORECASE,
)


# ═══════════════════════════════════════════════════════════════════════════
# URL normalization
# ═══════════════════════════════════════════════════════════════════════════

def _normalize_url(original_url: str, region: str) -> str:
    """Build a clean eBay item URL for the correct regional domain."""
    parsed = urlparse(original_url)
    path = parsed.path.strip("/")

    item_id = None
    if "/itm/" in original_url:
        parts = path.split("/")
        for p in reversed(parts):
            if p.isdigit() and len(p) >= 8:
                item_id = p
                break
        if not item_id:
            m = re.search(r"/itm/[^/]*/(\d+)", original_url)
            if m:
                item_id = m.group(1)
            else:
                m = re.search(r"/itm/(\d+)", original_url)
                if m:
                    item_id = m.group(1)

    if not item_id:
        m = re.search(r"(\d{10,})", original_url)
        if m:
            item_id = m.group(1)

    if not item_id:
        return original_url

    if region == "AU":
        return f"https://www.ebay.com.au/itm/{item_id}"
    return f"https://www.ebay.com/itm/{item_id}"


def _strip_price_suffix(text: str) -> str:
    if not text:
        return ""
    return PRICE_SUFFIX_PATTERN.sub("", text).strip()


def _ebay_region_referer(region: str) -> str:
    return "https://www.ebay.com.au/" if region == "AU" else "https://www.ebay.com/"


def _to_ebay_ca_url(url: str) -> str:
    """
    Replit-parity fallback: normalize to ebay.ca item URL.
    Sometimes .ca returns a parseable PDP where .com/.com.au serves a challenge shell.
    """
    parsed = urlparse(url or "")
    path = (parsed.path or "").strip("/")
    item_id = None
    if "/itm/" in (url or ""):
        parts = path.split("/")
        for p in reversed(parts):
            if p.isdigit() and len(p) >= 8:
                item_id = p
                break
    if not item_id:
        m = re.search(r"(\d{10,})", url or "")
        if m:
            item_id = m.group(1)
    if not item_id:
        return url
    return f"https://www.ebay.ca/itm/{item_id}"


def _ebay_http_first_enabled() -> bool:
    # Default off: datacenter IPs often get HTML shells without a buy box; Selenium is more reliable.
    # Set EBAY_HTTP_FIRST=1 for faster path on residential / clean IPs.
    return os.environ.get("EBAY_HTTP_FIRST", "0").lower() in ("1", "true", "yes")


# ═══════════════════════════════════════════════════════════════════════════
# Selenium driver management
# ═══════════════════════════════════════════════════════════════════════════

class EbayDriver:
    """Manage headless Chromium for eBay scraping."""

    @staticmethod
    def _create_driver():
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service

        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-infobars")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-web-security")
        options.add_argument("--ignore-certificate-errors")
        options.add_argument("--lang=en-US,en")

        # Randomize viewport to avoid fingerprinting
        width = random.randint(1800, 1920)
        height = random.randint(1000, 1080)
        options.add_argument(f"--window-size={width},{height}")

        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        )
        options.add_argument(f"--user-agent={ua}")

        chrome_bin = os.environ.get("CHROME_BIN")
        if chrome_bin:
            options.binary_location = chrome_bin

        chromedriver_path = os.environ.get("CHROMEDRIVER_PATH")
        if chromedriver_path and os.path.isfile(chromedriver_path):
            service = Service(executable_path=chromedriver_path)
        else:
            service = Service()

        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(TIMEOUT_SEC)

        # Spoof navigator.webdriver
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": """
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
                    Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                    window.chrome = { runtime: {} };
                """
            },
        )

        return driver

    @staticmethod
    def get_or_create(session_dict: dict):
        """Get existing driver from session or create a new one."""
        key = "ebay_selenium_driver"
        if session_dict is not None and key in session_dict:
            driver = session_dict[key]
            try:
                _ = driver.title
                return driver
            except Exception:
                try:
                    driver.quit()
                except Exception:
                    pass

        driver = EbayDriver._create_driver()
        if session_dict is not None:
            session_dict[key] = driver
        return driver

    @staticmethod
    def close(session_dict: dict):
        """Close the driver stored in session."""
        key = "ebay_selenium_driver"
        if session_dict is None:
            return
        driver = session_dict.pop(key, None)
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════════════
# Parser — stateless extraction
# ═══════════════════════════════════════════════════════════════════════════

class EbayParser:
    """Extract price, stock, and listing metadata from eBay HTML."""

    # Updated selectors for eBay 2024-2025 frontend (aligned with working aiohttp/Replit parser)
    PRICE_SELECTORS = [
        # Whole price row first — strips "Buy It Now" / Best Offer suffix (Replit `.x-price-primary`)
        ".x-price-primary",
        # Primary — current eBay frontend (2024-2025)
        "[data-testid='x-price-primary'] .ux-textspans--BOLD",
        "[data-testid='x-price-primary'] span",
        "[data-test-id='x-price-primary'] .ux-textspans--BOLD",
        "[data-test-id='x-price-primary'] span",
        # x-price-primary variants
        ".x-price-primary .ux-textspans--BOLD",
        ".x-price-primary span.ux-textspans",
        ".x-price-primary span",
        "div.x-price-primary",
        # Buy-it-now price block
        ".x-bin-price__content .ux-textspans--BOLD",
        ".x-bin-price__content span",
        ".x-bin-price span",
        "[data-testid='x-bin-price'] span",
        "[data-testid='x-bin-price'] .ux-textspans--BOLD",
        "section.x-item-price span.ux-textspans--BOLD",
        "div.ux-section-module span.ux-textspans--BOLD",
        # Auction current bid
        ".x-auction-price .ux-textspans--BOLD",
        ".x-auction-price span",
        # Legacy selectors (older listings still use these)
        ".ux-labels-values__values-content .ux-textspans--BOLD",
        "span.ux-textspans--BOLD",
        ".ux-price",
        "span[itemprop='price']",
        "#prcIsum",
        ".notranslate",
        # Mobile / app view fallbacks
        ".display-price",
        "[data-testid='price-value']",
        ".price-current",
    ]

    PRICE_JSON_PATTERNS = [
        r'"currentPrice"\s*:\s*\{\s*"value"\s*:\s*"([\d.]+)"',
        r'"buyItNowPrice"\s*:\s*\{\s*"value"\s*:\s*"([\d.]+)"',
        r'"convertedPrice"\s*:\s*\{\s*"value"\s*:\s*"([\d.]+)"',
        r'"binPrice"\s*:\s*\{\s*"value"\s*:\s*"([\d.]+)"',
        r'\\&quot;price\\&quot;\s*:\s*\[\\&quot;([\d.]+)\\&quot;\]',
        r'&quot;price&quot;\s*:\s*\[&quot;([\d.]+)&quot;\]',
        r'"price"\s*:\s*\[\s*"([\d.]+)"\s*\]',
        r'"price"\s*:\s*"([\d.]+)"',
        r'"value"\s*:\s*"([\d.]+)"\s*,\s*"currency"',
        r'"price"\s*:\s*\{\s*"value"\s*:\s*"([\d.]+)"',
        r'"__PRICE__"\s*:\s*"([\d.]+)"',
        r'"displayPrice"\s*:\s*\{\s*"value"\s*:\s*"([\d.]+)"',
        # 2024 window.__PRELOADED_STATE__ pattern
        r'"priceValue"\s*:\s*"([\d.]+)"',
        r'"finalPrice"\s*:\s*\{\s*"value"\s*:\s*([\d.]+)',
        r'"convertedAmount"\s*:\s*"([\d.]+)"',
        r'"price"\s*:\s*\{\s*"amount"\s*:\s*"([\d.]+)"',
        r'"transactionAmount"\s*:\s*\{\s*"value"\s*:\s*([\d.]+)',
    ]

    QUANTITY_PATTERN = re.compile(
        r'"NumberValidation","minValue":"(\d+)","maxValue":"(\d+)"'
    )

    STATUS_ENDED_PHRASES = (
        "listing has ended",
        "bidding has ended",
        "out of stock",
        "no longer available",
        "sold out",
        "this item is out of stock",
        "was ended",
    )

    # Updated title selectors for current eBay frontend
    TITLE_SELECTORS = [
        ".x-item-title__mainTitle span.ux-textspans",
        ".x-item-title__mainTitle span",
        "h1.x-item-title",
        "[data-testid='x-item-title'] span",
        "[data-testid='x-item-title']",
        "h1#itemTitle",
        "[data-test-id='x-item-title']",
        "h1#x-item-title",
        # Fallback meta
        "meta[property='og:title']",
    ]

    @classmethod
    def extract_title(cls, soup: BeautifulSoup) -> Optional[str]:
        for sel in cls.TITLE_SELECTORS:
            elem = soup.select_one(sel)
            if elem:
                if sel.startswith("meta") and elem.get("content"):
                    t = (elem.get("content") or "").strip()
                else:
                    t = elem.get_text(separator=" ", strip=True)
                if t and len(t) > 2:
                    return t[:500]
        for meta in (
            soup.find("meta", property="og:title"),
            soup.find("meta", attrs={"name": "twitter:title"}),
        ):
            if meta and meta.get("content"):
                t = meta["content"].strip()
                if t and len(t) > 2:
                    return t[:500]
        # Match Amazon-style fallback: visible H1/meta can be empty on some eBay shells
        if soup.title and soup.title.string:
            raw = soup.title.string.strip()
            for suffix in (" | eBay", " | eBay.com", " on eBay"):
                low = raw.lower()
                if low.endswith(suffix.lower()):
                    raw = raw[: -len(suffix)].strip()
            if raw and len(raw) > 2:
                return raw[:500]
        return None

    @classmethod
    def is_valid_listing(cls, soup: BeautifulSoup, html: str = "") -> bool:
        if cls.extract_title(soup) is not None:
            return True
        h = (html or "").lower()
        if "itemprop" in h and ("product" in h or "offers" in h):
            return True
        if 'og:type" content="product"' in h or "og:type' content='product'" in h:
            return True
        if "/itm/" in h and len(html) > 15000:
            return True
        return False

    @classmethod
    def detect_listing_type(cls, soup: BeautifulSoup, html: str) -> str:
        lower_html = html.lower()

        err_hdr = soup.select_one("p.error-header-v2__title")
        if err_hdr:
            et = err_hdr.get_text(strip=True).lower()
            if any(
                x in et
                for x in ("ended", "removed", "unavailable", "not available", "no longer", "sold out")
            ):
                return "ended"

        status_el = soup.select_one(".ux-layout-section__textual-display--statusMessage span")
        if status_el:
            st = status_el.get_text(strip=True).lower()
            if any(p in st for p in cls.STATUS_ENDED_PHRASES):
                return "ended"

        ended_indicators = [
            "This listing has ended",
            "Bidding has ended",
            "This item is out of stock",
        ]
        for indicator in ended_indicators:
            if indicator.lower() in lower_html:
                return "ended"

        sold_elem = soup.select_one(".vi-soldwrap-lnk, .d-statusmessage")
        if sold_elem and "sold" in sold_elem.get_text(strip=True).lower():
            return "ended"

        bid_elem = soup.select_one("#prcIsum_bidPrice, .vi-VR-cvipPrice, [itemprop='price']")
        place_bid = soup.select_one("#bidBtn_btn, .vi-bidding-area")
        if bid_elem or place_bid:
            return "auction"

        return "buy_now"

    @classmethod
    def extract_price(cls, soup: BeautifulSoup, html: str) -> Optional[float]:
        for mtag in soup.find_all("meta"):
            prop = (mtag.get("property") or "").lower()
            if prop == "og:price:amount" and mtag.get("content"):
                p = parse_price_text(_strip_price_suffix(str(mtag["content"])))
                if p:
                    return p
            if (mtag.get("itemprop") or "").lower() == "price" and mtag.get("content"):
                p = parse_price_text(_strip_price_suffix(str(mtag["content"])))
                if p:
                    return p

        for sel in cls.PRICE_SELECTORS:
            elem = soup.select_one(sel)
            if elem:
                text = elem.get_text(strip=True)
                if not text:
                    continue
                text = _strip_price_suffix(text)
                if not text:
                    continue
                logger.debug("Price selector '%s' matched text: %r", sel, text)
                if " to " in text.lower():
                    parts = re.split(r"\s+to\s+", text, flags=re.IGNORECASE)
                    p = parse_price_text(_strip_price_suffix(parts[0]))
                    if p:
                        return p
                p = parse_price_text(text)
                if p:
                    return p

        for pat in cls.PRICE_JSON_PATTERNS:
            m = re.search(pat, html)
            if m:
                p = parse_price_text(m.group(1))
                if p:
                    logger.debug("Price extracted via JSON pattern: %s → %s", pat, p)
                    return p

        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                offers = data.get("offers", {})
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                price_val = offers.get("price") or offers.get("lowPrice")
                if price_val:
                    p = parse_price_text(str(price_val))
                    if p:
                        logger.debug("Price extracted via ld+json: %s", p)
                        return p
            except (json.JSONDecodeError, KeyError, IndexError):
                continue

        bid_elem = soup.select_one("#prcIsum_bidPrice, .vi-VR-cvipPrice")
        if bid_elem:
            p = parse_price_text(_strip_price_suffix(bid_elem.get_text(strip=True)))
            if p:
                return p

        return None

    @staticmethod
    def _stock_from_availability_text(text: str) -> Optional[int]:
        if not text:
            return None
        text = text.lower()
        if "sold" in text and "available" not in text:
            return 0
        if "ended" in text or "unavailable" in text:
            return 0
        m2 = re.search(r"more than (\d+) available", text)
        if m2:
            return int(m2.group(1))
        m2 = re.search(r"(\d+)\s*available", text)
        if m2:
            return int(m2.group(1))
        if "last one" in text or "last item" in text:
            return 1
        if "available" in text or "in stock" in text:
            return 99
        return None

    @classmethod
    def extract_stock(cls, soup: BeautifulSoup, html: str) -> Optional[int]:
        m = cls.QUANTITY_PATTERN.search(html)
        if m:
            max_qty = int(m.group(2))
            if max_qty > 0:
                return max_qty

        stock_el = soup.select_one("div.x-quantity__availability")
        if stock_el:
            first_span = stock_el.find("span")
            chunk_el = first_span if first_span else stock_el
            got = cls._stock_from_availability_text(chunk_el.get_text(strip=True))
            if got is not None:
                return got

        # Updated stock selectors for current eBay frontend
        stock_selectors = [
            "div.ux-message__content",
            ".ux-labels-values--quantity .ux-labels-values__values-content",
            ".ux-labels-values--quantity",
            "[data-testid='x-quantity-available']",
            "#qtySubTxt",
            "span.qtyTxt",
            ".d-quantity__availability",
        ]
        for sel in stock_selectors:
            elem = soup.select_one(sel)
            if not elem:
                continue
            got = cls._stock_from_availability_text(elem.get_text(strip=True))
            if got is not None:
                return got

        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                avail = (
                    data.get("offers", {}) if isinstance(data.get("offers"), dict) else {}
                ).get("availability", "")
                if "OutOfStock" in avail or "Discontinued" in avail:
                    return 0
                if "InStock" in avail or "LimitedAvailability" in avail:
                    return 99
            except (json.JSONDecodeError, KeyError):
                continue

        return None


# ═══════════════════════════════════════════════════════════════════════════
# Fetcher — Selenium-based (eBay's Argon2 challenge requires JS execution)
# ═══════════════════════════════════════════════════════════════════════════

_CHALLENGE_INDICATORS = [
    "pardon our interruption",
    "checking your browser",
    "challengeget",
    "splashui",
    "enable javascript",
    "please enable javascript",
    "enable cookies",
    "turn on javascript",
    "just a moment",
    "cf-wrapper",  # Cloudflare-style interstitial sometimes proxied
]

_BLOCK_INDICATORS = [
    "captcha", "recaptcha", "verify you are human",
    "robot check", "security page", "access denied",
    "you have been blocked", "suspicious activity",
    "datadome", "perimeterx", "incapsula",
    "unusual traffic", "automated access",
]


def _is_challenge_or_blocked(content: str) -> Tuple[bool, str]:
    """Detect eBay challenge pages and generic block pages."""
    lower = content.lower()
    for indicator in _CHALLENGE_INDICATORS:
        if indicator in lower:
            return True, "challenge"
    for indicator in _BLOCK_INDICATORS:
        if indicator in lower:
            return True, "blocked"
    return False, ""


def _http_prefetch_looks_like_pdp(html: str) -> bool:
    """
    curl_cffi often gets 200 + large HTML that is still a bot wall or shell without a buy box.
    Require at least one strong listing/price signal before trusting HTTP-first HTML.
    """
    h = (html or "").lower()
    if len(h) < 8000:
        return False
    signals = (
        "x-price-primary",
        "data-testid=\"x-price-primary\"",
        "data-testid='x-price-primary'",
        '"currentprice"',
        "currentprice",
        "buyitnowprice",
        '"binprice"',
        "binprice",
        'itemprop="price"',
        "itemprop='price'",
        "prcisum",
        "__ebay",
        "madrona",  # eBay client bundles / state
        "x-bin-price",
        "ux-bin-price",
        '"pricevalue"',
    )
    return any(s in h for s in signals)


def fetch_ebay_html_http(url: str, region: str) -> Optional[str]:
    """
    Optional fast path: browser-like TLS (curl_cffi) + regional Referer.
    Datacenter IPs often get a challenge page — then returns None and Selenium runs.
    """
    try:
        from curl_cffi import requests as curl_requests
    except ImportError:
        logger.debug("curl_cffi not installed — skipping eBay HTTP fast path")
        return None
    # Try canonical URL first, then a Replit-style ebay.ca fallback.
    candidates = [url]
    ca_url = _to_ebay_ca_url(url)
    if ca_url and ca_url != url:
        candidates.append(ca_url)

    referers = [_ebay_region_referer(region), "https://www.ebay.ca/"]

    for candidate in candidates:
        for referer in referers:
            try:
                resp = curl_requests.get(
                    candidate,
                    impersonate="chrome131",
                    timeout=EBAY_HTTP_TIMEOUT_SEC,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/138.0.0.0 Safari/537.36 Edg/138.0.0.0"
                        ),
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9",
                        "Referer": referer,
                        "Cache-Control": "max-age=0",
                        "Pragma": "no-cache",
                    },
                    allow_redirects=True,
                )
            except Exception as exc:
                logger.debug("eBay HTTP fetch failed (%s): %s", candidate, exc)
                continue

            if resp.status_code != 200:
                continue

            html = resp.text or ""
            if len(html) < 9000:
                continue
            if _is_challenge_or_blocked(html)[0]:
                continue
            if not _http_prefetch_looks_like_pdp(html):
                continue

            # Ensure we can parse at least title or price before skipping Selenium.
            try:
                soup = BeautifulSoup(html, "lxml")
            except Exception:
                soup = BeautifulSoup(html, "html.parser")
            parsed_price = EbayParser.extract_price(soup, html)
            parsed_title = EbayParser.extract_title(soup)
            if parsed_price is None and not parsed_title:
                continue

            logger.info(
                "eBay HTTP-first accepted candidate=%s referer=%s bytes=%d",
                candidate, referer, len(html),
            )
            return html

    return None


class EbayFetcher:
    """Fetch eBay pages using Selenium headless Chromium."""

    @classmethod
    def _wait_for_price(cls, driver, timeout: int = 15) -> bool:
        """
        Wait until a price element appears in the DOM.
        Returns True if found, False if timed out.
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        price_locators = [
            (By.CSS_SELECTOR, "[data-testid='x-price-primary']"),
            (By.CSS_SELECTOR, "[data-testid='x-bin-price']"),
            (By.CSS_SELECTOR, ".x-price-primary"),
            (By.CSS_SELECTOR, ".x-bin-price"),
            (By.CSS_SELECTOR, "#prcIsum"),
            (By.CSS_SELECTOR, "span[itemprop='price']"),
            (By.CSS_SELECTOR, "meta[itemprop='price'][content]"),
        ]
        wait = WebDriverWait(driver, timeout)
        try:
            wait.until(
                EC.any_of(*[
                    EC.presence_of_element_located(loc) for loc in price_locators
                ])
            )
            logger.debug("Price element found (any_of locators)")
            return True
        except Exception:
            return False

    @classmethod
    def fetch(cls, url: str, session: dict) -> Tuple[Optional[str], Optional[int], str]:
        """
        Fetch URL via Selenium (solves JS challenges automatically).
        Returns (html_content, status_code_or_200, error_message).
        """
        try:
            driver = EbayDriver.get_or_create(session)
        except Exception as exc:
            return None, None, f"selenium_init: {exc}"

        try:
            driver.get(url)

            # Wait for initial page load and Argon2 challenge to pass
            time.sleep(PAGE_LOAD_WAIT)
            try:
                driver.execute_script(
                    "window.scrollTo(0, Math.min(900, document.body.scrollHeight || 900));"
                )
            except Exception:
                pass

            # ── DEBUG: log page title and HTML size immediately ──────────
            html = driver.page_source
            logger.info(
                "eBay fetch title=%r html_len=%d url=%s",
                driver.title, len(html), driver.current_url,
            )
            # ─────────────────────────────────────────────────────────────

            is_challenge, reason = _is_challenge_or_blocked(html)
            if is_challenge:
                logger.info("eBay %s detected, waiting 12s for resolution...", reason)
                time.sleep(12)        # was 8
                html = driver.page_source
                logger.info(
                    "eBay post-challenge title=%r html_len=%d",
                    driver.title, len(html),
                )
                is_challenge, _ = _is_challenge_or_blocked(html)
                if is_challenge:
                    logger.info("Still challenged, waiting 15s more...")
                    time.sleep(15)    # was 10
                    html = driver.page_source

            # If challenge passed, wait for price element to appear in DOM
            price_found = cls._wait_for_price(driver, timeout=22)
            if not price_found:
                logger.warning(
                    "No price element in DOM after first wait — one refresh for slow VPS/SPA loads"
                )
                try:
                    driver.refresh()
                    time.sleep(10)
                    price_found = cls._wait_for_price(driver, timeout=18)
                except Exception as refresh_exc:
                    logger.debug("eBay refresh retry skipped: %s", refresh_exc)
            if not price_found:
                logger.warning("No price element found in DOM after waiting — page may be incomplete")
            # Re-grab HTML (may still parse price from JSON-LD / embedded state)
            html = driver.page_source

            return html, 200, ""

        except Exception as exc:
            err_str = str(exc).lower()
            if "timeout" in err_str:
                return None, None, "timeout"
            logger.warning("Selenium fetch error: %s", exc)
            EbayDriver.close(session)
            return None, None, str(exc)


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════

def scrape_ebay(vendor_url: str, region: str, session: dict = None) -> dict:
    """
    Scrape eBay product page for price and stock.

    Parameters
    ----------
    vendor_url : str — Full eBay item URL
    region : str — 'USA' or 'AU'
    session : dict — Shared session dict (holds Selenium driver across calls).
                     IMPORTANT: pass the same dict for all products so the
                     Chrome driver is reused and not re-created every call.

                     Example:
                         session = {}
                         for url in urls:
                             result = scrape_ebay(url, "USA", session)

    Returns
    -------
    {"price": float|None, "stock": int|None, "title": str|None}
    """
    if session is None:
        logger.warning(
            "scrape_ebay called without a session dict — a new Chrome driver "
            "will be created for every call. Pass a shared session={} dict to "
            "reuse the driver across products and avoid bot detection."
        )
        session = {}

    url = _normalize_url(vendor_url, region)
    random_delay(0.5, 1.5)

    prefetch_html: Optional[str] = None
    if _ebay_http_first_enabled():
        prefetch_html = fetch_ebay_html_http(url, region)
        if prefetch_html:
            logger.info("eBay HTTP-first path got HTML (%d bytes), try parse before Selenium", len(prefetch_html))

    last_result = None
    for attempt in range(RETRY_LIMIT):
        if attempt > 0:
            backoff_delay(attempt, base=2.0, jitter=2.0)
            logger.info("eBay retry %d/%d for %s", attempt + 1, RETRY_LIMIT, url)

        if attempt == 0 and prefetch_html is not None:
            html = prefetch_html
            prefetch_html = None
            fetch_error = ""
        else:
            html, status, fetch_error = EbayFetcher.fetch(url, session)

        if fetch_error:
            last_result = ScrapeResult.fail(
                "fetch_error", f"{fetch_error} (attempt {attempt+1})", "", "ebay", url
            )
            continue

        if not html:
            last_result = ScrapeResult.fail("empty_response", "Empty response body", "", "ebay", url)
            continue

        # Challenge / block detection
        is_blocked, block_reason = _is_challenge_or_blocked(html)
        if is_blocked:
            last_result = ScrapeResult.fail(
                f"blocked_{block_reason}",
                f"eBay {block_reason} page detected after waiting",
                html, "ebay", url,
            )
            EbayDriver.close(session)
            continue

        # Parse
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")

        # Check for 404
        title_text = (soup.title.string if soup.title else "").lower()
        if "page not found" in title_text or "doesn't exist" in title_text:
            last_result = ScrapeResult.fail("http_404", "Listing not found", html, "ebay", url)
            break

        err_hdr = soup.select_one("p.error-header-v2__title")
        if err_hdr:
            err_txt = err_hdr.get_text(strip=True)
            if err_txt:
                last_result = ScrapeResult.fail("ebay_page_error", err_txt, html, "ebay", url)
                continue

        # Validate listing — log details to help diagnose failures
        valid_listing = EbayParser.is_valid_listing(soup, html)
        extracted_price = EbayParser.extract_price(soup, html)
        extracted_title = EbayParser.extract_title(soup)

        logger.debug(
            "Listing check — valid=%s price=%s title=%r",
            valid_listing, extracted_price, extracted_title,
        )

        if not valid_listing and extracted_price is None:
            last_result = ScrapeResult.fail(
                "not_listing", "Page is not a valid listing", html, "ebay", url
            )
            continue

        listing_type = EbayParser.detect_listing_type(soup, html)
        listing_title = extracted_title

        if listing_type == "ended":
            result = ScrapeResult.ok(price=None, stock=0, title=listing_title, listing_type="ended")
            logger.info(
                "eBay listing ended for %s (title=%r)",
                url[:80],
                (listing_title or "")[:120],
            )
            return result.to_legacy()

        price = extracted_price  # already computed above, don't call twice
        stock = EbayParser.extract_stock(soup, html)

        if price is None:
            last_result = ScrapeResult.fail("no_price", "Price not found", html, "ebay", url)
            logger.warning(
                "Price extraction failed for %s — check selectors. "
                "HTML snippet: %s",
                url, html[5000:7000],   # print middle of page where price usually lives
            )
            continue

        price_out = float(price) if price is not None else None
        stock_out = int(stock) if stock is not None else None
        result = ScrapeResult.ok(
            price=price_out, stock=stock_out, title=listing_title, listing_type=listing_type
        )
        logger.info(
            "eBay scrape OK for %s (price=%s stock=%s title=%r)",
            url[:80],
            price_out,
            stock_out,
            (listing_title or "")[:120],
        )
        return result.to_legacy()

    # All retries exhausted
    if last_result:
        logger.warning(
            "eBay scrape failed after %d attempts: url=%s code=%s msg=%s",
            RETRY_LIMIT, url, last_result.error_code, last_result.error_message,
        )
    return {"price": None, "stock": None, "title": None}
