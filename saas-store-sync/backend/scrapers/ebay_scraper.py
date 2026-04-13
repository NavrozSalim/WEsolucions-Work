"""
Hardened eBay product scraper.

Strategy:
1) HTTP-first using curl_cffi (browser TLS) if EBAY_HTTP_FIRST=1 (default 0 skips straight to browser warm).
2) If blocked/challenged, use Selenium once to warm a real browser session.
3) Export Selenium cookies + user-agent back into HTTP client.
4) Retry HTTP using warmed cookies.
5) Use Selenium DOM parse only as final fallback.

Public API:
    scrape_ebay(vendor_url, region, session=None) -> {"price": float|None, "stock": int|None, "title": str|None}
    close_ebay_session(session)
"""

import os
import re
import json
import time
import random
import logging
from typing import Optional, Tuple, Dict
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .core import (
    random_delay,
    backoff_delay,
    parse_price_text,
    classify_failure,
    should_retry_failure,
    save_debug_html,
)

logger = logging.getLogger("scrapers.ebay")

TIMEOUT_SEC = 45
EBAY_HTTP_TIMEOUT_SEC = 25
PAGE_WAIT_TIMEOUT = 25
RETRY_LIMIT = 4

PRICE_SUFFIX_PATTERN = re.compile(
    r"(or Best Offer|Buy It Now|Best Offer|Make Offer|each|/ea).*$",
    re.IGNORECASE,
)

_CHALLENGE_INDICATORS = [
    "pardon our interruption",
    "checking your browser",
    "splashui/challenge",
    "enable javascript",
    "please enable javascript",
    "enable cookies",
    "turn on javascript",
    "just a moment",
    "verify you are human",
    "robot check",
    "captcha",
    "security page",
    "access denied",
]

_BLOCK_INDICATORS = [
    "you have been blocked",
    "suspicious activity",
    "unusual traffic",
    "automated access",
    "datadome",
    "perimeterx",
    "incapsula",
]

PRODUCT_SIGNALS = [
    "x-price-primary",
    "x-bin-price",
    "itemprop=\"price\"",
    "itemprop='price'",
    "currentprice",
    "binprice",
    "pricevalue",
    "x-item-title",
    "og:title",
    "application/ld+json",
]


def _normalize_url(original_url: str, region: str) -> str:
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


def _to_ebay_ca_url(url: str) -> str:
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


def _strip_price_suffix(text: str) -> str:
    if not text:
        return ""
    return PRICE_SUFFIX_PATTERN.sub("", text).strip()


def _ebay_home_origin_for_item_url(item_url: str) -> str:
    u = (item_url or "").lower()
    if "ebay.ca" in u:
        return "https://www.ebay.ca/"
    if "ebay.com.au" in u:
        return "https://www.ebay.com.au/"
    return "https://www.ebay.com/"


def _ebay_region_referer(region: str) -> str:
    return "https://www.ebay.com.au/" if region == "AU" else "https://www.ebay.com/"


def _ebay_http_first_enabled() -> bool:
    return os.environ.get("EBAY_HTTP_FIRST", "0").lower() in ("1", "true", "yes")


def _random_user_agent() -> str:
    agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edg/131.0.0.0 Safari/537.36",
    ]
    return random.choice(agents)


def _looks_like_product_html(html: str) -> bool:
    if not html or len(html) < 7000:
        return False
    lower = html.lower()
    return any(sig in lower for sig in PRODUCT_SIGNALS)


def _is_challenge_or_blocked(content: str) -> Tuple[bool, str]:
    if not content:
        return True, "empty"
    lower = content.lower()

    if _looks_like_product_html(content):
        if "splashui/challenge" not in lower and "pardon our interruption" not in lower:
            return False, ""

    for indicator in _CHALLENGE_INDICATORS:
        if indicator in lower:
            return True, "challenge"

    for indicator in _BLOCK_INDICATORS:
        if indicator in lower:
            return True, "blocked"

    return False, ""


class EbayParser:
    PRICE_SELECTORS = [
        ".x-price-primary",
        "[data-testid='x-price-primary'] .ux-textspans--BOLD",
        "[data-testid='x-price-primary'] span",
        "[data-test-id='x-price-primary'] .ux-textspans--BOLD",
        "[data-test-id='x-price-primary'] span",
        ".x-price-primary .ux-textspans--BOLD",
        ".x-price-primary span.ux-textspans",
        ".x-price-primary span",
        "div.x-price-primary",
        ".x-bin-price__content .ux-textspans--BOLD",
        ".x-bin-price__content span",
        ".x-bin-price span",
        "[data-testid='x-bin-price'] span",
        "[data-testid='x-bin-price'] .ux-textspans--BOLD",
        "section.x-item-price span.ux-textspans--BOLD",
        "div.ux-section-module span.ux-textspans--BOLD",
        ".x-auction-price .ux-textspans--BOLD",
        ".x-auction-price span",
        ".ux-labels-values__values-content .ux-textspans--BOLD",
        "span.ux-textspans--BOLD",
        ".ux-price",
        "span[itemprop='price']",
        "#prcIsum",
        ".notranslate",
        ".display-price",
        "[data-testid='price-value']",
        ".price-current",
    ]

    PRICE_JSON_PATTERNS = [
        r'"currentPrice"\s*:\s*\{\s*"value"\s*:\s*"([\d.]+)"',
        r'"buyItNowPrice"\s*:\s*\{\s*"value"\s*:\s*"([\d.]+)"',
        r'"convertedPrice"\s*:\s*\{\s*"value"\s*:\s*"([\d.]+)"',
        r'"binPrice"\s*:\s*\{\s*"value"\s*:\s*"([\d.]+)"',
        r'"price"\s*:\s*\[\s*"([\d.]+)"\s*\]',
        r'"price"\s*:\s*"([\d.]+)"',
        r'"value"\s*:\s*"([\d.]+)"\s*,\s*"currency"',
        r'"priceValue"\s*:\s*"([\d.]+)"',
        r'"finalPrice"\s*:\s*\{\s*"value"\s*:\s*([\d.]+)',
        r'"transactionAmount"\s*:\s*\{\s*"value"\s*:\s*([\d.]+)',
    ]

    QUANTITY_PATTERN = re.compile(r'"NumberValidation","minValue":"(\d+)","maxValue":"(\d+)"')

    STATUS_ENDED_PHRASES = (
        "listing has ended",
        "bidding has ended",
        "out of stock",
        "no longer available",
        "sold out",
        "this item is out of stock",
        "was ended",
    )

    TITLE_SELECTORS = [
        ".x-item-title__mainTitle span.ux-textspans",
        ".x-item-title__mainTitle span",
        "h1.x-item-title",
        "[data-testid='x-item-title'] span",
        "[data-testid='x-item-title']",
        "h1#itemTitle",
        "[data-test-id='x-item-title']",
        "h1#x-item-title",
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

        if soup.title and soup.title.string:
            raw = soup.title.string.strip()
            for suffix in (" | eBay", " | eBay.com", " on eBay"):
                if raw.lower().endswith(suffix.lower()):
                    raw = raw[: -len(suffix)].strip()
            if raw and len(raw) > 2:
                return raw[:500]

        return None

    @classmethod
    def is_valid_listing(cls, soup: BeautifulSoup, html: str = "") -> bool:
        if cls.extract_title(soup):
            return True
        lower = (html or "").lower()
        if "itemprop" in lower and ("product" in lower or "offers" in lower):
            return True
        if "/itm/" in lower and len(lower) > 15000:
            return True
        return False

    @classmethod
    def detect_listing_type(cls, soup: BeautifulSoup, html: str) -> str:
        lower_html = html.lower()

        err_hdr = soup.select_one("p.error-header-v2__title")
        if err_hdr:
            et = err_hdr.get_text(strip=True).lower()
            if any(x in et for x in ("ended", "removed", "unavailable", "not available", "no longer", "sold out")):
                return "ended"

        status_el = soup.select_one(".ux-layout-section__textual-display--statusMessage span")
        if status_el:
            st = status_el.get_text(strip=True).lower()
            if any(p in st for p in cls.STATUS_ENDED_PHRASES):
                return "ended"

        if any(ind in lower_html for ind in ("this listing has ended", "bidding has ended", "this item is out of stock")):
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
                text = _strip_price_suffix(elem.get_text(strip=True))
                if not text:
                    continue

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
                        return p
            except Exception:
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

        m = re.search(r"more than (\d+) available", text)
        if m:
            return int(m.group(1))

        m = re.search(r"(\d+)\s*available", text)
        if m:
            return int(m.group(1))

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
            got = cls._stock_from_availability_text(stock_el.get_text(strip=True))
            if got is not None:
                return got

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
                offers = data.get("offers", {})
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                avail = offers.get("availability", "")
                if "OutOfStock" in avail or "Discontinued" in avail:
                    return 0
                if "InStock" in avail or "LimitedAvailability" in avail:
                    return 99
            except Exception:
                continue

        return None


class EbayHTTP:
    @staticmethod
    def _get_client(session_dict: dict):
        client = session_dict.get("ebay_http_client") if session_dict else None
        if client:
            return client

        try:
            from curl_cffi import requests as curl_requests

            client = curl_requests.Session()
            if session_dict is not None:
                session_dict["ebay_http_client"] = client
            return client
        except Exception:
            logger.debug("curl_cffi unavailable for eBay HTTP session")
            return None

    @staticmethod
    def _get_headers(url: str, region: str, session_dict: dict) -> Dict[str, str]:
        ua = None
        if session_dict is not None:
            ua = session_dict.get("ebay_last_user_agent")
        if not ua:
            ua = _random_user_agent()
            if session_dict is not None:
                session_dict["ebay_last_user_agent"] = ua

        return {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": _ebay_region_referer(region),
            "Cache-Control": "max-age=0",
            "Pragma": "no-cache",
            "Upgrade-Insecure-Requests": "1",
        }

    @classmethod
    def fetch(cls, url: str, region: str, session_dict: dict) -> Tuple[Optional[str], Optional[int], str]:
        client = cls._get_client(session_dict)
        if client is None:
            return None, None, "curl_cffi_not_installed"

        headers = cls._get_headers(url, region, session_dict)

        try:
            resp = client.get(
                url,
                headers=headers,
                timeout=EBAY_HTTP_TIMEOUT_SEC,
                allow_redirects=True,
                impersonate="chrome131",
            )
        except Exception as exc:
            return None, None, f"http_error: {exc}"

        html = getattr(resp, "text", "") or ""
        status = getattr(resp, "status_code", None)

        if session_dict is not None:
            session_dict["ebay_last_http_url"] = getattr(resp, "url", url)

        if status != 200:
            return html, status, f"http_{status}"

        blocked, reason = _is_challenge_or_blocked(html)
        if blocked:
            return html, status, reason

        if not _looks_like_product_html(html):
            return html, status, "not_product_like"

        return html, status, ""

    @classmethod
    def import_cookies_from_selenium(cls, driver, region: str, session_dict: dict):
        client = cls._get_client(session_dict)
        if client is None:
            return

        try:
            cookies = driver.get_cookies()
        except Exception as exc:
            logger.debug("Could not export Selenium cookies: %s", exc)
            return

        for c in cookies:
            try:
                name = c.get("name")
                value = c.get("value")
                domain = c.get("domain")
                path = c.get("path", "/")
                if not name or value is None:
                    continue
                client.cookies.set(name, value, domain=domain, path=path)
            except Exception:
                continue

        try:
            ua = driver.execute_script("return navigator.userAgent")
            if ua and session_dict is not None:
                session_dict["ebay_last_user_agent"] = ua
        except Exception:
            pass


class EbayDriver:
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
        options.add_argument("--ignore-certificate-errors")
        options.add_argument("--lang=en-US,en")

        width = random.randint(1600, 1920)
        height = random.randint(900, 1080)
        options.add_argument(f"--window-size={width},{height}")

        ua = _random_user_agent()
        options.add_argument(f"--user-agent={ua}")

        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

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
        """Close Selenium driver only (use close_ebay_session for full teardown)."""
        key = "ebay_selenium_driver"
        if session_dict is None:
            return
        driver = session_dict.pop(key, None)
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


class EbayBrowserSession:
    @staticmethod
    def _wait_until_product_or_stable_challenge(driver, timeout: int = PAGE_WAIT_TIMEOUT) -> str:
        from selenium.webdriver.common.by import By
        start = time.time()
        last_html = ""

        product_locators = [
            (By.CSS_SELECTOR, "[data-testid='x-price-primary']"),
            (By.CSS_SELECTOR, "[data-testid='x-bin-price']"),
            (By.CSS_SELECTOR, ".x-price-primary"),
            (By.CSS_SELECTOR, ".x-bin-price"),
            (By.CSS_SELECTOR, "h1.x-item-title"),
            (By.CSS_SELECTOR, "[data-testid='x-item-title']"),
            (By.CSS_SELECTOR, "span[itemprop='price']"),
            (By.CSS_SELECTOR, "meta[itemprop='price'][content]"),
        ]

        while time.time() - start < timeout:
            try:
                for locator in product_locators:
                    elems = driver.find_elements(*locator)
                    if elems:
                        html = driver.page_source
                        if _looks_like_product_html(html):
                            return html
            except Exception:
                pass

            try:
                html = driver.page_source
                last_html = html
                blocked, _ = _is_challenge_or_blocked(html)
                if not blocked and _looks_like_product_html(html):
                    return html
            except Exception:
                pass

            time.sleep(1.5)

        return last_html

    @classmethod
    def warm_and_fetch(cls, url: str, region: str, session_dict: dict) -> Tuple[Optional[str], str]:
        try:
            driver = EbayDriver.get_or_create(session_dict)
        except Exception as exc:
            return None, f"selenium_init: {exc}"

        try:
            home = _ebay_home_origin_for_item_url(url)
            driver.get(home)
            time.sleep(1.5 + random.uniform(0.5, 1.5))

            driver.get(url)
            html = cls._wait_until_product_or_stable_challenge(driver, timeout=PAGE_WAIT_TIMEOUT)

            current_url = ""
            try:
                current_url = driver.current_url
            except Exception:
                pass

            if session_dict is not None:
                session_dict["ebay_last_browser_url"] = current_url

            EbayHTTP.import_cookies_from_selenium(driver, region, session_dict)

            if not html:
                return None, "empty_browser_html"

            blocked, reason = _is_challenge_or_blocked(html)
            if blocked and "splashui/challenge" in current_url.lower():
                return html, f"browser_{reason}"

            return html, ""

        except Exception as exc:
            return None, f"selenium_error: {exc}"


def _parse_html_to_result(html: str, url: str) -> Optional[dict]:
    if not html:
        return None

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    title_text = (soup.title.string if soup.title else "").lower()
    if "page not found" in title_text or "doesn't exist" in title_text:
        return {"price": None, "stock": None, "title": None}

    err_hdr = soup.select_one("p.error-header-v2__title")
    if err_hdr:
        err_txt = err_hdr.get_text(strip=True).lower()
        if any(x in err_txt for x in ("ended", "removed", "unavailable", "sold out", "no longer")):
            listing_title = EbayParser.extract_title(soup)
            return {"price": None, "stock": 0, "title": listing_title}

    valid_listing = EbayParser.is_valid_listing(soup, html)
    price = EbayParser.extract_price(soup, html)
    title = EbayParser.extract_title(soup)

    if not valid_listing and price is None:
        return None

    listing_type = EbayParser.detect_listing_type(soup, html)
    if listing_type == "ended":
        return {"price": None, "stock": 0, "title": title}

    stock = EbayParser.extract_stock(soup, html)

    if price is None:
        return None

    return {
        "price": float(price) if price is not None else None,
        "stock": int(stock) if stock is not None else None,
        "title": title,
    }


def scrape_ebay(vendor_url: str, region: str, session: dict = None) -> dict:
    if session is None:
        session = {}

    url = _normalize_url(vendor_url, region)
    ca_url = _to_ebay_ca_url(url)
    candidate_urls = [url]
    if ca_url != url:
        candidate_urls.append(ca_url)

    random_delay(0.4, 1.2)
    last_error = None
    last_browser_html = None

    for attempt in range(RETRY_LIMIT):
        if attempt > 0:
            backoff_delay(attempt, base=2.0, jitter=1.5)

        try_urls = candidate_urls if attempt % 2 == 0 else list(reversed(candidate_urls))

        for candidate in try_urls:
            logger.info("eBay scrape attempt=%s url=%s", attempt + 1, candidate)

            if _ebay_http_first_enabled():
                html, status, err = EbayHTTP.fetch(candidate, region, session)
            else:
                html, status, err = None, None, "http_skipped"

            if html and not err:
                parsed = _parse_html_to_result(html, candidate)
                if parsed is not None:
                    logger.info("eBay HTTP success for %s", candidate)
                    return parsed

            browser_html, browser_err = EbayBrowserSession.warm_and_fetch(candidate, region, session)
            if browser_html:
                last_browser_html = browser_html

            html2, status2, err2 = EbayHTTP.fetch(candidate, region, session)
            if html2 and not err2:
                parsed = _parse_html_to_result(html2, candidate)
                if parsed is not None:
                    logger.info("eBay cookie-handoff HTTP success for %s", candidate)
                    return parsed

            if browser_html:
                parsed = _parse_html_to_result(browser_html, candidate)
                if parsed is not None:
                    logger.info("eBay Selenium HTML success for %s", candidate)
                    return parsed

            parts = [err2, browser_err, err]
            if status is not None:
                parts.append(f"http_{status}")
            if status2 is not None:
                parts.append(f"http_{status2}")
            last_error = next((p for p in parts if p), "unknown_error")

            if last_error and last_error.startswith("http_"):
                try:
                    status_val = int(last_error.split("_", 1)[1])
                except Exception:
                    status_val = None
                if status_val is not None:
                    last_error = classify_failure(status_val, browser_html or html2 or html or "")

            if not should_retry_failure(last_error):
                break

            if len(candidate_urls) > 1:
                try:
                    current_browser_url = session.get("ebay_last_browser_url", "") if session else ""
                    if current_browser_url and urlparse(current_browser_url).netloc != urlparse(candidate).netloc:
                        EbayDriver.close(session)
                except Exception:
                    pass

    logger.warning("eBay scrape failed for %s, last_error=%s", url, last_error)

    if last_browser_html:
        save_debug_html(last_browser_html, "ebay", url, last_error or "unknown")

    return {"price": None, "stock": None, "title": None}


def close_ebay_session(session: dict):
    if session is None:
        return

    EbayDriver.close(session)

    client = session.pop("ebay_http_client", None)
    if client:
        try:
            client.close()
        except Exception:
            pass

    session.pop("ebay_last_user_agent", None)
    session.pop("ebay_last_http_url", None)
    session.pop("ebay_last_browser_url", None)
    session.pop("ebay_last_failed_html", None)
