"""
HEB product scraper (optimized Selenium flow).

Goals:
- Fast page load and extraction (minimal sleeps, explicit waits)
- Reuse one driver across rows in the same sync run
- Early block/captcha detection
- Return the same shape used by the app: {"price": float|None, "stock": int|None, "title": str|None}
"""
import logging
import re
from typing import Optional

from bs4 import BeautifulSoup

from .core import ScrapeResult, detect_block, parse_price_text, random_delay

logger = logging.getLogger("scrapers.heb")

RETRY_LIMIT = 2
PAGE_TIMEOUT = 18


class HebParser:
    TITLE_SELECTORS = (
        "h1[data-testid*='title']",
        "h1.product-title",
        "h1",
        "meta[property='og:title']",
    )
    PRICE_SELECTORS = (
        "[data-testid*='price']",
        "span[class*='price']",
        ".price",
        "meta[property='product:price:amount']",
    )
    STOCK_HINTS_IN = ("in stock", "available", "add to cart")
    STOCK_HINTS_OUT = ("out of stock", "unavailable", "sold out")

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
    def extract_title(cls, soup: BeautifulSoup) -> Optional[str]:
        t = cls._select_text(soup, cls.TITLE_SELECTORS)
        return t[:500] if t else None

    @classmethod
    def extract_price(cls, soup: BeautifulSoup, html: str) -> Optional[float]:
        txt = cls._select_text(soup, cls.PRICE_SELECTORS)
        p = parse_price_text(txt)
        if p is not None:
            return p
        # Fallback regex across HTML/inline json
        for pat in (
            r'"price"\s*:\s*"?\$?(\d+(?:\.\d{2})?)',
            r'"amount"\s*:\s*"?(\d+(?:\.\d{2})?)',
            r'\$(\d+(?:\.\d{2})?)',
        ):
            m = re.search(pat, html or "", re.IGNORECASE)
            if m:
                p = parse_price_text(m.group(1))
                if p is not None:
                    return p
        return None

    @classmethod
    def extract_stock(cls, soup: BeautifulSoup, html: str) -> Optional[int]:
        text = (soup.get_text(" ", strip=True) or "").lower()
        if not text:
            text = (html or "").lower()
        if any(k in text for k in cls.STOCK_HINTS_OUT):
            return 0
        if any(k in text for k in cls.STOCK_HINTS_IN):
            return 3
        return None


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
        driver = webdriver.Chrome(options=opts)
        driver.set_page_load_timeout(PAGE_TIMEOUT)
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
    try:
        WebDriverWait(driver, 4).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
    except Exception:
        pass
    # Wait for at least one likely product signal (short wait to stay fast)
    for sel in ("h1", "[data-testid*='price']", "span[class*='price']"):
        try:
            WebDriverWait(driver, 2).until(EC.presence_of_element_located((By.CSS_SELECTOR, sel)))
            break
        except Exception:
            continue
    return driver.page_source or ""


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
                random_delay(0.5, 1.2)
            html = _fetch_html(driver, vendor_url)
            blocked, reason = detect_block(html)
            if blocked:
                last = ScrapeResult.fail(f"blocked_{reason}", f"Blocked: {reason}", html, "heb", vendor_url)
                continue
            soup = BeautifulSoup(html, "lxml")
            title = HebParser.extract_title(soup)
            price = HebParser.extract_price(soup, html)
            stock = HebParser.extract_stock(soup, html)
            if price is None:
                last = ScrapeResult.fail("no_price", "Price not found on HEB page", html, "heb", vendor_url)
                continue
            if stock is None:
                stock = 3
            return ScrapeResult.ok(price=price, stock=stock, title=title).to_legacy()
    except Exception as exc:
        logger.exception("HEB scrape exception: %s", exc)
        last = ScrapeResult.fail("exception", str(exc), "", "heb", vendor_url)
    finally:
        if created and session.get("heb_driver") is None:
            HebDriver.quit_safe(driver)
    return (last or ScrapeResult.fail("max_retries", "HEB retries exhausted", "", "heb", vendor_url)).to_legacy()


def close_heb_session(session):
    if session is None:
        return
    drv = session.pop("heb_driver", None)
    HebDriver.quit_safe(drv)

