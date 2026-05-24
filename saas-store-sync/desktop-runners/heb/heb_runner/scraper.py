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
]
HEB_PRICE_WRAP_SELECTORS = [
    ".ProductPrice_wrap__bp_Iq",
    "[class*='ProductPrice_wrap']",
]
HEB_PRICE_IN_WRAP = "span[data-component='product-price-sale-price']"
HEB_PRICE_SELECTORS = [
    ".ProductPrice_wrap__bp_Iq span[data-component='product-price-sale-price']",
    "[class*='ProductPrice_wrap'] span[data-component='product-price-sale-price']",
]
HEB_ADD_TO_CART_SELECTORS = [
    ".eTxoqw [data-testid='logged-out-add-to-cart'] span[data-component='button-label']",
    "[data-testid='logged-out-add-to-cart'] span[data-component='button-label']",
    "[data-testid*='add-to-cart'] span[data-component='button-label']",
]
HEB_ADD_TO_CART_LABEL = "add to cart"

HEB_MODAL_CLOSE_SELECTORS = [
    "button[data-qe-id='modalClose']",
    "button[aria-label='Close Modal']",
    "[class*='ModalClose_button']",
    "[class*='WhatsNewModal'] button[aria-label='Close Modal']",
    "[data-component='modal-content'] button[data-component='button']",
]

# PDP price hydrates after h1; __NEXT_DATA__ sale price is often stale vs the UI.
DOM_WAIT_SEC = 10.0

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
        # Block images/fonts only — blocking CSS breaks React price hydration on PDP.
        driver.execute_cdp_cmd(
            "Network.setBlockedURLs",
            {"urls": ["*.jpg", "*.jpeg", "*.png", "*.gif", "*.webp", "*.woff", "*.woff2"]},
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
    _dismiss_overlays(driver)


def _dismiss_overlays(driver) -> None:
    """Close HEB promo / what's-new modals that block the PDP."""
    time.sleep(0.5)
    for sel in HEB_MODAL_CLOSE_SELECTORS:
        try:
            for btn in driver.find_elements(By.CSS_SELECTOR, sel):
                if not btn.is_displayed():
                    continue
                aria = (btn.get_attribute("aria-label") or "").lower()
                qe_id = btn.get_attribute("data-qe-id") or ""
                if qe_id == "modalClose" or "close" in aria:
                    btn.click()
                    time.sleep(0.6)
                    return
        except Exception:
            continue
    # Escape key fallback for dialog role modals
    try:
        from selenium.webdriver.common.keys import Keys

        body = driver.find_element(By.TAG_NAME, "body")
        if "modal-open" in (body.get_attribute("class") or ""):
            body.send_keys(Keys.ESCAPE)
            time.sleep(0.4)
    except Exception:
        pass


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


def _first_visible_text(driver, selectors: list[str], wait: float = DOM_WAIT_SEC) -> str:
    for sel in selectors:
        try:
            WebDriverWait(driver, wait).until(
                EC.visibility_of_element_located((By.CSS_SELECTOR, sel))
            )
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                if not el.is_displayed():
                    continue
                text = (el.get_attribute("textContent") or el.text or "").strip()
                if text:
                    return text
        except Exception:
            continue
    return ""


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


def _read_price_in_wrap(driver) -> Optional[float]:
    """Read sale price only inside the ProductPrice_wrap block (matches PDP UI)."""
    for wrap_sel in HEB_PRICE_WRAP_SELECTORS:
        try:
            wraps = driver.find_elements(By.CSS_SELECTOR, wrap_sel)
        except Exception:
            continue
        for wrap in wraps:
            try:
                if not wrap.is_displayed():
                    continue
                spans = wrap.find_elements(By.CSS_SELECTOR, HEB_PRICE_IN_WRAP)
                for span in spans:
                    if not span.is_displayed():
                        continue
                    text = (span.get_attribute("textContent") or span.text or "").strip()
                    price = _parse_price_text(text)
                    if price is not None:
                        return price
            except Exception:
                continue
    return None


def _price_from_html(html: str) -> Optional[float]:
    if not html:
        return None
    wrap_match = re.search(
        r'<div[^>]*class=["\'][^"\']*ProductPrice_wrap[^"\']*["\'][^>]*>(.*?)</div>',
        html,
        re.IGNORECASE | re.DOTALL,
    )
    chunk = wrap_match.group(1) if wrap_match else ""
    if not chunk:
        return None
    for m in re.finditer(
        r'<span[^>]*data-component=["\']product-price-sale-price["\'][^>]*>(.*?)</span>',
        chunk,
        re.IGNORECASE | re.DOTALL,
    ):
        inner = re.sub(r"<[^>]+>", " ", m.group(1))
        price = _parse_price_text(inner)
        if price is not None:
            return price
    return None


def _price_from_dom(driver, html: str) -> Optional[float]:
    """Poll until React hydrates — stale SSR price can differ from final UI price."""
    try:
        WebDriverWait(driver, DOM_WAIT_SEC).until(
            EC.visibility_of_element_located(
                (By.CSS_SELECTOR, HEB_PRICE_WRAP_SELECTORS[0])
            )
        )
    except Exception:
        try:
            WebDriverWait(driver, 3).until(
                EC.visibility_of_element_located(
                    (By.CSS_SELECTOR, HEB_PRICE_WRAP_SELECTORS[1])
                )
            )
        except Exception:
            pass

    deadline = time.time() + DOM_WAIT_SEC
    last_seen: Optional[float] = None
    while time.time() < deadline:
        current = _read_price_in_wrap(driver)
        if current is not None:
            last_seen = current
        time.sleep(0.5)

    if last_seen is not None:
        return last_seen
    return _price_from_html(html)


def _add_to_cart_visible(driver) -> bool:
    needle = HEB_ADD_TO_CART_LABEL
    for sel in HEB_ADD_TO_CART_SELECTORS:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                if not el.is_displayed():
                    continue
                text = (el.get_attribute("textContent") or el.text or "").strip().lower()
                if needle in text:
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


def scrape_url(driver, url: str) -> dict[str, Any]:
    last_err = "unknown"
    for attempt in range(MAX_RETRIES):
        try:
            if attempt:
                time.sleep(random.uniform(1.0, 2.5))
            driver.get(url)
            time.sleep(random.uniform(1.0, 1.5))
            _dismiss_overlays(driver)
            time.sleep(random.uniform(0.5, 1.0))

            blocked, reason = _page_blocked(driver)
            if blocked:
                last_err = reason
                continue

            html = driver.page_source or ""
            nd = _parse_next_data(html)

            title = _first_visible_text(driver, HEB_TITLE_SELECTORS, wait=DOM_WAIT_SEC)
            if not title and nd:
                title = _dig_title(nd) or ""

            # Sale price from visible PDP UI only — never __NEXT_DATA__ (often wrong).
            price = _price_from_dom(driver, html)
            if price is None:
                time.sleep(1.5)
                html = driver.page_source or ""
                price = _price_from_dom(driver, html)

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
