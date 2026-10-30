"""
Amazon AU scraper (amazon.com.au).

Primary: requests + BeautifulSoup (fast).
Fallback: Selenium headless Chrome (when HTTP is blocked or price not found).

Public API:
  scrape_amazon_au(vendor_url, region, session) -> {"price": float|None, "stock": int|None, "title": str|None}
  close_amazon_au_session(session)
"""
import logging
import os
import re

import requests
from bs4 import BeautifulSoup

from .core import (
    ScrapeResult, detect_block, is_amazon_captcha_page, is_amazon_dog_page,
    save_debug_html, random_delay, backoff_delay, parse_price_text,
    get_random_headers, USER_AGENTS,
)
from .amazon_us_scraper import AmazonDriver, AmazonParser, AmazonScraper

logger = logging.getLogger("scrapers.amazon_au")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


RETRY_LIMIT = max(1, _env_int("AMAZON_AU_HTTP_RETRIES", 2) + 1)
FETCH_TIMEOUT = max(5, _env_int("AMAZON_AU_HTTP_TIMEOUT_SEC", 15))
SELENIUM_ATTEMPTS = max(1, _env_int("AMAZON_AU_SELENIUM_ATTEMPTS", 1))
_SESSION_PROXY_KEY = "amazon_au_http_proxy"

# Default to Tallawong NSW so shipping-coverage errors match typical AU customer locations.
# Override with AMAZON_AU_POSTCODE in the AU worker .env.prod.
_DEFAULT_AU_POSTCODE = "2762"


def amazon_au_postcode() -> str:
    """AU delivery postcode for Amazon.com.au pricing / coverage checks."""
    raw = (os.environ.get("AMAZON_AU_POSTCODE") or _DEFAULT_AU_POSTCODE).strip()
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 4:
        return digits
    logger.warning(
        "Invalid AMAZON_AU_POSTCODE=%r — using default %s",
        raw,
        _DEFAULT_AU_POSTCODE,
    )
    return _DEFAULT_AU_POSTCODE

_AU_COOKIES = {
    "i18n-prefs": "AUD",
    "lc-main": "en_AU",
}

_USD_COOKIES = {
    "i18n-prefs": "USD",
    "lc-main": "en_US",
}

# Red error under spacing-base → force inventory 0.
AU_OOS_ERROR_SELECTOR = ".a-spacing-base span.a-color-error"
# Broader buy-box errors (shipping coverage, etc.) may not nest under .a-spacing-base.
AU_COLOR_ERROR_SELECTOR = "span.a-color-error"
_AU_UNSELLABLE_ERROR_MARKERS = (
    "beyond seller's shipping coverage",
    "beyond sellers shipping coverage",
    "shipping coverage for this item",
    "selected delivery location",
    "choose a different delivery location",
    "purchase from another seller",
    "temporarily out of stock",
    "currently unavailable",
    "out of stock",
    # Amazon Global / imported offer. Page still says "In stock", but the
    # goods are not the local product. Match any country prefix, and the
    # newer "Imported products may differ…" wording.
    "imports may differ from local products",
    "imported products may differ from local products",
)


def _au_error_span_is_unsellable(text: str) -> bool:
    t = " ".join((text or "").lower().split())
    if not t:
        return False
    # "Only 5 left in stock" is a-color-error on some PDPs but still sellable.
    if "left in stock" in t or "only" in t and "left" in t:
        if not any(m in t for m in _AU_UNSELLABLE_ERROR_MARKERS):
            return False
    return any(m in t for m in _AU_UNSELLABLE_ERROR_MARKERS)


def apply_au_error_stock(soup: BeautifulSoup, stock):
    """Force stock to 0 when Amazon AU shows an unsellable offer.

    Matches ``.a-spacing-base span.a-color-error``, any ``span.a-color-error``
    whose text indicates shipping coverage / unavailable, or an import
    disclaimer ("imports may differ from local products" or
    "imported products may differ from local products") anywhere on the page
    (it is plain buy-box text, not a red error). Price is unchanged.
    """
    try:
        if soup is None:
            return stock
        page_text = " ".join(soup.get_text(" ", strip=True).lower().split())
        if (
            "imports may differ from local products" in page_text
            or "imported products may differ from local products" in page_text
        ):
            return 0
        if soup.select_one(AU_OOS_ERROR_SELECTOR) is not None:
            return 0
        for el in soup.select(AU_COLOR_ERROR_SELECTOR):
            if _au_error_span_is_unsellable(el.get_text(" ", strip=True)):
                return 0
    except Exception:
        pass
    return stock


_ASIN_TOKEN_RE = re.compile(r"\b(B[A-Z0-9]{9})\b", re.IGNORECASE)
_ASIN_URL_RE = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})", re.IGNORECASE)


def asin_from_amazon_url(url: str) -> str:
    """ASIN in the vendor link that was opened, before any redirect."""
    match = _ASIN_URL_RE.search(url or "")
    return match.group(1).upper() if match else ""


def asin_from_amazon_page(soup: BeautifulSoup) -> str:
    """ASIN printed on the product page (Item details / Additional Information)."""
    if soup is None:
        return ""
    for node in soup.select("th, span.a-text-bold, td.a-span3, span.a-color-secondary"):
        label = node.get_text(" ", strip=True).rstrip(":").strip().upper()
        if label != "ASIN":
            continue
        sibling = node.find_next_sibling()
        blob = sibling.get_text(" ", strip=True) if sibling else ""
        if not blob and node.parent is not None:
            blob = node.parent.get_text(" ", strip=True)
        match = _ASIN_TOKEN_RE.search(blob)
        if match:
            return match.group(1).upper()
    for sel in ("input#ASIN", "input[name='ASIN']"):
        el = soup.select_one(sel)
        value = (el.get("value") if el else "") or ""
        if _ASIN_TOKEN_RE.fullmatch(value.strip()):
            return value.strip().upper()
    return ""


def au_asin_mismatch_result(
    requested_url: str,
    soup: BeautifulSoup,
    landed_url: str = "",
):
    """Fail the row when Amazon opened a different product than the listing ASIN.

    A mismatch is taken from the printed Item details ASIN or from the URL
    Amazon actually opened. Either one is enough, so a redirect still fails
    when the details table is missing. The other product's price is not returned.
    """
    expected = asin_from_amazon_url(requested_url)
    if not expected:
        return None
    observed = []
    for candidate in (asin_from_amazon_page(soup), asin_from_amazon_url(landed_url)):
        if candidate and candidate not in observed:
            observed.append(candidate)
    foreign = [asin for asin in observed if asin != expected]
    if not foreign:
        return None
    found = foreign[0]
    logger.info(
        "Amazon AU ASIN mismatch url=%s expected=%s opened=%s",
        (requested_url or "")[:120],
        expected,
        found,
    )
    return ScrapeResult.fail(
        "asin_mismatch",
        f"Amazon opened ASIN {found}; this listing is {expected}.",
        "",
        "amazon_au",
        requested_url,
    )


# ═══════════════════════════════════════════════════════════════════════════
# HTTP-based scraper (primary)
# ═══════════════════════════════════════════════════════════════════════════

class AmazonAUHTTP:
    """Fast requests-based scraper for Amazon AU."""

    _ZIP_CHANGE_URLS = {
        False: "https://www.amazon.com/gp/delivery/ajax/address-change.html",
        True: "https://www.amazon.com.au/gp/delivery/ajax/address-change.html",
    }

    @staticmethod
    def _get_session(session_dict: dict, is_au: bool, rotate: bool = False) -> requests.Session:
        """HTTP session bound to one proxy. ``rotate`` moves it to the next proxy.

        Cookies (including the delivery postcode) belong to one IP, so a new
        proxy gets a fresh session and the postcode is set again.
        """
        key = "amazon_au_http_session"
        existing = session_dict.get(key) if session_dict is not None else None
        if existing is not None and not rotate:
            return existing

        from .amazon_au_proxies import acquire_proxy

        assignment = acquire_proxy(force_rotate=rotate) if is_au else None
        if existing is not None:
            try:
                existing.close()
            except Exception:
                pass
        s = requests.Session()
        domain = "amazon.com.au" if is_au else "amazon.com"
        s.headers.update(get_random_headers(f"https://www.{domain}/"))
        s.cookies.update(_AU_COOKIES if is_au else _USD_COOKIES)
        if assignment is not None:
            s.proxies = assignment.as_requests_proxy()
            logger.info("Amazon AU HTTP session via proxy %s", assignment.label)
        if session_dict is not None:
            session_dict[key] = s
            session_dict[_SESSION_PROXY_KEY] = assignment
            session_dict.pop("amazon_au_http_zip_set", None)
        return s

    @classmethod
    def _ensure_zip(cls, s: requests.Session, seed_url: str, session_dict: dict, is_au: bool):
        """Set delivery location for accurate pricing.

        Tried once per HTTP session. A failed attempt is not repeated for every
        product: that added two extra requests to each scrape.
        """
        zip_key = "amazon_au_http_zip_set"
        if session_dict is not None and zip_key in session_dict:
            return
        zip_code = amazon_au_postcode() if is_au else "10001"
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
        vendor = "amazon_au"
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

        mismatch = au_asin_mismatch_result(url, soup, getattr(resp, "url", "") or "")
        if mismatch is not None:
            return mismatch

        oos = AmazonParser.oos_without_offer_result(soup)
        if oos is not None:
            return oos

        price = AmazonParser.extract_price(soup, html, market="AU")
        stock = AmazonParser.extract_stock(soup)
        title = AmazonParser.extract_title(soup)
        stock = apply_au_error_stock(soup, stock)

        if price is None:
            return ScrapeResult.fail("no_price", "Price not found via HTTP", html, vendor, url)

        if stock is None and price is not None:
            stock = 2

        return ScrapeResult.ok(price=price, stock=stock, title=title)

    @classmethod
    def scrape_with_retry(cls, url: str, session_dict: dict = None, is_au: bool = False) -> ScrapeResult:
        from .amazon_au_proxies import mark_blocked, mark_success, proxies_configured

        use_proxies = is_au and proxies_configured()
        last_result = None
        for attempt in range(RETRY_LIMIT):
            result = cls.fetch(url, session_dict, is_au)
            assignment = (session_dict or {}).get(_SESSION_PROXY_KEY)
            if result.success:
                mark_success(assignment)
                return result
            last_result = result
            if result.error_code in ("http_404", "asin_mismatch", "no_price"):
                break
            if attempt + 1 >= RETRY_LIMIT:
                break
            ip_problem = (
                result.error_code.startswith(("blocked", "http_5", "http_403", "http_429"))
                or result.error_code in ("timeout", "connection_error", "request_error")
            )
            if use_proxies and ip_problem:
                mark_blocked(assignment)
                cls._get_session(session_dict, is_au, rotate=True)
                continue
            backoff_delay(attempt + 1, base=2.0, jitter=1.5)
            if result.error_code.startswith("blocked"):
                s = cls._get_session(session_dict, is_au)
                domain = "amazon.com.au" if is_au else "amazon.com"
                s.headers.update(get_random_headers(f"https://www.{domain}/"))
        return last_result or ScrapeResult.fail("max_retries", "All HTTP attempts failed", "", "amazon_au", url)


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
            postcode = amazon_au_postcode()
            postal_input.clear()
            postal_input.send_keys(postcode)
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
        random_delay(1, 2)
        AmazonScraper.solve_captcha(driver)

        try:
            WebDriverWait(driver, 8).until(EC.presence_of_element_located((
                By.CSS_SELECTOR,
                "span.a-price, .apex-pricetopay-value, #corePrice_feature_div, "
                ".apexPriceToPay, #availability",
            )))
        except Exception:
            pass
        time.sleep(0.5)

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
        try:
            landed_url = driver.current_url or ""
        except Exception:
            landed_url = ""

        mismatch = au_asin_mismatch_result(url, soup, landed_url)
        if mismatch is not None:
            return mismatch

        oos = AmazonParser.oos_without_offer_result(soup)
        if oos is not None:
            return oos

        price = AmazonParser.extract_price(soup, html, market="AU")
        stock = AmazonParser.extract_stock(soup)
        title = AmazonParser.extract_title(soup)
        stock = apply_au_error_stock(soup, stock)

        if price is None:
            return ScrapeResult.fail("no_price", "Price not found", html, "amazon_au", url)

        if stock is None and price is not None:
            stock = 2

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

def scrape_amazon_au(vendor_url: str, region: str, session: dict = None) -> dict:
    """
    Scrape Amazon AU product page (amazon.com.au).

    Strategy: HTTP first, Selenium fallback.
    Returns {"price": float|None, "stock": int|None, "title": str|None}
    """
    if session is None:
        session = {}

    is_au = "amazon.com.au" in (vendor_url or "").lower()

    # --- Primary: HTTP ---
    http_result = AmazonAUHTTP.scrape_with_retry(vendor_url, session, is_au)
    if http_result.success:
        logger.info("HTTP scrape OK for %s (price=%s)", vendor_url[:60], http_result.price)
        return http_result.to_legacy()
    if http_result.error_code == "asin_mismatch":
        logger.info(
            "Amazon AU ASIN mismatch is final for %s; not trying the browser",
            vendor_url[:80],
        )
        return http_result.to_legacy()

    logger.info(
        "HTTP scrape failed [%s], trying Selenium fallback for %s",
        http_result.error_code, vendor_url[:60],
    )

    if os.environ.get("AMAZON_AU_SELENIUM_FALLBACK", "1").strip().lower() in ("0", "false", "no", "off"):
        return http_result.to_legacy()

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
        for attempt in range(SELENIUM_ATTEMPTS):
            if attempt > 0:
                backoff_delay(attempt, base=2.5, jitter=2.0)
            result = _selenium_scrape_page(vendor_url, driver)
            if result.success:
                return result.to_legacy()
            last_result = result
            if result.error_code in ("captcha", "dog_page", "asin_mismatch"):
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


def close_amazon_au_session(session):
    """Close and cleanup Amazon AU driver / HTTP session if present in session."""
    if session is None:
        return
    driver = session.pop("amazon_driver", None)
    AmazonDriver.quit_safe(driver)
    session.pop("amazon_au_location_set", None)
    session.pop("amazon_location_set", None)
    http_sess = session.pop("amazon_au_http_session", None)
    if http_sess:
        try:
            http_sess.close()
        except Exception:
            pass
    session.pop("amazon_au_http_zip_set", None)
    session.pop(_SESSION_PROXY_KEY, None)
