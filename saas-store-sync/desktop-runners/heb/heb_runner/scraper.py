from __future__ import annotations

import json
import random
import re
import sys
import time
from typing import Any, Optional

import undetected_chromedriver as uc
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .config import (
    CHROME_VERSION_MAIN,
    COOKIES_FILE,
    HEB_HOME,
    HEADLESS,
    MAX_RETRIES,
    URL_TIMEOUT_SEC,
)
from .cookies import inject_cookies, load_cookies

HEB_TITLE_SELECTORS = [
    "h1",
    "h1[data-testid*='title']",
    "[data-testid='product-title']",
]
HEB_PRICE_SELECTORS = [
    "[data-component='product-price-sale-price']",
    "span.sc-659eeebc-1.ljptds",
    "[data-testid*='price']",
    "span[class*='price' i]",
    "meta[itemprop='price']",
]
HEB_ADD_TO_CART_SELECTORS = [
    "div.AddToCartButton_layout__XhhQg",
    "[class*='AddToCartButton_layout']",
    "button[data-testid*='cart']",
]

BLOCK_INDICATORS = (
    "pardon our interruption",
    "access denied",
    "please verify you are human",
    "cloudflare",
    "just a moment",
    "checking your browser",
)


def _safe_quit(driver) -> None:
    if driver is None:
        return
    try:
        driver.quit()
    except OSError:
        pass
    except Exception:
        pass


def _chrome_major() -> Optional[int]:
    if CHROME_VERSION_MAIN:
        try:
            return int(CHROME_VERSION_MAIN)
        except ValueError:
            pass
    if sys.platform != "win32":
        return None
    try:
        import winreg

        for hive, path in (
            (winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Google\Chrome\BLBeacon"),
        ):
            try:
                with winreg.OpenKey(hive, path) as key:
                    ver, _ = winreg.QueryValueEx(key, "version")
                major = int(str(ver).split(".")[0])
                if major > 0:
                    return major
            except OSError:
                continue
    except Exception:
        pass
    return None


def create_driver():
    opts = uc.ChromeOptions()
    if HEADLESS:
        opts.add_argument("--headless=new")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--window-size=1366,900")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--blink-settings=imagesEnabled=false")

    kwargs: dict[str, Any] = {"options": opts, "use_subprocess": True}
    major = _chrome_major()
    if major:
        kwargs["version_main"] = major

    driver = uc.Chrome(**kwargs)
    driver.set_page_load_timeout(URL_TIMEOUT_SEC)
    try:
        driver.execute_cdp_cmd("Network.enable", {})
        driver.execute_cdp_cmd(
            "Network.setBlockedURLs",
            {"urls": ["*.css", "*.jpg", "*.jpeg", "*.png", "*.gif", "*.woff", "*.woff2"]},
        )
    except Exception:
        pass
    return driver


def warm_session(driver) -> None:
    cookies = load_cookies(COOKIES_FILE)
    driver.get(HEB_HOME)
    time.sleep(1.0)
    n = inject_cookies(driver, cookies)
    if n == 0:
        raise RuntimeError(f"No HEB cookies loaded from {COOKIES_FILE}")
    driver.get(HEB_HOME)
    time.sleep(1.5)
    print(f"Session warmed with {n} cookies")


def _page_blocked(driver) -> tuple[bool, str]:
    try:
        title = (driver.title or "").lower()
    except Exception:
        title = ""
    for needle in BLOCK_INDICATORS:
        if needle in title:
            return True, f"blocked_title:{needle}"
    try:
        src = (driver.page_source or "")[:8000].lower()
    except Exception:
        src = ""
    for needle in BLOCK_INDICATORS:
        if needle in src:
            return True, f"blocked_body:{needle}"
    return False, ""


def _parse_next_data(html: str) -> Optional[dict]:
    m = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html or "",
        re.DOTALL | re.IGNORECASE,
    )
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _dig_price(data: dict) -> Optional[float]:
    paths = [
        lambda d: d["props"]["pageProps"]["pdpData"]["product"]["price"]["value"],
        lambda d: d["props"]["pageProps"]["product"]["price"]["value"],
    ]
    for fn in paths:
        try:
            val = fn(data)
            if val is not None:
                return float(val)
        except (KeyError, TypeError, ValueError):
            continue
    return None


def _dig_title(data: dict) -> Optional[str]:
    paths = [
        lambda d: d["props"]["pageProps"]["pdpData"]["product"]["name"],
        lambda d: d["props"]["pageProps"]["product"]["name"],
    ]
    for fn in paths:
        try:
            val = fn(data)
            if val:
                return str(val).strip()
        except (KeyError, TypeError):
            continue
    return None


def _first_text(driver, selectors: list[str], wait: float = 2.0) -> str:
    for sel in selectors:
        try:
            WebDriverWait(driver, wait).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, sel))
            )
            el = driver.find_element(By.CSS_SELECTOR, sel)
            text = (el.get_attribute("textContent") or el.text or "").strip()
            if text:
                return text
        except Exception:
            continue
    return ""


def _add_to_cart_visible(driver) -> bool:
    for sel in HEB_ADD_TO_CART_SELECTORS:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                if el.is_displayed():
                    return True
        except Exception:
            continue
    return False


def _stock_from_page(driver, price: Optional[float]) -> int:
    if price is None:
        return 0
    if _add_to_cart_visible(driver):
        return 3
    text = (driver.page_source or "").lower()
    if any(x in text for x in ("out of stock", "unavailable", "not available")):
        return 0
    return 3 if price is not None else 0


def _parse_price_text(raw: str) -> Optional[float]:
    if not raw:
        return None
    m = re.search(r"\$?\s*(\d+\.\d{2})", raw.replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def scrape_url(driver, url: str) -> dict[str, Any]:
    last_err = "unknown"
    for attempt in range(MAX_RETRIES):
        try:
            if attempt:
                time.sleep(random.uniform(1.0, 2.5))
            driver.get(url)
            time.sleep(random.uniform(0.8, 1.5))

            blocked, reason = _page_blocked(driver)
            if blocked:
                last_err = reason
                continue

            html = driver.page_source or ""
            nd = _parse_next_data(html)
            title = _dig_title(nd) if nd else ""
            price = _dig_price(nd) if nd else None

            if not title:
                title = _first_text(driver, HEB_TITLE_SELECTORS)
            if price is None:
                raw = _first_text(driver, HEB_PRICE_SELECTORS)
                price = _parse_price_text(raw)

            if not title or price is None:
                blocked, reason = _page_blocked(driver)
                if blocked:
                    last_err = reason
                else:
                    last_err = "no_price_or_title"
                continue

            stock = _stock_from_page(driver, price)
            return {
                "url": url,
                "price": price,
                "stock": stock,
                "title": title[:500],
            }
        except (TimeoutException, WebDriverException) as exc:
            last_err = f"fetch_error:{type(exc).__name__}"
        except Exception as exc:
            last_err = f"error:{exc}"

    return {
        "url": url,
        "price": None,
        "stock": None,
        "title": None,
        "error_code": last_err[:50],
        "error_message": last_err,
    }


class HebBrowserSession:
    """One Chrome session for many PDP URLs."""

    def __init__(self) -> None:
        self.driver = None

    def start(self) -> None:
        self.driver = create_driver()
        warm_session(self.driver)

    def close(self) -> None:
        _safe_quit(self.driver)
        self.driver = None

    def scrape(self, url: str) -> dict[str, Any]:
        if not self.driver:
            raise RuntimeError("Browser session not started")
        return scrape_url(self.driver, url)
