"""
Amazon legacy / AU scraper.

Primary: requests + BeautifulSoup (fast).
Fallback: Selenium headless Chrome (when HTTP is blocked or price not found).

Handles amazon.com.au and other non-US Amazon domains.

Public API:
  scrape_amazon(vendor_url, region, session) -> {"price": float|None, "stock": int|None, "title": str|None}
  close_amazon_session(session)
"""
import logging

import requests
from bs4 import BeautifulSoup

from .core import (
    ScrapeResult, detect_block, is_amazon_captcha_page, is_amazon_dog_page,
    save_debug_html, random_delay, backoff_delay, parse_price_text,
    get_random_headers, USER_AGENTS,
)
from .amazon_us_scraper import AmazonDriver, AmazonParser, AmazonScraper

logger = logging.getLogger("scrapers.amazon_legacy")

RETRY_LIMIT = 3
FETCH_TIMEOUT = 30

_AU_COOKIES = {
    "i18n-prefs": "AUD",
    "lc-main": "en_AU",
}

_USD_COOKIES = {
    "i18n-prefs": "USD",
    "lc-main": "en_US",
}


# ═══════════════════════════════════════════════════════════════════════════
# HTTP-based scraper (primary)
# ═══════════════════════════════════════════════════════════════════════════

class AmazonLegacyHTTP:
    """Fast requests-based scraper for non-US Amazon domains."""

    _ZIP_CHANGE_URLS = {
        False: "https://www.amazon.com/gp/delivery/ajax/address-change.html",
        True: "https://www.amazon.com.au/gp/delivery/ajax/address-change.html",
    }

    @staticmethod
    def _get_session(session_dict: dict, is_au: bool) -> requests.Session:
        key = "amazon_legacy_http_session"
        if session_dict is not None and key in session_dict:
            return session_dict[key]
        s = requests.Session()
        domain = "amazon.com.au" if is_au else "amazon.com"
        s.headers.update(get_random_headers(f"https://www.{domain}/"))
        s.cookies.update(_AU_COOKIES if is_au else _USD_COOKIES)
        if session_dict is not None:
            session_dict[key] = s
        return s

    @classmethod
    def _ensure_zip(cls, s: requests.Session, seed_url: str, session_dict: dict, is_au: bool):
        """Set delivery location for accurate pricing."""
        zip_key = "amazon_legacy_http_zip_set"
        if session_dict is not None and session_dict.get(zip_key):
            return
        zip_code = "3000" if is_au else "10001"
        endpoint = cls._ZIP_CHANGE_URLS.get(is_au, cls._ZIP_CHANGE_URLS[False])
        try:
            s.get(seed_url, timeout=FETCH_TIMEOUT)
            resp = s.post(
                endpoint,
                data={
                    "locationType": "LOCATION_INPUT",
                    "zipCode": zip_code,
                    "storeContext": "generic",
                    "deviceType": "web",
                    "pageType": "Detail",
                    "actionSource": "glow",
                },
                headers={
                    "x-requested-with": "XMLHttpRequest",
                    "referer": seed_url,
                },
                timeout=FETCH_TIMEOUT,
            )
            ok = resp.status_code == 200 and resp.json().get("isAddressUpdated")
            if ok:
                logger.info("Legacy HTTP session ZIP set to %s", zip_code)
        except Exception as exc:
            logger.warning("Failed to set legacy ZIP via HTTP: %s", exc)
            ok = False
        if session_dict is not None:
            session_dict[zip_key] = ok

    @classmethod
    def fetch(cls, url: str, session_dict: dict = None, is_au: bool = False) -> ScrapeResult:
        s = cls._get_session(session_dict, is_au)
        cls._ensure_zip(s, url, session_dict, is_au)
        vendor = "amazon_au" if is_au else "amazon_legacy"
        try:
            resp = s.get(url, timeout=FETCH_TIMEOUT, allow_redirects=True)
        except requests.Timeout:
            return ScrapeResult.fail("timeout", "HTTP timeout", "", vendor, url)
        except requests.ConnectionError as exc:
            return ScrapeResult.fail("connection_error", str(exc), "", vendor, url)
        except requests.RequestException as exc:
            return ScrapeResult.fail("request_error", str(exc), "", vendor, url)

        html = resp.text
        if resp.status_code != 200:
            return ScrapeResult.fail(f"http_{resp.status_code}", f"HTTP {resp.status_code}", html, vendor, url)

        blocked, reason = detect_block(html)
        if blocked:
            return ScrapeResult.fail(f"blocked_{reason}", f"Blocked: {reason}", html, vendor, url)

        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")

        price = AmazonParser.extract_price(soup, html)
        stock = AmazonParser.extract_stock(soup)
        title = AmazonParser.extract_title(soup)

        if price is None:
            return ScrapeResult.fail("no_price", "Price not found via HTTP", html, vendor, url)

        if stock is None and price is not None:
            stock = 2

        return ScrapeResult.ok(price=price, stock=stock, title=title)

    @classmethod
    def scrape_with_retry(cls, url: str, session_dict: dict = None, is_au: bool = False) -> ScrapeResult:
        last_result = None
        for attempt in range(RETRY_LIMIT):
            if attempt > 0:
                backoff_delay(attempt, base=2.0, jitter=1.5)
            result = cls.fetch(url, session_dict, is_au)
            if result.success:
                return result
            last_result = result
            if result.error_code in ("http_404",):
                break
            if result.error_code.startswith("blocked"):
                s = cls._get_session(session_dict, is_au)
                domain = "amazon.com.au" if is_au else "amazon.com"
                s.headers.update(get_random_headers(f"https://www.{domain}/"))
        return last_result or ScrapeResult.fail("max_retries", "All HTTP attempts failed", "", "amazon_legacy", url)


# ═══════════════════════════════════════════════════════════════════════════
# Selenium fallback (AU location setup)
# ═══════════════════════════════════════════════════════════════════════════

def _ensure_au_location(driver, session: dict = None) -> bool:
    if session and session.get("amazon_au_location_set"):
        return True
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        driver.get("https://www.amazon.com.au")
        random_delay(2, 4)
        AmazonScraper.solve_captcha(driver)

        wait = WebDriverWait(driver, 8)
        loc_selectors = [
            "a#nav-global-location-popover-link",
            "a[data-csa-c-content-id='nav_cs_gb_td_address']",
            "span#nav-global-location-slot",
        ]
        clicked = False
        for sel in loc_selectors:
            try:
                trigger = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, sel)))
                trigger.click()
                clicked = True
                break
            except Exception:
                continue

        if not clicked:
            driver.get("https://www.amazon.com.au/gp/delivery/ajax/address-change.html")
        random_delay(2, 3)

        zip_selectors = ["input#GLUXZipUpdateInput", "input.GLUX_Full_Width"]
        postal_input = None
        for sel in zip_selectors:
            try:
                postal_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, sel)))
                break
            except Exception:
                continue

        if postal_input:
            postal_input.clear()
            postal_input.send_keys("3000")
            random_delay(0.5, 1.5)
            for sel in [
                "input#GLUXZipUpdate[type='submit']",
                "span#GLUXZipUpdate input",
            ]:
                try:
                    btn = driver.find_element(By.CSS_SELECTOR, sel)
                    btn.click()
                    break
                except Exception:
                    continue
            else:
                postal_input.send_keys(Keys.RETURN)
            random_delay(2, 3)
            AmazonScraper.solve_captcha(driver)

        if session is not None:
            session["amazon_au_location_set"] = True
        return True
    except Exception:
        return False


def _selenium_scrape_page(url: str, driver) -> ScrapeResult:
    import time
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    try:
        driver.get(url)
        random_delay(3, 5)
        AmazonScraper.solve_captcha(driver)

        for sel in ["span.a-price", "#priceblock_ourprice", ".apexPriceToPay", "#availability"]:
            try:
                WebDriverWait(driver, 8).until(EC.presence_of_element_located((By.CSS_SELECTOR, sel)))
                break
            except Exception:
                continue
        time.sleep(1.5)

        html = driver.page_source

        if is_amazon_captcha_page(html):
            solved = AmazonScraper.solve_captcha(driver)
            if not solved:
                return ScrapeResult.fail("captcha", "CAPTCHA not solvable", html, "amazon_au", url)
            html = driver.page_source

        if is_amazon_dog_page(html):
            return ScrapeResult.fail("dog_page", "Amazon block page", html, "amazon_au", url)

        blocked, reason = detect_block(html)
        if blocked:
            return ScrapeResult.fail(f"blocked_{reason}", f"Blocked: {reason}", html, "amazon_au", url)

        soup = BeautifulSoup(html, "html.parser")

        price = AmazonParser.extract_price(soup, html)
        stock = AmazonParser.extract_stock(soup)
        title = AmazonParser.extract_title(soup)

        if price is None:
            return ScrapeResult.fail("no_price", "Price not found", html, "amazon_au", url)

        return ScrapeResult.ok(price=price, stock=stock, title=title)

    except Exception as exc:
        logger.exception("Amazon AU Selenium error for %s", url)
        try:
            html = driver.page_source
        except Exception:
            html = ""
        return ScrapeResult.fail("exception", str(exc), html, "amazon_au", url)


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════

def scrape_amazon(vendor_url: str, region: str, session: dict = None) -> dict:
    """
    Scrape Amazon product page (AU/legacy regions).

    Strategy: HTTP first, Selenium fallback.
    Returns {"price": float|None, "stock": int|None, "title": str|None}
    """
    if session is None:
        session = {}

    is_au = "amazon.com.au" in (vendor_url or "").lower()

    # --- Primary: HTTP ---
    http_result = AmazonLegacyHTTP.scrape_with_retry(vendor_url, session, is_au)
    if http_result.success:
        logger.info("HTTP scrape OK for %s (price=%s)", vendor_url[:60], http_result.price)
        return http_result.to_legacy()

    logger.info(
        "HTTP scrape failed [%s], trying Selenium fallback for %s",
        http_result.error_code, vendor_url[:60],
    )

    # --- Fallback: Selenium ---
    try:
        from selenium import webdriver  # noqa: F401
    except ImportError:
        logger.warning("Selenium not installed — cannot fall back to browser")
        return http_result.to_legacy()

    driver = None
    created_driver = False
    try:
        if session.get("amazon_driver"):
            driver = session["amazon_driver"]
        else:
            driver = AmazonDriver.create()
            created_driver = True
            session["amazon_driver"] = driver

        if is_au:
            _ensure_au_location(driver, session)
        elif region == "USA" or "amazon.com" in (vendor_url or "").lower():
            zip_set = session.get("amazon_us_location_set", False)
            if not zip_set:
                AmazonScraper.set_zip_on_product_page(driver, vendor_url)
                session["amazon_us_location_set"] = True

        last_result = None
        for attempt in range(RETRY_LIMIT):
            if attempt > 0:
                backoff_delay(attempt, base=2.5, jitter=2.0)
            result = _selenium_scrape_page(vendor_url, driver)
            if result.success:
                return result.to_legacy()
            last_result = result
            if result.error_code in ("captcha", "dog_page"):
                break

        logger.warning(
            "Selenium fallback also failed: url=%s code=%s",
            vendor_url,
            last_result.error_code if last_result else "unknown",
        )
        return {"price": None, "stock": None}

    except Exception as exc:
        logger.exception("Selenium fallback exception for %s: %s", vendor_url, exc)
        return {"price": None, "stock": None}

    finally:
        if created_driver and driver and not session.get("amazon_driver"):
            AmazonDriver.quit_safe(driver)


def close_amazon_session(session):
    """Close and cleanup Amazon driver if present in session."""
    if session is None:
        return
    driver = session.pop("amazon_driver", None)
    AmazonDriver.quit_safe(driver)
    session.pop("amazon_au_location_set", None)
    session.pop("amazon_location_set", None)
    http_sess = session.pop("amazon_legacy_http_session", None)
    if http_sess:
        try:
            http_sess.close()
        except Exception:
            pass
