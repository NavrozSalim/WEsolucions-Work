"""
Shared eBay scraping implementation (US and AU market modules import from here).

See `ebay_us_scraper` and `ebay_au_scraper` for public entry points.
"""

import contextvars
import os
import re
import json
import time
import random
import logging
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, Iterator
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .stop_signal import raise_if_scrape_stopped
from .core import (
    random_delay,
    backoff_delay,
    parse_price_text,
    classify_failure,
    should_retry_failure,
    save_debug_html,
)

logger = logging.getLogger("scrapers.ebay_common")

EBAY_MARKET_US = "us"
EBAY_MARKET_AU = "au"


def _ebay_sk(market: str, suffix: str) -> str:
    return f"ebay_{market}_{suffix}"


def _ebay_market_region(market: str) -> str:
    return "AU" if market == EBAY_MARKET_AU else "USA"


def _migrate_legacy_ebay_session(session: dict | None, market: str) -> None:
    """Lift pre-split session keys into market-specific slots (one catalog run)."""
    if not session:
        return
    pairs = (
        ("selenium_driver", "ebay_selenium_driver"),
        ("http_client", "ebay_http_client"),
        ("last_user_agent", "ebay_last_user_agent"),
        ("last_http_url", "ebay_last_http_url"),
        ("last_browser_url", "ebay_last_browser_url"),
        ("last_failed_html", "ebay_last_failed_html"),
    )
    for suffix, legacy in pairs:
        nk = _ebay_sk(market, suffix)
        if nk not in session and legacy in session:
            session[nk] = session.pop(legacy)


SESSION_DEBUG_HTML_KEY = "_debug_save_html_path"

TIMEOUT_SEC = 45
EBAY_HTTP_TIMEOUT_SEC = 20
PAGE_WAIT_TIMEOUT = 18
RETRY_LIMIT = 3


def _ebay_http_timeout_sec() -> int:
    raw = (os.environ.get("EBAY_HTTP_TIMEOUT_SEC") or "").strip()
    if raw:
        try:
            return max(5, int(raw))
        except ValueError:
            pass
    return EBAY_HTTP_TIMEOUT_SEC


def _ebay_page_wait_timeout(region: str, item_url: str = "") -> int:
    """Max seconds to poll Selenium for product DOM (lower for AU = faster fail on blocks)."""
    auish = (region or "").upper() == "AU" or "ebay.com.au" in (item_url or "").lower()
    if auish:
        raw = (os.environ.get("EBAY_AU_PAGE_WAIT_TIMEOUT_SEC") or "").strip()
        if raw:
            try:
                return max(4, int(raw))
            except ValueError:
                pass
        return 10
    raw = (os.environ.get("EBAY_PAGE_WAIT_TIMEOUT_SEC") or "").strip()
    if raw:
        try:
            return max(4, int(raw))
        except ValueError:
            pass
    return PAGE_WAIT_TIMEOUT

PRICE_SUFFIX_PATTERN = re.compile(
    r"(or Best Offer|Buy It Now|Best Offer|Make Offer|each|/ea).*$",
    re.IGNORECASE,
)

# Reference / footnote rows — not the live BIN headline.
_RE_LIST_PRICE_PHRASE = re.compile(r"\blist\s+price\b", re.IGNORECASE)
_RE_COMPARE_AT_PHRASE = re.compile(r"\bcompare\s+at\b", re.IGNORECASE)
_RE_PERCENT_OFF_ONLY = re.compile(
    r"^\s*\(?\s*\d+(?:\.\d+)?\s*%\s*off\s*\)?\s*$",
    re.IGNORECASE,
)
# Coupon / promo banners (eBay AU shows "Extra AU $100.00 off seller's price with code MAYSS2").
# These are NOT the BIN price — the live price stays in x-price-primary.
_RE_PROMO_PHRASE = re.compile(
    r"\b(?:off\s+seller'?s\s+price|with\s+code|coupon|promo\s*code"
    r"|extra\s+[^.]{0,40}\boff\b|save\s+[^.]{0,40}\bcode\b"
    r"|seller'?s\s+price\s+with\s+code|apply\s+at\s+checkout)\b",
    re.IGNORECASE,
)
_RE_CURRENCY_AMOUNT = re.compile(
    r"(?:US|AU|USD|AUD|CAD|GBP|EUR)?\s*"
    r"(?:\$|£|€)\s*"
    r"([\d,]+(?:\.\d{1,2})?)",
    re.IGNORECASE,
)

# AU postage in embedded JSON (HTTP HTML often lacks hydrated shipping DOM).
_AU_SHIPPING_JSON_PATTERNS = (
    # eBay GraphQL shipping quote (amount is numeric, not a string).
    re.compile(
        r'"converted"\s*:\s*null\s*,\s*"original"\s*:\s*\{\s*"__typename"\s*:\s*"Price"\s*,'
        r'\s*"amount"\s*:\s*([\d.]+)\s*,\s*"currency"\s*:\s*"AUD"\s*\}\s*\}\s*,\s*"shipToLocations"',
        re.IGNORECASE,
    ),
    re.compile(
        r'"original"\s*:\s*\{\s*"__typename"\s*:\s*"Price"\s*,\s*"amount"\s*:\s*([\d.]+)\s*,'
        r'\s*"currency"\s*:\s*"AUD"\s*\}\s*\}\s*,\s*"shipToLocations"',
        re.IGNORECASE,
    ),
    re.compile(
        r'"amount"\s*:\s*([\d.]+)\s*,\s*"currency"\s*:\s*"AUD"\s*\}\s*\}\s*,\s*"shipToLocations"',
        re.IGNORECASE,
    ),
    re.compile(
        r'"shipping(?:Cost|Price|Amount)"\s*:\s*\{[^}]{0,200}?"value"\s*:\s*"([\d.]+)"',
        re.IGNORECASE,
    ),
    re.compile(
        r'"shipping(?:Cost|Price|Amount)"\s*:\s*"([\d.]+)"',
        re.IGNORECASE,
    ),
    re.compile(
        r'"postage(?:Cost|Price|Amount)"\s*:\s*\{[^}]{0,200}?"value"\s*:\s*"([\d.]+)"',
        re.IGNORECASE,
    ),
    re.compile(
        r'"delivery(?:Cost|Price)"\s*:\s*\{[^}]{0,200}?"value"\s*:\s*"([\d.]+)"',
        re.IGNORECASE,
    ),
    re.compile(
        r'"postagePrice"\s*:\s*\{[^}]{0,200}?"value"\s*:\s*"([\d.]+)"',
        re.IGNORECASE,
    ),
)

_RE_AU_DELIVERY_PRICE_LINE = re.compile(
    r"AU\s*\$\s*([\d,]+(?:\.\d{2})?)\s*delivery",
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
    # Akamai's 403 page on every eBay site ("Error Page | eBay").
    "something went wrong on our end",
]


def ebay_result_error_code(last_error: str | None) -> str:
    """Reason code for a failed eBay scrape, as the catalog reads it.

    ``not_found``: eBay says the listing does not exist. ``blocked`` /
    ``challenge``: the page was never seen (bot wall, proxy or browser error),
    so the listing must not be treated as gone. ``no_price``: the page loaded
    but no price was found.
    """
    err = (last_error or "").strip().lower()
    if not err:
        return "no_price"
    if err in ("not_found", "http_404", "http_410"):
        return "not_found"
    if "challenge" in err or "captcha" in err:
        return "challenge"
    page_not_seen = (
        "blocked", "http_4", "http_5", "empty", "proxy_exhausted", "not_product_like",
        "http_error", "timeout", "selenium", "response_invalid", "upstream_error",
        "connection", "curl_cffi", "proxy_error",
    )
    if any(k in err for k in page_not_seen):
        return "blocked"
    return "no_price"

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

    query = (parsed.query or "").strip()
    fragment = (parsed.fragment or "").strip()

    orig_lower = (original_url or "").lower()
    if "ebay.com.au" in orig_lower:
        base = f"https://www.ebay.com.au/itm/{item_id}"
    elif (region or "").strip().upper() == "AU":
        base = f"https://www.ebay.com.au/itm/{item_id}"
    else:
        base = f"https://www.ebay.com/itm/{item_id}"

    if query:
        base = f"{base}?{query}"
    if fragment:
        base = f"{base}#{fragment}"
    return base


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


def _effective_ebay_region(region: str, normalized_url: str) -> str:
    """Use AU cookies/referer/HTTP-first when the scrape target is ebay.com.au."""
    if "ebay.com.au" in (normalized_url or "").lower():
        return "AU"
    return (region or "").strip().upper() or "USA"


def _strip_price_suffix(text: str) -> str:
    if not text:
        return ""
    return PRICE_SUFFIX_PATTERN.sub("", text).strip()


def _parse_ebay_display_price_text(text: str) -> Optional[float]:
    """Parse a single eBay ``ux-textspans`` price line (headline BIN, not footnotes).

    Rejects list-price reference rows, bare ``21% off`` percent lines, and any text
    where the only numeric token is a discount percentage (no currency marker).
    """
    if not text:
        return None
    stripped = _strip_price_suffix(text.strip())
    if not stripped or len(stripped) > 120:
        return None
    if _RE_LIST_PRICE_PHRASE.search(stripped) or _RE_COMPARE_AT_PHRASE.search(stripped):
        return None
    if _RE_PROMO_PHRASE.search(stripped):
        return None
    if _RE_PERCENT_OFF_ONLY.match(stripped):
        return None
    if "%" in stripped and not _RE_CURRENCY_AMOUNT.search(stripped):
        return None

    amounts: list[float] = []
    for m in _RE_CURRENCY_AMOUNT.finditer(stripped):
        try:
            val = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        if 0.01 <= val < 999_999:
            amounts.append(val)
    if amounts:
        return min(amounts)

    if "%" in stripped:
        return None
    return parse_price_text(stripped)


def _ebay_home_origin_for_item_url(item_url: str) -> str:
    u = (item_url or "").lower()
    if "ebay.ca" in u:
        return "https://www.ebay.ca/"
    if "ebay.com.au" in u:
        return "https://www.ebay.com.au/"
    return "https://www.ebay.com/"


def _ebay_region_referer(region: str) -> str:
    return "https://www.ebay.com.au/" if region == "AU" else "https://www.ebay.com/"


def _ebay_bin_hydrate_max_seconds(eff_region: str, item_url: str) -> float:
    """Extra post-load polling so seller-discount rows can paint (AU PDPs often hydrate late)."""
    auish = eff_region == "AU" or "ebay.com.au" in (item_url or "").lower()
    raw = (os.environ.get("EBAY_BIN_HYDRATE_MAX_SEC") or "").strip()
    if raw:
        return max(0.0, float(raw))
    return 2.0 if auish else 0.0


def _ebay_debug_write_html(session: Optional[dict], html: Optional[str], tag: str, candidate: str) -> None:
    """If ``session[SESSION_DEBUG_HTML_KEY]`` is set, write raw HTML used for parsing (debug)."""
    if not session or not html:
        return
    path = (session.get(SESSION_DEBUG_HTML_KEY) or "").strip()
    if not path:
        return
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        safe_url = (candidate or "")[:400].replace("--", "-")
        header = f"<!-- ebay-debug tag={tag} url={safe_url} -->\n"
        p.write_text(header + html, encoding="utf-8", errors="replace")
        logger.info("eBay debug HTML (%s) wrote %s bytes to %s", tag, len(html), path)
    except OSError as exc:
        logger.warning("eBay debug HTML write failed: %s", exc)


def _ebay_http_first_enabled(region: Optional[str]) -> bool:
    r = (region or "").strip().upper()
    trueish = ("1", "true", "yes")
    if r == "AU":
        au = (os.environ.get("EBAY_AU_HTTP_FIRST") or "").strip()
        if au:
            return au.lower() in trueish
        return (os.environ.get("EBAY_HTTP_FIRST", "1") or "1").strip().lower() in trueish
    return (os.environ.get("EBAY_HTTP_FIRST", "1") or "1").strip().lower() in trueish


def _ebay_proxy_module(market: str):
    if market == EBAY_MARKET_AU:
        from . import ebay_au_proxies

        return ebay_au_proxies
    from . import ebay_us_proxies

    return ebay_us_proxies


def _ebay_proxy_pool(market: str):
    if market == EBAY_MARKET_AU:
        from .ebay_au_proxies import get_ebay_au_pool

        return get_ebay_au_pool()
    from .ebay_us_proxies import get_ebay_us_pool

    return get_ebay_us_pool()


def _env_on(name: str, default: str) -> bool:
    return (os.environ.get(name) or default).strip().lower() in ("1", "true", "yes", "on")


def ebay_us_browser_first() -> bool:
    """eBay US loads pages in real Chrome (``EBAY_US_BROWSER_FIRST``, default on).

    eBay's bot wall (Akamai) only lets a visitor in after running its
    JavaScript, which plain HTTP cannot do.
    """
    return _env_on("EBAY_US_BROWSER_FIRST", "1")


def ebay_us_browser_proxies() -> bool:
    """Chrome for eBay US goes through the eBay US proxies (``EBAY_US_BROWSER_PROXIES``, default on).

    Chrome cannot send a proxy password, so the server's IP must be on the
    proxy provider's IP allowlist. Set 0 to load pages from the server's own IP.
    """
    return _env_on("EBAY_US_BROWSER_PROXIES", "1")


def ebay_us_chrome_native_ua() -> bool:
    """``EBAY_US_CHROME_NATIVE_UA=1``: Chrome keeps its own Linux browser name.

    Off by default: eBay US Chrome presents one consistent Windows Chrome instead.
    """
    return _env_on("EBAY_US_CHROME_NATIVE_UA", "0")


def ebay_us_chrome_attempts() -> int:
    """Chrome tries per eBay US listing, each on a fresh proxy (``EBAY_US_CHROME_ATTEMPTS``, default 5)."""
    try:
        return max(1, min(10, int((os.environ.get("EBAY_US_CHROME_ATTEMPTS") or "5").strip())))
    except ValueError:
        return 5


def ebay_us_ca_first() -> bool:
    """Chrome-first tries the item on ebay.ca before ebay.com (``EBAY_US_CA_FIRST``, default off).

    With Chrome on the proxies ebay.com gets through and ebay.ca is refused, so
    ebay.ca is only the second try. Turn on when Chrome uses the server's own IP.
    """
    return _env_on("EBAY_US_CA_FIRST", "0")


def ebay_us_cookie_handoff() -> bool:
    """After Chrome, one plain request with Chrome's eBay cookies (``EBAY_US_COOKIE_HANDOFF``, default on)."""
    return _env_on("EBAY_US_COOKIE_HANDOFF", "1")


# How long US Chrome waits on eBay's "Pardon Our Interruption" page.
_US_CHALLENGE_WAIT_SEC = 25


def _human_pause(driver) -> None:
    """A few seconds on the page with a little scrolling, as a person would."""
    try:
        for _ in range(2):
            time.sleep(random.uniform(1.2, 2.0))
            driver.execute_script(f"window.scrollBy(0, {random.randint(150, 450)});")
        time.sleep(random.uniform(0.5, 1.0))
    except Exception:
        pass


# When the proxies refuse Chrome (server IP not on the allowlist), every page is
# an empty stub. After this many refusals in a row on different proxies, Chrome
# uses the server's own IP for a while instead of failing every listing.
_US_PROXY_FAILS_BEFORE_OFF = 2
_US_PROXY_OFF_SEC = 1800
_us_browser_proxy_fails = 0
_us_browser_proxy_off_until = 0.0


def _us_browser_proxies_active() -> bool:
    return ebay_us_browser_proxies() and time.monotonic() >= _us_browser_proxy_off_until


def _us_chrome_uses_proxies() -> bool:
    """US Chrome will go through the eBay US proxies (configured, on, not switched off)."""
    if not _us_browser_proxies_active():
        return False
    from . import ebay_us_proxies

    return ebay_us_proxies.proxies_configured()


def _note_us_browser_proxy_result(ok: bool) -> None:
    """Count Chrome proxy refusals; after a run of them, use the server's own IP."""
    global _us_browser_proxy_fails, _us_browser_proxy_off_until
    if ok:
        _us_browser_proxy_fails = 0
        return
    _us_browser_proxy_fails += 1
    if _us_browser_proxy_fails >= _US_PROXY_FAILS_BEFORE_OFF:
        _us_browser_proxy_fails = 0
        _us_browser_proxy_off_until = time.monotonic() + _US_PROXY_OFF_SEC
        logger.warning(
            "eBay US: the proxies refused Chrome %s times in a row; using this server's own IP "
            "for %s minutes. Add this server's IP to the proxy provider's IP allowlist, "
            "or set EBAY_US_BROWSER_PROXIES=0.",
            _US_PROXY_FAILS_BEFORE_OFF,
            _US_PROXY_OFF_SEC // 60,
        )


_BROWSER_NET_ERROR_MARKERS = (
    "err_tunnel_connection_failed",
    "err_proxy_connection_failed",
    "err_proxy_auth",
    "err_no_supported_proxies",
    "err_connection_refused",
    "err_connection_reset",
    "err_connection_closed",
    "err_empty_response",
    "err_timed_out",
    "class=\"neterror\"",
)


def _browser_page_not_loaded(html: str | None) -> bool:
    """Chrome shows an empty stub or its own error page when the proxy refuses it."""
    if not html:
        return True
    if _looks_like_product_html(html):
        return False
    lower = html.lower()
    if any(m in lower for m in _BROWSER_NET_ERROR_MARKERS):
        return True
    return len(html) < 1500 and "ebay" not in lower


def _ebay_http_only_with_proxies(market: str) -> bool:
    """Chrome cannot log in to Webshare proxies, so it would load pages from the VPS IP."""
    prefix = "EBAY_AU" if market == EBAY_MARKET_AU else "EBAY_US"
    force_sel = (os.environ.get(f"{prefix}_FULL_ENGINE_SELENIUM") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )
    return _ebay_proxy_module(market).proxies_configured() and not force_sel


_CHROME_131_WIN_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _random_user_agent() -> str:
    agents = [
        _CHROME_131_WIN_UA,
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
    # Prefer BIN / primary display inside the main item price region only (avoids ads, bundles, sidebar).
    _ITEM_PRICE_SECTION_SELECTORS = (
        "[data-testid='x-item-price']",
        "section.x-item-price",
        "[data-testid='x-price-view']",
    )

    # AU BIN headline: first ``span`` under ``[data-testid='x-price-primary']`` (ebay.com.au layout).
    _AU_PRIMARY_HEADLINE_SELECTORS = (
        "[data-testid='x-price-primary'] > span",
        "[data-testid='x-price-primary'] span.ux-textspans",
        "[data-testid='x-price-primary'] span",
        "[data-test-id='x-price-primary'] > span",
        "[data-test-id='x-price-primary'] span",
        # AU pages occasionally drop ``x-price-primary`` and expose headline
        # directly under ``x-bin-price`` (single-row BIN layout).
        "[data-testid='x-bin-price'] .ux-textspans--BOLD",
        "[data-testid='x-bin-price'] > div > span.ux-textspans",
        ".x-bin-price .x-price-primary span",
        ".x-price-primary span.ux-textspans",
        ".x-price-primary > span",
    )

    # US BIN headline: ``x-price-primary`` nested inside ``x-bin-price`` (not a sibling row).
    _US_BIN_HEADLINE_SELECTORS = (
        "#mainContent .x-bin-price .x-price-primary > span",
        ".x-price-section .x-bin-price .x-price-primary span",
        ".vim.x-bin-price .x-price-primary span",
        "[data-testid='x-bin-price'] .x-price-primary span",
        ".x-bin-price .x-price-primary > span",
        ".x-bin-price .x-price-primary .ux-textspans",
        ".x-bin-price .x-price-primary span",
    )

    PRIMARY_PRICE_SELECTORS = [
        ".x-bin-price .x-price-primary .ux-textspans--BOLD",
        ".x-bin-price .x-price-primary .ux-textspans",
        ".x-bin-price .x-price-primary span",
        "[data-testid='x-bin-price'] .x-price-primary span",
        "[data-testid='x-price-primary'] .ux-textspans--BOLD",
        "[data-testid='x-price-primary'] .ux-textspans",
        "[data-testid='x-price-primary'] span",
        "[data-test-id='x-price-primary'] .ux-textspans--BOLD",
        "[data-test-id='x-price-primary'] span",
        ".x-price-primary .ux-textspans--BOLD",
        ".x-price-primary span.ux-textspans",
        ".x-price-primary span",
        "div.x-price-primary",
        ".x-price-primary",
        "[data-testid='x-bin-price'] .ux-textspans--BOLD",
        "[data-testid='x-bin-price'] span",
        ".x-bin-price__content .ux-textspans--BOLD",
        ".x-bin-price__content span",
        ".x-bin-price span",
    ]

    # Legacy / wider layout — avoid bare span.ux-textspans--BOLD until late (matches unrelated bold prices).
    FALLBACK_PRICE_SELECTORS = [
        ".x-price-primary",
        "section.x-item-price span.ux-textspans--BOLD",
        "div.ux-section-module span.ux-textspans--BOLD",
        ".x-auction-price .ux-textspans--BOLD",
        ".x-auction-price span",
        ".ux-labels-values__values-content .ux-textspans--BOLD",
        ".ux-price",
        "span[itemprop='price']",
        "#prcIsum",
        ".notranslate",
        ".display-price",
        "[data-testid='price-value']",
        ".price-current",
        "span.ux-textspans--BOLD",
    ]

    # Prefer listing BIN / local display keys; generic "price" + convertedPrice often pick wrong figures.
    PRICE_JSON_PATTERNS_PRIMARY = [
        r'"buyItNowPrice"\s*:\s*\{\s*"value"\s*:\s*"([\d.]+)"',
        r'"binPrice"\s*:\s*\{\s*"value"\s*:\s*"([\d.]+)"',
        r'"currentPrice"\s*:\s*\{\s*"value"\s*:\s*"([\d.]+)"',
        r'"discountedPrice"\s*:\s*\{\s*"value"\s*:\s*"([\d.]+)"',
        r'"price"\s*:\s*\[\s*"([\d.]+)"\s*\]',
        r'"priceValue"\s*:\s*"([\d.]+)"',
        r'"value"\s*:\s*"([\d.]+)"\s*,\s*"currency"',
        r'"finalPrice"\s*:\s*\{\s*"value"\s*:\s*([\d.]+)',
        r'"transactionAmount"\s*:\s*\{\s*"value"\s*:\s*([\d.]+)',
    ]

    # Tighter patterns for the item model chunk only (avoid unrelated "price" keys sitewide).
    ITEM_MODEL_PRICE_JSON_PATTERNS = (
        r'"buyItNowPrice"\s*:\s*\{\s*"value"\s*:\s*"([\d.]+)"',
        r'"buyItNowPrice"\s*:\s*\{\s*"value"\s*:\s*([\d.]+)\s*[,}]',
        r'"binPrice"\s*:\s*\{\s*"value"\s*:\s*"([\d.]+)"',
        r'"binPrice"\s*:\s*\{\s*"value"\s*:\s*([\d.]+)\s*[,}]',
        r'"currentPrice"\s*:\s*\{\s*"value"\s*:\s*"([\d.]+)"',
        r'"currentPrice"\s*:\s*\{\s*"value"\s*:\s*([\d.]+)\s*[,}]',
        r'"discountedPrice"\s*:\s*\{\s*"value"\s*:\s*"([\d.]+)"',
        r'"discountedPrice"\s*:\s*\{\s*"value"\s*:\s*([\d.]+)\s*[,}]',
        r'"salePrice"\s*:\s*\{\s*"value"\s*:\s*"([\d.]+)"',
        r'"salePrice"\s*:\s*\{\s*"value"\s*:\s*([\d.]+)\s*[,}]',
        r'"displayPrice"\s*:\s*\{\s*"value"\s*:\s*"([\d.]+)"',
        r'"displayPrice"\s*:\s*\{\s*"value"\s*:\s*([\d.]+)\s*[,}]',
        r'"marketingPrice"\s*:\s*\{\s*"value"\s*:\s*"([\d.]+)"',
        r'"marketingPrice"\s*:\s*\{\s*"value"\s*:\s*([\d.]+)\s*[,}]',
        r'"finalPrice"\s*:\s*\{\s*"value"\s*:\s*([\d.]+)',
    )

    PRICE_JSON_PATTERNS_FALLBACK = [
        r'"price"\s*:\s*"([\d.]+)"',
        r'"convertedPrice"\s*:\s*\{\s*"value"\s*:\s*"([\d.]+)"',
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

    # AU postage row. Paid postage is added on top of the scraped item price.
    AU_SHIPPING_BLOCK_SELECTOR = ".ux-labels-values--shipping"
    AU_SHIPPING_VALUES_SELECTOR = (
        ".ux-labels-values--shipping div.ux-labels-values__values-content"
    )
    AU_SHIPPING_SELECTOR = (
        ".ux-labels-values--shipping .ux-labels-values__values-content div:nth-of-type(1)"
    )

    _AU_SHIPPING_SKIP_PHRASES = (
        "doesn't post",
        "does not post",
        "doesnt post",
        "can't post",
        "cannot post",
        "unable to post",
        "no postage",
        "located in:",
        "get it between",
        "see details",
        "buyer pays for return",
        "returns accepted",
        "returns.",
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

        # Do not use [itemprop='price'] — schema.org offers include BIN pages and would skip
        # the ux-textspans headline path in extract_price.
        bid_elem = soup.select_one("#prcIsum_bidPrice, .vi-VR-cvipPrice")
        place_bid = soup.select_one("#bidBtn_btn, .vi-bidding-area")
        if bid_elem or place_bid:
            return "auction"

        return "buy_now"

    @staticmethod
    def _is_strikethrough_element(elem) -> bool:
        """True if eBay marks this node (or a short ancestor chain) as struck / old price."""
        if elem is None:
            return False
        cur = elem
        for _ in range(8):
            if cur is None:
                break
            cls = " ".join(cur.get("class") or "").lower()
            if any(
                x in cls
                for x in (
                    "strikethrough",
                    "strike-through",
                    "linethrough",
                    "line-through",
                    "text-strike",
                )
            ):
                return True
            style = (cur.get("style") or "").lower().replace(" ", "")
            if "line-through" in style or "linethrough" in style:
                return True
            cur = getattr(cur, "parent", None)
        return False

    @staticmethod
    def _under_price_noise(elem) -> bool:
        """True if ``elem`` is under sponsored / merch / similar-items (not main BIN headline)."""
        if elem is None:
            return True
        cur = elem
        for _ in range(22):
            if cur is None:
                break
            tid = (cur.get("data-testid") or "").lower()
            if any(
                x in tid
                for x in (
                    "spon",
                    "merch",
                    "recs",
                    "related-",
                    "left-nav",
                    "hub-",
                    "d-sisr",
                    "x-sisr",
                )
            ):
                return True
            cid = (cur.get("id") or "").lower()
            if any(x in cid for x in ("srp-", "relatedads", "rtm_html")):
                return True
            cls = " ".join(cur.get("class") or []).lower()
            if any(
                x in cls
                for x in (
                    "sponsored",
                    "merchandising",
                    "similar-items",
                    "vi-carousel",
                    "str-item-card",
                    "str-sponsored",
                    "ad-banner",
                    "ads-",
                    "ad--",
                    "vim-",
                    "x-merch",
                )
            ):
                return True
            cur = getattr(cur, "parent", None)
        return False

    @staticmethod
    def _ux_span_outside_noise_regions(span) -> bool:
        """Skip title / shipping / merch when scanning loose ``span.ux-textspans``."""
        p = span
        while p is not None:
            tid = (p.get("data-testid") or "").lower()
            if tid in ("x-item-title", "x-shipping"):
                return False
            cls = " ".join(p.get("class") or "").lower()
            if "x-item-title" in cls:
                return False
            p = getattr(p, "parent", None)
        if EbayParser._under_price_noise(span):
            return False
        return True

    @staticmethod
    def _ux_span_installment_payment_row(span) -> bool:
        """BNPL / split-pay rows (e.g. Afterpay) carry small dollar amounts; not the BIN headline."""
        cur = span
        for _ in range(18):
            if cur is None:
                break
            tid = (cur.get("data-testid") or "").lower()
            if any(
                x in tid
                for x in (
                    "afterpay",
                    "zip-pay",
                    "zipmoney",
                    "klarna",
                    "laybuy",
                    "splitpay",
                    "installment",
                    "pay-in-4",
                    "payin4",
                )
            ):
                return True
            cls = " ".join(cur.get("class") or []).lower()
            if any(
                x in cls
                for x in (
                    "x-payment-message",
                    "payment-unfold",
                    "installment",
                    "afterpay",
                    "zip-pay",
                )
            ):
                return True
            cur = getattr(cur, "parent", None)
        return False

    @staticmethod
    def _walk_json_values(obj: Any) -> Iterator[Dict[str, Any]]:
        if isinstance(obj, dict):
            yield obj
            for v in obj.values():
                yield from EbayParser._walk_json_values(v)
        elif isinstance(obj, list):
            for x in obj:
                yield from EbayParser._walk_json_values(x)

    @classmethod
    def _listing_item_id(cls, soup: BeautifulSoup, html: str) -> Optional[str]:
        """Numeric listing id from canonical / og:url / raw HTML (for scoped JSON extraction)."""
        itm_re = re.compile(r"/itm/(?:[^/?#\"]+/)?(\d{10,})(?:[?#\"']|/|$)", re.IGNORECASE)

        for link in soup.find_all("link", rel=True):
            rel = link.get("rel")
            if isinstance(rel, (list, tuple)):
                rel_l = " ".join(str(x) for x in rel).lower()
            else:
                rel_l = (str(rel) if rel else "").lower()
            if "canonical" in rel_l and link.get("href"):
                m = itm_re.search(str(link["href"]))
                if m:
                    return m.group(1)
        og = soup.find("meta", attrs={"property": "og:url"})
        if og and og.get("content"):
            m = itm_re.search(str(og["content"]))
            if m:
                return m.group(1)
        m = itm_re.search(html or "")
        if m:
            return m.group(1)
        return None

    _CANONICAL_OG_ITM_RE = re.compile(
        r'(?:href|content)=["\']https?://(?:www\.)?ebay\.(?:com\.au|com|ca)/itm/(?:[^"\']*?/)?(\d{10,})(?:[?#][^"\']*)?["\']',
        re.IGNORECASE,
    )

    @classmethod
    def _canonical_itm_index(cls, html: str, item_id: str) -> int:
        """Byte offset of this listing's canonical / og URL (stable anchor vs stray ``itemId`` in recs JSON)."""
        if not html or not item_id:
            return -1
        for m in cls._CANONICAL_OG_ITM_RE.finditer(html):
            if m.group(1) == item_id:
                return m.start()
        return -1

    @classmethod
    def _item_id_marker_positions(cls, html: str, item_id: str) -> list[int]:
        """All offsets of this listing id inside JSON-like blobs."""
        needles = (
            f'"itemId":"{item_id}"',
            f'"itemId":{item_id}',
            f'"legacyItemId":"{item_id}"',
            f'"legacyItemId":{item_id}',
        )
        pos: set[int] = set()
        for needle in needles:
            start = 0
            while True:
                j = html.find(needle, start)
                if j < 0:
                    break
                pos.add(j)
                start = j + 1
        return sorted(pos)

    @classmethod
    def _item_model_json_anchor_index(cls, html: str, item_id: str) -> int:
        """Prefer ``itemId`` for this listing in the main model (first match at/after canonical URL)."""
        positions = cls._item_id_marker_positions(html, item_id)
        if not positions:
            return -1
        cidx = cls._canonical_itm_index(html, item_id)
        if cidx < 0:
            return positions[0]
        after = [p for p in positions if p >= cidx]
        return min(after) if after else positions[0]

    @classmethod
    def _item_model_json_price_candidates(cls, html: str, item_id: Optional[str]) -> list[float]:
        """BIN-related amounts from the preloaded item model near ``itemId`` (seller discounts often only here)."""
        if not html or not item_id:
            return []
        idx = cls._item_model_json_anchor_index(html, item_id)
        if idx < 0:
            return []
        chunk = html[max(0, idx - 12_000) : idx + 320_000]
        found: list[float] = []
        for pat in cls.ITEM_MODEL_PRICE_JSON_PATTERNS:
            for m in re.finditer(pat, chunk):
                p = parse_price_text(m.group(1))
                if p and 0.01 <= p < 999_999:
                    found.append(p)
        return found

    @classmethod
    def _ld_json_product_offer_prices(cls, soup: BeautifulSoup, item_id: Optional[str]) -> list[float]:
        """Schema.org Product / offers price when tied to this listing (supplements thin SSR DOM)."""
        found: list[float] = []
        for script in soup.find_all("script", type="application/ld+json"):
            raw = (script.string or script.get_text() or "").strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue
            for node in cls._walk_json_values(data):
                types = node.get("@type")
                if types is None:
                    continue
                if isinstance(types, str):
                    type_names = {types.lower()}
                elif isinstance(types, list):
                    type_names = {str(t).lower() for t in types}
                else:
                    continue
                if "product" not in type_names:
                    continue
                if item_id:
                    blob = json.dumps(node, default=str, ensure_ascii=False)
                    if item_id not in blob:
                        continue
                offers = node.get("offers")
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                if not isinstance(offers, dict):
                    continue
                for key in ("price", "lowPrice", "highPrice"):
                    val = offers.get(key)
                    if not val:
                        continue
                    p = parse_price_text(str(val))
                    if p and 0.01 <= p < 999_999:
                        found.append(p)
        return found

    @classmethod
    def _ux_textspan_prices_in_subtree(cls, root) -> list[float]:
        """Prices from ``.ux-textspans`` and ``[itemprop=price]`` under ``root`` (non-struck, not noise)."""
        found: list[float] = []
        if root is None:
            return found
        for el in root.select(".ux-textspans"):
            if el.name not in ("span", "div", "p"):
                continue
            if cls._ux_span_installment_payment_row(el):
                continue
            if cls._under_price_noise(el):
                continue
            if cls._is_strikethrough_element(el):
                continue
            t = el.get_text(strip=True)
            if not t or len(t) > 120:
                continue
            p = _parse_ebay_display_price_text(t)
            if p and 0.01 <= p < 999_999:
                found.append(p)
        for mp in root.select("[itemprop='price']"):
            raw = (mp.get("content") or mp.get_text(strip=True) or "").strip()
            if not raw:
                continue
            if cls._under_price_noise(mp):
                continue
            if cls._is_strikethrough_element(mp):
                continue
            p = _parse_ebay_display_price_text(raw)
            if p and 0.01 <= p < 999_999:
                found.append(p)
        return found

    @classmethod
    def _collect_bin_price_candidates(cls, root) -> list:
        """Headline BIN amounts: ``span.ux-textspans`` in primary BIN blocks (eBay AU layout).

        eBay shows the live price in ``<span class="ux-textspans">AU $35.00</span>``; skip struck rows.
        If those wrappers are absent, fall back to ``.ux-textspans`` under ``root`` (item price).
        Bloc hits do not skip a full-root scan: promo amounts often live outside primary/BIN wrappers.
        """
        found: list[float] = []
        if root is None:
            return found
        bloc_selectors = (
            "[data-testid='x-price-primary'], [data-testid='x-bin-price'], "
            ".x-price-primary, .x-bin-price, .x-bin-price__content"
        )
        for bloc in root.select(bloc_selectors):
            if cls._under_price_noise(bloc):
                continue
            bloc_prices: list[float] = []
            spans = bloc.select(".ux-textspans")
            for span in spans:
                if span.name not in ("span", "div", "p"):
                    continue
                if cls._ux_span_installment_payment_row(span):
                    continue
                if cls._is_strikethrough_element(span):
                    continue
                t = span.get_text(strip=True)
                if not t or len(t) > 120:
                    continue
                p = _parse_ebay_display_price_text(t)
                if p and 0.01 <= p < 999_999:
                    bloc_prices.append(p)
            if bloc_prices:
                found.extend(bloc_prices)
            elif not spans:
                p_whole = cls._price_from_element(bloc)
                if p_whole and 0.01 <= p_whole < 999_999:
                    found.append(p_whole)
        # Always scan the full item-price root: sale lines often sit outside x-price-primary / x-bin-price.
        for span in root.select(".ux-textspans"):
            if span.name not in ("span", "div", "p"):
                continue
            if not cls._ux_span_outside_noise_regions(span):
                continue
            if cls._ux_span_installment_payment_row(span):
                continue
            if cls._is_strikethrough_element(span):
                continue
            t = span.get_text(strip=True)
            if not t or len(t) > 120:
                continue
            p = _parse_ebay_display_price_text(t)
            if p and 0.01 <= p < 999_999:
                found.append(p)
        return found

    _PRIMARY_BIN_SELECTORS = (
        "[data-testid='x-price-primary']",
        "[data-test-id='x-price-primary']",
        ".x-price-primary",
    )
    _BIN_ROW_SELECTORS = (
        "[data-testid='x-bin-price']",
        ".x-bin-price__content",
        ".x-bin-price",
    )

    @classmethod
    def _headline_prices_in_nodes(cls, nodes) -> list[float]:
        found: list[float] = []
        for node in nodes:
            if node is None or cls._under_price_noise(node):
                continue
            found.extend(cls._ux_textspan_prices_in_subtree(node))
        return found

    @classmethod
    def _pick_headline_bin_price(cls, candidates: list[float]) -> Optional[float]:
        if not candidates:
            return None
        return min(candidates)

    @classmethod
    def _is_ebay_au_page(cls, soup: BeautifulSoup, html: str = "") -> bool:
        """True when the HTML is from ebay.com.au (canonical / og:url / host hints)."""
        for link in soup.find_all("link", rel=True):
            rel = link.get("rel")
            if isinstance(rel, (list, tuple)):
                rel_l = " ".join(str(x) for x in rel).lower()
            else:
                rel_l = (str(rel) if rel else "").lower()
            href = (link.get("href") or "").lower()
            if "canonical" in rel_l and "ebay.com.au" in href:
                return True
        og = soup.find("meta", attrs={"property": "og:url"})
        if og and og.get("content") and "ebay.com.au" in str(og["content"]).lower():
            return True
        sample = (html or "")[:80_000].lower()
        return "ebay.com.au" in sample

    @classmethod
    def _au_primary_headline_price(cls, soup: BeautifulSoup, root=None) -> Optional[float]:
        """First valid price from ``[data-testid='x-price-primary'] span`` (eBay AU)."""
        scopes: list = []
        if root is not None:
            scopes.append(root)
        scopes.append(soup)
        for scope in scopes:
            if scope is None:
                continue
            for sel in cls._AU_PRIMARY_HEADLINE_SELECTORS:
                for span in scope.select(sel):
                    if span.name not in ("span", "div", "p"):
                        continue
                    if cls._ux_span_installment_payment_row(span):
                        continue
                    if cls._is_strikethrough_element(span):
                        continue
                    t = span.get_text(strip=True)
                    if not t or "approximately" in t.lower():
                        continue
                    p = _parse_ebay_display_price_text(t)
                    if p and 0.01 <= p < 999_999:
                        return p
        return None

    @classmethod
    def _us_bin_headline_price(cls, soup: BeautifulSoup, root=None) -> Optional[float]:
        """First valid price from the US BIN headline span (``.x-bin-price .x-price-primary span``)."""
        scopes: list = []
        if root is not None:
            scopes.append(root)
        scopes.append(soup)
        for scope in scopes:
            if scope is None:
                continue
            for sel in cls._US_BIN_HEADLINE_SELECTORS:
                for span in scope.select(sel):
                    if span.name not in ("span", "div", "p"):
                        continue
                    if cls._ux_span_installment_payment_row(span):
                        continue
                    if cls._is_strikethrough_element(span):
                        continue
                    t = span.get_text(strip=True)
                    p = _parse_ebay_display_price_text(t)
                    if p and 0.01 <= p < 999_999:
                        return p
        return None

    @classmethod
    def _buy_now_display_price(cls, soup: BeautifulSoup, html: str = "") -> Optional[float]:
        """Headline BIN from item-price section (primary + BIN row), then JSON supplements."""
        headline: list[float] = []
        item_section = None
        for sec_sel in cls._ITEM_PRICE_SECTION_SELECTORS:
            item_section = soup.select_one(sec_sel)
            if item_section is not None:
                break

        if cls._is_ebay_au_page(soup, html):
            au_headline = cls._au_primary_headline_price(soup, item_section)
            if au_headline is not None:
                # Seller promos are sometimes ONLY in the item-model JSON (DOM still shows
                # pre-discount). Allow JSON / LD-JSON to override IF strictly lower. Do NOT
                # scan promo banners (".x-coupon-pricing", "Extra $100 off ... with code")
                # which would pollute the candidate list with coupon dollar amounts.
                supplements: list[float] = []
                item_id = cls._listing_item_id(soup, html or "")
                supplements.extend(cls._item_model_json_price_candidates(html or "", item_id))
                supplements.extend(cls._ld_json_product_offer_prices(soup, item_id))
                lower = [p for p in supplements if p < au_headline - 0.001]
                if lower:
                    return min(lower)
                return au_headline

        us_headline = cls._us_bin_headline_price(soup, item_section)
        if us_headline is not None:
            return us_headline

        if item_section is not None:
            for sel in cls._PRIMARY_BIN_SELECTORS:
                headline.extend(cls._headline_prices_in_nodes(item_section.select(sel)))
            for sel in cls._BIN_ROW_SELECTORS:
                headline.extend(cls._headline_prices_in_nodes(item_section.select(sel)))
            headline.extend(cls._collect_bin_price_candidates(item_section))

        if not headline:
            headline.extend(
                cls._headline_prices_in_nodes(soup.select(", ".join(cls._PRIMARY_BIN_SELECTORS)))
            )

        item_id = cls._listing_item_id(soup, html or "")
        headline.extend(cls._item_model_json_price_candidates(html or "", item_id))
        headline.extend(cls._ld_json_product_offer_prices(soup, item_id))
        return cls._pick_headline_bin_price(headline)

    @staticmethod
    def _price_from_element(elem) -> Optional[float]:
        if elem is None:
            return None
        text = elem.get_text(strip=True)
        if not text:
            return None
        if " to " in text.lower():
            parts = re.split(r"\s+to\s+", text, flags=re.IGNORECASE)
            return _parse_ebay_display_price_text(parts[0])
        return _parse_ebay_display_price_text(text)

    @classmethod
    def extract_price(cls, soup: BeautifulSoup, html: str) -> Optional[float]:
        listing_type = cls.detect_listing_type(soup, html)

        if listing_type == "buy_now":
            display = cls._buy_now_display_price(soup, html)
            if display is not None:
                return display

        for sec_sel in cls._ITEM_PRICE_SECTION_SELECTORS:
            section = soup.select_one(sec_sel)
            if not section:
                continue
            for sel in cls.PRIMARY_PRICE_SELECTORS:
                p = cls._price_from_element(section.select_one(sel))
                if p:
                    return p

        for sel in cls.PRIMARY_PRICE_SELECTORS:
            p = cls._price_from_element(soup.select_one(sel))
            if p:
                return p

        for sel in cls.FALLBACK_PRICE_SELECTORS:
            p = cls._price_from_element(soup.select_one(sel))
            if p:
                return p

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

        for pat in cls.PRICE_JSON_PATTERNS_PRIMARY:
            m = re.search(pat, html)
            if m:
                p = parse_price_text(m.group(1))
                if p:
                    return p

        for pat in cls.PRICE_JSON_PATTERNS_FALLBACK:
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

    @classmethod
    def _au_shipping_line_is_noise(cls, text: str) -> bool:
        lower = (text or "").lower()
        if not lower or len(lower) > 200:
            return True
        return any(p in lower for p in cls._AU_SHIPPING_SKIP_PHRASES)

    @classmethod
    def _amount_from_au_shipping_line(cls, text: str) -> Optional[float]:
        if not text or cls._au_shipping_line_is_noise(text):
            return None
        lower = text.lower()
        if ("free postage" in lower or "free delivery" in lower or "free shipping" in lower):
            if "$" not in text and "au" not in lower:
                return 0.0
        m = _RE_AU_DELIVERY_PRICE_LINE.search(text)
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                pass
        return _parse_ebay_display_price_text(text)

    @classmethod
    def _extract_au_shipping_from_shipping_block(cls, soup: BeautifulSoup) -> Optional[float]:
        block = soup.select_one(cls.AU_SHIPPING_BLOCK_SELECTOR)
        if not block:
            return None

        amounts: list[float] = []
        for node in block.select("div, span, p, li"):
            text = node.get_text(" ", strip=True)
            amt = cls._amount_from_au_shipping_line(text)
            if amt is not None:
                amounts.append(amt)

        if amounts:
            # Prefer the smallest positive amount (postage, not a bundled total).
            positive = [a for a in amounts if a > 0]
            if positive:
                return min(positive)
            return 0.0
        return None

    @classmethod
    def _extract_au_shipping_from_embedded_html(cls, html: str) -> Optional[float]:
        if not html:
            return None

        amounts: list[float] = []
        for pat in _AU_SHIPPING_JSON_PATTERNS:
            for m in pat.finditer(html):
                try:
                    val = float(m.group(1))
                except ValueError:
                    continue
                if 0.0 <= val < 999_999:
                    amounts.append(val)

        if amounts:
            positive = [a for a in amounts if a > 0]
            if positive:
                return min(positive)
            return 0.0

        for m in _RE_AU_DELIVERY_PRICE_LINE.finditer(html):
            try:
                val = float(m.group(1).replace(",", ""))
            except ValueError:
                continue
            if 0.01 <= val < 999_999:
                return val

        return None

    @classmethod
    def extract_au_shipping_amount(cls, soup: BeautifulSoup, html: str = "") -> Optional[float]:
        """Return the paid postage amount from the AU shipping row, or ``None``.

        Scans every line in the postage block (the first div is often
        ``Item doesn't post to you`` when no delivery postcode is set).
        Falls back to shipping-specific JSON / ``AU $X delivery`` snippets
        embedded in the raw HTML.
        """
        values_el = soup.select_one(cls.AU_SHIPPING_VALUES_SELECTOR)
        if values_el:
            values_text = values_el.get_text(" ", strip=True)
            if values_text and "free" in values_text.lower():
                return 0.0

        from_block = cls._extract_au_shipping_from_shipping_block(soup)
        if from_block is not None:
            return from_block

        if html:
            return cls._extract_au_shipping_from_embedded_html(html)

        return None


class EbayHTTP:
    @staticmethod
    def _get_client(session_dict: dict, market: str, *, proxy_url: str | None = None):
        _migrate_legacy_ebay_session(session_dict, market)
        proxy_key = _ebay_sk(market, "http_proxy_url")
        client_key = _ebay_sk(market, "http_client")
        bound_proxy = (session_dict or {}).get(proxy_key)
        if proxy_url is not None and bound_proxy != proxy_url:
            old = (session_dict or {}).pop(client_key, None)
            if old:
                try:
                    old.close()
                except Exception:
                    pass
            if session_dict is not None:
                session_dict[proxy_key] = proxy_url

        client = session_dict.get(client_key) if session_dict else None
        if client:
            return client

        try:
            from curl_cffi import requests as curl_requests

            client = curl_requests.Session()
            if session_dict is not None:
                session_dict[client_key] = client
            return client
        except Exception:
            logger.debug("curl_cffi unavailable for eBay HTTP session")
            return None

    @staticmethod
    def _get_headers(url: str, region: str, session_dict: dict, market: str) -> Dict[str, str]:
        is_au = (market == EBAY_MARKET_AU) or (region or "").upper() == "AU"

        ua = None
        if session_dict is not None:
            ua = session_dict.get(_ebay_sk(market, "last_user_agent"))
        if not ua:
            # For AU pin a UA that matches curl_cffi ``impersonate="chrome131"``; mixing
            # Edge / Chrome 132 UAs with Chrome 131 TLS fingerprint is a strong bot signal
            # and was correlated with the ``http_403`` cascade we hit in production logs.
            ua = _CHROME_131_WIN_UA if is_au else _random_user_agent()
            if session_dict is not None:
                session_dict[_ebay_sk(market, "last_user_agent")] = ua

        # AU PDPs respond differently when the client advertises en-AU. We were sending
        # ``en-US,en;q=0.9`` to ``ebay.com.au`` which trips bot heuristics; Costco AU
        # on the same proxy pool works fine because it sends ``en-AU,en;q=0.9``.
        accept_language = (
            "en-AU,en;q=0.9,en-US;q=0.8" if is_au else "en-US,en;q=0.9"
        )

        return {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": accept_language,
            "Referer": _ebay_region_referer(region),
            "Cache-Control": "max-age=0",
            "Pragma": "no-cache",
            "Upgrade-Insecure-Requests": "1",
        }

    @classmethod
    def _fetch_via_proxies(
        cls,
        url: str,
        region: str,
        session_dict: dict,
        market: str,
        headers: Dict[str, str],
    ) -> Tuple[Optional[str], Optional[int], str]:
        proxies = _ebay_proxy_module(market)
        if not proxies.proxies_configured():
            return None, None, ""

        pool = _ebay_proxy_pool(market)
        market_label = "AU" if market == EBAY_MARKET_AU else "US"
        last_err = "proxy_exhausted"
        attempts = proxies.http_retry_limit()

        for attempt in range(attempts):
            raise_if_scrape_stopped()
            assignment = proxies.acquire_proxy(session_dict, force_rotate=attempt > 0)
            if assignment is None:
                break
            pool.wait_for_gap(assignment)
            proxies.remember_proxy(session_dict, assignment)

            client = cls._get_client(session_dict, market, proxy_url=assignment.url)
            if client is None:
                return None, None, "curl_cffi_not_installed"
            proxies.bind_curl_client(client, assignment)

            try:
                resp = client.get(
                    url,
                    headers=headers,
                    timeout=_ebay_http_timeout_sec(),
                    allow_redirects=True,
                    impersonate="chrome131",
                )
            except Exception as exc:
                last_err = f"http_error: {exc}"
                proxies.mark_proxy_blocked(assignment)
                continue

            html = getattr(resp, "text", "") or ""
            status = getattr(resp, "status_code", None)

            if session_dict is not None:
                session_dict[_ebay_sk(market, "last_http_url")] = getattr(resp, "url", url)

            if status != 200:
                last_err = f"http_{status}"
                # A missing item is the same on every proxy. Return it as-is.
                if status in (404, 410):
                    proxies.mark_proxy_success(assignment)
                    return html, status, last_err
                proxies.mark_proxy_blocked(assignment)
                continue

            blocked, reason = _is_challenge_or_blocked(html)
            if blocked:
                last_err = reason
                proxies.mark_proxy_blocked(assignment)
                continue

            if not _looks_like_product_html(html):
                # Parser/page-shape miss is NOT a proxy-quality signal — don't burn the
                # proxy. Rotate to a fresh one for the next try, keep this one available.
                last_err = "not_product_like"
                continue

            proxies.mark_proxy_success(assignment)
            logger.info(
                "eBay %s HTTP via proxy OK proxy=%s url=%s",
                market_label,
                assignment.label,
                url[:70],
            )
            return html, status, ""

        return None, None, last_err

    @classmethod
    def fetch(cls, url: str, region: str, session_dict: dict, market: str) -> Tuple[Optional[str], Optional[int], str]:
        headers = cls._get_headers(url, region, session_dict, market)

        if market in (EBAY_MARKET_AU, EBAY_MARKET_US):
            html, status, err = cls._fetch_via_proxies(
                url, region, session_dict or {}, market, headers,
            )
            if html is not None or err not in ("", "proxy_exhausted"):
                return html, status, err

        client = cls._get_client(session_dict, market)
        if client is None:
            return None, None, "curl_cffi_not_installed"

        headers = cls._get_headers(url, region, session_dict, market)

        try:
            resp = client.get(
                url,
                headers=headers,
                timeout=_ebay_http_timeout_sec(),
                allow_redirects=True,
                impersonate="chrome131",
            )
        except Exception as exc:
            return None, None, f"http_error: {exc}"

        html = getattr(resp, "text", "") or ""
        status = getattr(resp, "status_code", None)

        if session_dict is not None:
            session_dict[_ebay_sk(market, "last_http_url")] = getattr(resp, "url", url)

        if status != 200:
            return html, status, f"http_{status}"

        blocked, reason = _is_challenge_or_blocked(html)
        if blocked:
            return html, status, reason

        if not _looks_like_product_html(html):
            return html, status, "not_product_like"

        return html, status, ""

    @classmethod
    def import_cookies_from_selenium(cls, driver, region: str, session_dict: dict, market: str):
        client = cls._get_client(session_dict, market)
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
                session_dict[_ebay_sk(market, "last_user_agent")] = ua
        except Exception:
            pass


def _load_us_saved_cookies() -> list[dict]:
    """Browser cookies for ebay.com from ``EBAY_US_COOKIES_JSON`` or ``EBAY_US_COOKIES_FILE``.

    Same JSON format as the eBay AU cookie file: a list of cookie objects
    exported from a real browser (not logged in to an eBay account).
    """
    inline = (os.environ.get("EBAY_US_COOKIES_JSON") or "").strip()
    path = (os.environ.get("EBAY_US_COOKIES_FILE") or "").strip()
    sources: list = []
    if inline:
        sources.append(("EBAY_US_COOKIES_JSON", inline))
    elif path:
        # ebay_ca_cookies.json next to it: Chrome tries ebay.ca first and only
        # takes cookies for the site it is on.
        for p in (path, os.path.join(os.path.dirname(path), "ebay_ca_cookies.json")):
            if os.path.isfile(p):
                sources.append((p, None))
    out: list[dict] = []
    for name, text in sources:
        try:
            if text is None:
                with open(name, encoding="utf-8") as f:
                    data = json.load(f)
            else:
                data = json.loads(text)
        except Exception as exc:
            logger.warning("eBay US saved cookies unreadable (%s): %s", os.path.basename(name), exc)
            continue
        # A plain list, or the extension's {"url": ..., "cookies": [...]} export.
        if isinstance(data, dict):
            data = data.get("cookies")
        if isinstance(data, list):
            out.extend(c for c in data if isinstance(c, dict))
    return out


def _add_us_saved_cookies(driver) -> None:
    """Give a new US Chrome the saved browser cookies (it must be on ebay.com already)."""
    cookies = _load_us_saved_cookies()
    added = 0
    for cookie in cookies:
        payload = {k: v for k, v in cookie.items() if k in ("name", "value", "domain", "path", "secure", "httpOnly", "expiry", "sameSite")}
        if "expiry" not in payload and isinstance(cookie.get("expirationDate"), (int, float)):
            payload["expiry"] = int(cookie["expirationDate"])
        if payload.get("sameSite") not in ("Strict", "Lax", "None"):
            payload.pop("sameSite", None)
        if not payload.get("name"):
            continue
        try:
            driver.add_cookie(payload)
            added += 1
        except Exception:
            continue
    if cookies:
        host = ""
        try:
            host = urlparse(driver.current_url).netloc
        except Exception:
            pass
        logger.info(
            "eBay US: loaded %d of %d saved browser cookies into Chrome on %s",
            added, len(cookies), host or "?",
        )


def ebay_us_chrome_headful() -> bool:
    """eBay US Chrome runs windowed on a virtual screen (``EBAY_US_CHROME_HEADFUL``, default off).

    On the eBay US proxies it got through no more often than headless Chrome
    (about 1 try in 5) and took longer to start.
    """
    return _env_on("EBAY_US_CHROME_HEADFUL", "0")


_XVFB: dict = {"proc": None, "display": None, "warned": False}


def _reset_us_virtual_display() -> None:
    proc = _XVFB.get("proc")
    _XVFB.update(proc=None, display=None)
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass


def _us_virtual_display() -> str | None:
    """This worker process's virtual screen (``:N``), started on first use; None = headless.

    ``-terminate`` makes the screen close by itself when its last Chrome closes,
    so a worker process that exits never leaves one behind; the next Chrome
    starts a new one.
    """
    if not ebay_us_chrome_headful():
        return None
    proc = _XVFB.get("proc")
    if proc is not None and proc.poll() is None:
        return _XVFB["display"]
    import shutil
    import subprocess

    xvfb = shutil.which("Xvfb")
    if not xvfb:
        if not _XVFB["warned"]:
            _XVFB["warned"] = True
            logger.warning("eBay US: Xvfb is not installed; Chrome runs headless.")
        return None
    read_fd, write_fd = os.pipe()
    number = ""
    proc = None
    try:
        proc = subprocess.Popen(
            [xvfb, "-displayfd", str(write_fd), "-screen", "0", "1920x1080x24",
             "-nolisten", "tcp", "-terminate"],
            pass_fds=(write_fd,),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        os.close(write_fd)
        write_fd = -1
        import select

        ready, _, _ = select.select([read_fd], [], [], 10)
        if ready:
            number = os.read(read_fd, 32).decode().strip()
    except Exception as exc:
        logger.warning("eBay US: virtual screen failed to start: %s", exc)
    finally:
        if write_fd >= 0:
            os.close(write_fd)
        os.close(read_fd)
    if not number.isdigit():
        if proc is not None and proc.poll() is None:
            proc.kill()
        return None
    _XVFB.update(proc=proc, display=f":{number}")
    logger.info("eBay US: Chrome runs on virtual screen :%s", number)
    return _XVFB["display"]


_US_PROFILE_SCRIPT = """
(() => {
  const spoof = (proto) => {
    if (!proto) return;
    const orig = proto.getParameter;
    proto.getParameter = function (p) {
      if (p === 37445) return 'Google Inc. (Intel)';
      if (p === 37446) return 'ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)';
      return orig.call(this, p);
    };
  };
  spoof(window.WebGLRenderingContext && WebGLRenderingContext.prototype);
  spoof(window.WebGL2RenderingContext && WebGL2RenderingContext.prototype);
  Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
  Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});
  Object.defineProperty(navigator, 'maxTouchPoints', {get: () => 0});
})();
"""


def us_windows_user_agent(real_user_agent: str) -> tuple[str, str, str]:
    """(user agent, major, full version): Windows Chrome at the real Chrome's version."""
    m = re.search(r"Chrome/(\d+)\.(\d+\.\d+\.\d+)", real_user_agent or "")
    major, full = (m.group(1), f"{m.group(1)}.{m.group(2)}") if m else ("131", "131.0.0.0")
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
    )
    return ua, major, full


def _apply_us_windows_profile(driver) -> None:
    real = driver.execute_script("return navigator.userAgent") or ""
    ua, major, full = us_windows_user_agent(real)
    brands = [("Chromium", major, full), ("Google Chrome", major, full), ("Not_A Brand", "24", "24.0.0.0")]
    driver.execute_cdp_cmd(
        "Network.setUserAgentOverride",
        {
            "userAgent": ua,
            "acceptLanguage": "en-US,en;q=0.9",
            "platform": "Win32",
            "userAgentMetadata": {
                "brands": [{"brand": b, "version": v} for b, v, _ in brands],
                "fullVersionList": [{"brand": b, "version": f} for b, _, f in brands],
                "fullVersion": full,
                "platform": "Windows",
                "platformVersion": "10.0.0",
                "architecture": "x86",
                "bitness": "64",
                "model": "",
                "mobile": False,
                "wow64": False,
            },
        },
    )
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": _US_PROFILE_SCRIPT})


class EbayDriver:
    @staticmethod
    def _create_driver(
        *, proxy_url: str | None = None, native_ua: bool = False, us_profile: bool = False,
    ):
        """Headless Chrome for eBay.

        ``native_ua`` keeps Chrome's own browser identity (with "HeadlessChrome"
        renamed) instead of a random one. ``us_profile`` (eBay US) presents one
        consistent Windows Chrome: the same version and platform in the browser
        name, its client hints and the page's navigator, and a normal graphics card.
        A browser whose signals disagree is one of the first things a bot wall checks.
        """
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service

        options = Options()
        # eBay US runs as a normal windowed Chrome on a virtual screen: eBay's
        # bot wall recognises headless Chrome. Without the screen, headless.
        display = _us_virtual_display() if us_profile else None
        if display is None:
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

        if not native_ua and not us_profile:
            ua = _random_user_agent()
            options.add_argument(f"--user-agent={ua}")

        if proxy_url:
            from .ebay_au_proxies import proxy_chrome_arg

            options.add_argument(f"--proxy-server={proxy_chrome_arg(proxy_url)}")

        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        chrome_bin = os.environ.get("CHROME_BIN")
        if chrome_bin:
            options.binary_location = chrome_bin

        chromedriver_path = os.environ.get("CHROMEDRIVER_PATH")
        service_env = {**os.environ, "DISPLAY": display} if display else None
        if chromedriver_path and os.path.isfile(chromedriver_path):
            service = Service(executable_path=chromedriver_path, env=service_env)
        else:
            service = Service(env=service_env)

        try:
            driver = webdriver.Chrome(service=service, options=options)
        except Exception:
            if not display:
                raise
            # The virtual screen may have just closed with the last Chrome; fall
            # back to headless for this one rather than lose the listing.
            _reset_us_virtual_display()
            options.add_argument("--headless=new")
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

        if native_ua:
            try:
                real = driver.execute_script("return navigator.userAgent") or ""
                if "HeadlessChrome" in real:
                    driver.execute_cdp_cmd(
                        "Network.setUserAgentOverride",
                        {"userAgent": real.replace("HeadlessChrome", "Chrome")},
                    )
            except Exception as exc:
                logger.debug("eBay Chrome user agent override failed: %s", exc)
        elif us_profile:
            try:
                _apply_us_windows_profile(driver)
            except Exception as exc:
                logger.warning("eBay US Chrome profile failed: %s", exc)

        return driver

    @staticmethod
    def _us_proxy_url(session_dict: dict | None, *, force_rotate: bool = False) -> str | None:
        """Next eBay US proxy for Chrome, or None to use the server's own IP."""
        if session_dict is None or not _us_browser_proxies_active():
            return None
        from . import ebay_us_proxies

        if not ebay_us_proxies.proxies_configured():
            return None
        if ebay_us_proxies.shared_mode():
            # Every worker sees which proxies are resting or taken; waits for a
            # free one (raises ProxyPoolBusy) instead of reusing a refused IP.
            current = session_dict.get(_ebay_sk(EBAY_MARKET_US, "browser_proxy"))
            assignment = ebay_us_proxies.acquire_shared_wait(current, force_rotate=force_rotate)
        else:
            assignment = ebay_us_proxies.acquire_proxy(session_dict, force_rotate=force_rotate)
        if assignment is None:
            return None
        ebay_us_proxies.get_ebay_us_pool().wait_for_gap(assignment)
        ebay_us_proxies.remember_proxy(session_dict, assignment)
        session_dict[_ebay_sk(EBAY_MARKET_US, "browser_proxy")] = assignment
        return assignment.url

    @staticmethod
    def mark_us_browser_proxy_blocked(session_dict: dict | None) -> None:
        """Cool down the proxy that Chrome was refused on; the next try rotates."""
        if not session_dict:
            return
        assignment = session_dict.get(_ebay_sk(EBAY_MARKET_US, "browser_proxy"))
        if assignment is None:
            return
        from . import ebay_us_proxies

        ebay_us_proxies.mark_proxy_blocked(assignment)

    @staticmethod
    def _au_proxy_url(session_dict: dict | None, *, force_rotate: bool = False) -> str | None:
        if session_dict is None:
            return None
        from .ebay_au_proxies import (
            acquire_proxy,
            get_ebay_au_pool,
            proxies_configured,
            remember_proxy,
        )

        if not proxies_configured():
            return None
        pool = get_ebay_au_pool()
        assignment = acquire_proxy(session_dict, force_rotate=force_rotate)
        if assignment is None:
            return None
        pool.wait_for_gap(assignment)
        remember_proxy(session_dict, assignment)
        return assignment.url

    @staticmethod
    def get_or_create(session_dict: dict, market: str, *, force_proxy_rotate: bool = False):
        _migrate_legacy_ebay_session(session_dict, market)
        key = _ebay_sk(market, "selenium_driver")
        proxy_key = _ebay_sk(market, "selenium_proxy_url")

        proxy_url = None
        us_browser = market == EBAY_MARKET_US and ebay_us_browser_first()
        if market == EBAY_MARKET_AU:
            proxy_url = EbayDriver._au_proxy_url(session_dict, force_rotate=force_proxy_rotate)
        elif us_browser:
            proxy_url = EbayDriver._us_proxy_url(session_dict, force_rotate=force_proxy_rotate)

        if session_dict is not None and key in session_dict:
            driver = session_dict[key]
            bound_proxy = session_dict.get(proxy_key)
            # A US Chrome that was on a proxy is rebuilt when proxies are switched off.
            proxy_changed = bound_proxy != proxy_url and (proxy_url or us_browser)
            if proxy_changed:
                try:
                    driver.quit()
                except Exception:
                    pass
                session_dict.pop(key, None)
            else:
                try:
                    _ = driver.title
                    return driver
                except Exception:
                    try:
                        driver.quit()
                    except Exception:
                        pass
                    session_dict.pop(key, None)

        native = us_browser and ebay_us_chrome_native_ua()
        driver = EbayDriver._create_driver(
            proxy_url=proxy_url, native_ua=native, us_profile=us_browser and not native,
        )
        if session_dict is not None:
            session_dict[key] = driver
            if proxy_url:
                session_dict[proxy_key] = proxy_url
            else:
                session_dict.pop(proxy_key, None)
            if us_browser:
                # A new browser has none of the old one's cookies: open the home
                # page (and load saved cookies) again before the next item.
                session_dict.pop(_ebay_sk(market, "browser_warmed"), None)
        return driver

    @staticmethod
    def invalidate_au_proxy(session_dict: dict | None) -> None:
        """Close Selenium and force the next AU fetch onto a fresh proxy."""
        if session_dict is None:
            return
        EbayDriver.close(session_dict, EBAY_MARKET_AU)
        session_dict.pop(_ebay_sk(EBAY_MARKET_AU, "selenium_proxy_url"), None)
        session_dict.pop(_ebay_sk(EBAY_MARKET_AU, "http_proxy_url"), None)
        from .ebay_au_proxies import rotate_proxy

        rotate_proxy(session_dict)

    @staticmethod
    def close(session_dict: dict, market: str):
        """Close Selenium driver only (use close_ebay_market_session for full teardown)."""
        key = _ebay_sk(market, "selenium_driver")
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
    def _apply_region_browser_profile(driver, region: str, item_url: str) -> None:
        """AU PDPs often depend on locale headers/timezone for seller discount / marketing price."""
        au = _effective_ebay_region(region, item_url) == "AU"
        try:
            driver.execute_cdp_cmd("Network.enable", {})
        except Exception:
            pass
        try:
            if au:
                driver.execute_cdp_cmd(
                    "Network.setExtraHTTPHeaders",
                    {"headers": {"Accept-Language": "en-AU,en;q=0.9"}},
                )
                driver.execute_cdp_cmd(
                    "Emulation.setTimezoneOverride",
                    {"timezoneId": "Australia/Sydney"},
                )
                driver.execute_cdp_cmd("Emulation.setLocaleOverride", {"locale": "en-AU"})
            else:
                driver.execute_cdp_cmd(
                    "Network.setExtraHTTPHeaders",
                    {"headers": {"Accept-Language": "en-US,en;q=0.9"}},
                )
                driver.execute_cdp_cmd(
                    "Emulation.setTimezoneOverride",
                    {"timezoneId": "America/New_York"},
                )
                driver.execute_cdp_cmd("Emulation.setLocaleOverride", {"locale": "en-US"})
        except Exception:
            pass

    @staticmethod
    def _nudge_buy_box(driver) -> None:
        """Light scroll to the price module (no long sleeps) — used while polling for late hydration."""
        from selenium.webdriver.common.by import By

        for css in (
            "[data-testid='x-item-price']",
            "[data-testid='x-price-view']",
            "[data-testid='x-bin-price']",
        ):
            try:
                elems = driver.find_elements(By.CSS_SELECTOR, css)
                if elems:
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block: 'center'});",
                        elems[0],
                    )
                    break
            except Exception:
                continue
        time.sleep(0.18 + random.uniform(0, 0.14))

    @staticmethod
    def _finalize_bin_price_hydration(
        driver,
        seed_html: str,
        region: str,
        item_url: str,
    ) -> str:
        """Poll ``page_source`` until extracted BIN stops dropping (captures post-SSR seller discounts)."""
        eff = _effective_ebay_region(region, item_url)
        max_wait = _ebay_bin_hydrate_max_seconds(eff, item_url)
        if max_wait <= 0:
            return seed_html or (driver.page_source or "")

        interval = float(os.environ.get("EBAY_BIN_HYDRATE_POLL_SEC", "0.4"))
        stable_needed = int(os.environ.get("EBAY_BIN_HYDRATE_STABLE_POLLS", "2"))
        min_elapsed = float(os.environ.get("EBAY_BIN_HYDRATE_MIN_SEC", "1.0"))

        def parse_price(html_blob: str) -> Optional[float]:
            if not html_blob:
                return None
            try:
                soup = BeautifulSoup(html_blob, "lxml")
            except Exception:
                soup = BeautifulSoup(html_blob, "html.parser")
            return EbayParser.extract_price(soup, html_blob)

        start = time.time()
        best_html = seed_html or (driver.page_source or "")
        best_price = parse_price(best_html)
        stable = 0
        nudge = 0

        while time.time() - start < max_wait:
            elapsed = time.time() - start
            EbayBrowserSession._nudge_buy_box(driver)
            nudge += 1
            if nudge % 4 == 0:
                try:
                    driver.execute_script(
                        "window.scrollBy(0, Math.min(700, 220 + Math.random()*480));"
                    )
                    time.sleep(0.22)
                    driver.execute_script("window.scrollTo(0, 0);")
                    time.sleep(0.12)
                except Exception:
                    pass

            html = driver.page_source or ""
            price = parse_price(html)

            if price is not None:
                if best_price is None or price < best_price - 0.005:
                    best_price = price
                    best_html = html
                    stable = 0
                else:
                    stable += 1
            else:
                stable += 1

            if best_price is not None and elapsed >= min_elapsed and stable >= stable_needed:
                break

            time.sleep(interval)

        try:
            EbayBrowserSession._settle_buy_box(driver)
        except Exception:
            pass
        final_html = driver.page_source or best_html
        final_p = parse_price(final_html)
        if best_price is not None and final_p is not None and final_p > best_price + 0.01:
            return best_html
        return final_html

    @staticmethod
    def _settle_buy_box(driver) -> None:
        """Scroll buy box into view and wait so promo / discounted rows can hydrate."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        settle_selectors = (
            "[data-testid='x-item-price']",
            "section.x-item-price",
            "[data-testid='x-price-view']",
            "[data-testid='x-price-primary']",
            "[data-testid='x-bin-price']",
            ".x-price-primary",
        )
        for css in settle_selectors[:3]:
            try:
                WebDriverWait(driver, 4).until(
                    EC.visibility_of_element_located((By.CSS_SELECTOR, css))
                )
                break
            except Exception:
                continue
        try:
            for sel in settle_selectors:
                elems = driver.find_elements(By.CSS_SELECTOR, sel)
                if elems:
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", elems[0])
                    break
        except Exception:
            pass
        time.sleep(0.18 + random.uniform(0.04, 0.1))
        try:
            WebDriverWait(driver, 1.5).until(
                lambda d: len(d.find_elements(By.CSS_SELECTOR, "[data-testid='x-item-price'] .ux-textspans"))
                >= 1
                or len(d.find_elements(By.CSS_SELECTOR, "[data-testid='x-bin-price'] .ux-textspans")) >= 1
                or len(d.find_elements(By.CSS_SELECTOR, "[data-testid='x-price-primary'] .ux-textspans"))
                >= 1
            )
        except Exception:
            pass
        time.sleep(0.2 + random.uniform(0.05, 0.15))

    @staticmethod
    def _wait_until_product_or_stable_challenge(
        driver, timeout: int = PAGE_WAIT_TIMEOUT, *, wait_out_challenge: bool = False,
    ) -> str:
        """Poll until the item page shows, or eBay blocks it.

        ``wait_out_challenge``: keep waiting on eBay's "Pardon Our Interruption"
        page, which lets a real browser through once its script has run.
        """
        from selenium.webdriver.common.by import By
        start = time.time()
        last_html = ""

        # Prefer the full item price module for the first few seconds — ``x-price-primary`` alone
        # often exists before discounted rows / BIN sale lines hydrate.
        preferred_locators = [
            (By.CSS_SELECTOR, "[data-testid='x-item-price']"),
            (By.CSS_SELECTOR, "section.x-item-price"),
            (By.CSS_SELECTOR, "[data-testid='x-price-view']"),
            (By.CSS_SELECTOR, "[data-testid='x-bin-price']"),
            (By.CSS_SELECTOR, ".x-bin-price"),
        ]
        late_locators = [
            (By.CSS_SELECTOR, "[data-testid='x-price-primary']"),
            (By.CSS_SELECTOR, ".x-price-primary"),
            (By.CSS_SELECTOR, "h1.x-item-title"),
            (By.CSS_SELECTOR, "[data-testid='x-item-title']"),
            (By.CSS_SELECTOR, "span[itemprop='price']"),
            (By.CSS_SELECTOR, "meta[itemprop='price'][content]"),
        ]

        while time.time() - start < timeout:
            try:
                html = driver.page_source
                last_html = html
                blocked, why = _is_challenge_or_blocked(html)
                if blocked and not (wait_out_challenge and why == "challenge"):
                    return html
                if blocked:
                    parsed = _parse_html_to_result(html, "")
                    if parsed is not None and parsed.get("price") is not None:
                        return html  # the item page itself, mentioning the check page
                    time.sleep(1.0)
                    continue
            except Exception:
                pass

            try:
                elapsed = time.time() - start
                locators = preferred_locators + (late_locators if elapsed >= 5.0 else [])
                for locator in locators:
                    elems = driver.find_elements(*locator)
                    if elems:
                        html = driver.page_source
                        if _looks_like_product_html(html):
                            # Fast exit: if the current DOM already yields a parseable price,
                            # skip the buy-box settle (saves 1.5–5s per URL on cold sessions).
                            if _parse_html_to_result(html, "") is not None:
                                return html
                            EbayBrowserSession._settle_buy_box(driver)
                            return driver.page_source
            except Exception:
                pass

            try:
                html = driver.page_source
                last_html = html
                lower = html.lower()
                blocked, _ = _is_challenge_or_blocked(html)
                if not blocked and _looks_like_product_html(html):
                    elapsed = time.time() - start
                    has_item_price = (
                        'data-testid="x-item-price"' in lower
                        or "data-testid='x-item-price'" in lower
                        or "x-item-price" in lower
                    )
                    if has_item_price or elapsed >= 5.0:
                        if _parse_html_to_result(html, "") is not None:
                            return html
                        EbayBrowserSession._settle_buy_box(driver)
                        return driver.page_source
            except Exception:
                pass

            time.sleep(0.4)

        return last_html

    @classmethod
    def warm_and_fetch(
        cls,
        url: str,
        region: str,
        session_dict: dict,
        market: str,
        *,
        force_proxy_rotate: bool = False,
    ) -> Tuple[Optional[str], str]:
        try:
            driver = EbayDriver.get_or_create(
                session_dict, market, force_proxy_rotate=force_proxy_rotate,
            )
        except Exception as exc:
            from .ebay_us_proxies import ProxyPoolBusy

            if isinstance(exc, ProxyPoolBusy):
                return None, "proxy_exhausted"
            return None, f"selenium_init: {exc}"

        try:
            cls._apply_region_browser_profile(driver, region, url)

            if market == EBAY_MARKET_AU:
                try:
                    from .ebay_au_fast import inject_au_cookies_into_driver

                    inject_au_cookies_into_driver(
                        driver,
                        session_dict,
                        loaded_key=_ebay_sk(EBAY_MARKET_AU, "market_cookies_loaded"),
                    )
                except Exception as exc:
                    logger.debug("eBay AU market cookie inject: %s", exc)

            us_on_proxy = bool(
                market == EBAY_MARKET_US
                and session_dict
                and session_dict.get(_ebay_sk(market, "selenium_proxy_url"))
            )
            warmed_key = _ebay_sk(market, "browser_warmed")
            home = _ebay_home_origin_for_item_url(url)
            home_host = urlparse(home).netloc
            warmed = session_dict.get(warmed_key) if session_dict else None
            # US remembers which site was warmed: moving from ebay.ca to ebay.com
            # opens the ebay.com home page (and loads its saved cookies) too.
            already_warmed = (warmed == home_host) if market == EBAY_MARKET_US else bool(warmed)
            us_chrome = market == EBAY_MARKET_US and ebay_us_browser_first()
            if not already_warmed:
                driver.get(home)
                if us_chrome:
                    # eBay's bot check runs its script on the first page; opening
                    # the item half a second later is what bots do.
                    _human_pause(driver)
                else:
                    time.sleep(0.3 + random.uniform(0.1, 0.3))
                if us_on_proxy and _browser_page_not_loaded(driver.page_source):
                    # The proxy refused Chrome; skip the item page's long wait.
                    EbayDriver.mark_us_browser_proxy_blocked(session_dict)
                    _note_us_browser_proxy_result(False)
                    EbayDriver.close(session_dict, market)
                    return None, "browser_proxy_error"
                if market == EBAY_MARKET_US:
                    _add_us_saved_cookies(driver)
                if session_dict is not None:
                    session_dict[warmed_key] = home_host if market == EBAY_MARKET_US else True

            driver.get(url)
            wait_timeout = _ebay_page_wait_timeout(region, url)
            if us_chrome:
                wait_timeout = max(wait_timeout, _US_CHALLENGE_WAIT_SEC)
            html = cls._wait_until_product_or_stable_challenge(
                driver, timeout=wait_timeout, wait_out_challenge=us_chrome,
            )
            if us_chrome and _is_challenge_or_blocked(html)[1] == "blocked":
                # A refusal often clears once the check script has run: one more
                # visit in the same Chrome, after a pause, before giving up.
                _human_pause(driver)
                driver.get(url)
                html = cls._wait_until_product_or_stable_challenge(
                    driver, timeout=wait_timeout, wait_out_challenge=True,
                )
                logger.info(
                    "eBay Chrome second visit: %s",
                    _is_challenge_or_blocked(html)[1] or "page loaded",
                )

            # Skip long discount polling when the first DOM already yields a BIN price.
            if _parse_html_to_result(html, url) is not None:
                html = driver.page_source or html
            else:
                html = cls._finalize_bin_price_hydration(driver, html, region, url)

            current_url = ""
            try:
                current_url = driver.current_url
            except Exception:
                pass

            if session_dict is not None:
                session_dict[_ebay_sk(market, "last_browser_url")] = current_url

            EbayHTTP.import_cookies_from_selenium(driver, region, session_dict, market)

            if _browser_page_not_loaded(html):
                if us_on_proxy:
                    EbayDriver.mark_us_browser_proxy_blocked(session_dict)
                    _note_us_browser_proxy_result(False)
                    EbayDriver.close(session_dict, market)
                    return None, "browser_proxy_error"
                return None, "empty_browser_html"
            if us_on_proxy:
                _note_us_browser_proxy_result(True)

            # A page that gives a price got through, even when its scripts still
            # mention eBay's check page; don't cool down the proxy that worked.
            if market == EBAY_MARKET_US and ebay_us_browser_first():
                parsed_ok = _parse_html_to_result(html, url)
                if parsed_ok is not None and parsed_ok.get("price") is not None:
                    return html, ""

            blocked, reason = _is_challenge_or_blocked(html)
            if blocked:
                if market == EBAY_MARKET_AU:
                    from .ebay_au_proxies import (
                        mark_proxy_blocked,
                        proxies_configured,
                        session_proxy_assignment,
                    )

                    if proxies_configured():
                        mark_proxy_blocked(session_proxy_assignment(session_dict))
                        EbayDriver.invalidate_au_proxy(session_dict)
                elif market == EBAY_MARKET_US and ebay_us_browser_first():
                    EbayDriver.mark_us_browser_proxy_blocked(session_dict)
                if "splashui/challenge" in current_url.lower():
                    return html, f"browser_{reason}"
                return html, f"browser_{reason}"

            return html, ""

        except Exception as exc:
            if (
                market == EBAY_MARKET_US
                and "net::err_" in str(exc).lower()
                and session_dict
                and session_dict.get(_ebay_sk(market, "selenium_proxy_url"))
            ):
                EbayDriver.mark_us_browser_proxy_blocked(session_dict)
                _note_us_browser_proxy_result(False)
                EbayDriver.close(session_dict, market)
                return None, "browser_proxy_error"
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
        return {"price": None, "stock": None, "title": None, "error_code": "not_found"}

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


# The catalog scrape adds the page's postage to the eBay AU price. The Auto Purchasing check turns
# this off and asks eBay for the postage to the order's postcode instead (scrapers.shipping_quote).
ADD_POSTAGE_TO_PRICE = contextvars.ContextVar("ebay_au_add_postage_to_price", default=True)


def _parse_html_to_result_au(html: str, url: str) -> Optional[dict]:
    """AU-only wrapper around :func:`_parse_html_to_result`.

    When the AU postage row is present with a paid amount, add it to the
    scraped item price (rounded to 2 decimals). Free postage rows are ignored.
    """
    if not html:
        return None

    parsed = _parse_html_to_result(html, url)

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    if parsed is None or parsed.get("price") is None:
        return parsed

    shipping = EbayParser.extract_au_shipping_amount(soup, html)
    if shipping is not None:
        parsed = dict(parsed)
        parsed["shipping"] = float(shipping)
        parsed["shipping_status"] = "page"
    if shipping is not None and shipping > 0 and ADD_POSTAGE_TO_PRICE.get():
        try:
            parsed["price"] = round(float(parsed["price"]) + float(shipping), 2)
        except (TypeError, ValueError):
            pass

    return parsed


def scrape_ebay_for_market(
    vendor_url: str,
    region: str,
    session: dict = None,
    *,
    market: str,
    max_attempts: int | None = None,
) -> dict:
    if session is None:
        session = {}

    eff_region = _ebay_market_region(market)
    url = _normalize_url(vendor_url, eff_region)
    # eBay US goes straight to Chrome: eBay's bot wall refuses plain HTTP from
    # every proxy, and each refused fetch only cost time and burned a proxy.
    browser_first = market == EBAY_MARKET_US and ebay_us_browser_first()

    candidate_urls = [url]
    if market == EBAY_MARKET_US and "ebay.com.au" not in url.lower():
        ca_url = _to_ebay_ca_url(url)
        if ca_url != url:
            if browser_first and ebay_us_ca_first():
                # eBay's wall refuses the item on ebay.com but lets it through on
                # ebay.ca (US sellers' prices show there in US $).
                candidate_urls.insert(0, ca_url)
            elif browser_first and _us_chrome_uses_proxies():
                # On the proxies ebay.ca was refused every time; a second site
                # only doubles the time a refused listing takes.
                pass
            else:
                candidate_urls.append(ca_url)

    if eff_region == "AU":
        random_delay(0.15, 0.4)
    else:
        random_delay(0.2, 0.6)
    last_error = None
    last_browser_html = None
    limit = ebay_us_chrome_attempts() if browser_first else RETRY_LIMIT
    attempts = max(1, min(max_attempts if max_attempts is not None else limit, limit))

    http_only = (not browser_first) and _ebay_http_only_with_proxies(market)
    give_up = False

    for attempt in range(attempts):
        if give_up:
            break
        raise_if_scrape_stopped()
        if attempt > 0:
            backoff_delay(attempt, base=2.0, jitter=1.5)
            if market == EBAY_MARKET_AU:
                from .ebay_au_proxies import proxies_configured

                if proxies_configured():
                    EbayDriver.invalidate_au_proxy(session)

        # Chrome-first keeps ebay.ca first on every try: each refused ebay.com
        # request puts proxies on a 5-minute cooldown.
        if browser_first or attempt % 2 == 0:
            try_urls = candidate_urls
        else:
            try_urls = list(reversed(candidate_urls))

        for candidate in try_urls:
            logger.info("eBay scrape attempt=%s url=%s", attempt + 1, candidate)

            if browser_first:
                html, status, err = None, None, ""
            elif _ebay_http_first_enabled(eff_region):
                html, status, err = EbayHTTP.fetch(candidate, eff_region, session, market)
            else:
                html, status, err = None, None, "http_skipped"

            if html and not err:
                if market == EBAY_MARKET_AU:
                    parsed = _parse_html_to_result_au(html, candidate)
                else:
                    parsed = _parse_html_to_result(html, candidate)
                if parsed is not None:
                    if parsed.get("price") is not None:
                        logger.info("eBay HTTP success for %s", candidate)
                    else:
                        logger.info(
                            "eBay HTTP fetched (no price / ended) for %s",
                            candidate,
                        )
                    _ebay_debug_write_html(session, html, "http_cold", candidate)
                    return parsed

            if http_only:
                # Chrome can't auth Webshare proxies via --proxy-server (no creds on
                # bare host:port), so Selenium warm-fetches return ~39-byte stubs and
                # only waste ~30s. Retry HTTP with the same warm curl_cffi session —
                # eBay BIN hydration often unlocks on the 2nd hit.
                browser_html, browser_err = None, ""
                if status in (404, 410):
                    # A missing listing is missing on every proxy; one fetch is enough.
                    html2, status2, err2 = None, None, ""
                else:
                    html2, status2, err2 = EbayHTTP.fetch(candidate, eff_region, session, market)
            else:
                browser_html, browser_err = EbayBrowserSession.warm_and_fetch(
                    candidate,
                    eff_region,
                    session,
                    market,
                    force_proxy_rotate=attempt > 0,
                )
                if browser_first:
                    logger.info(
                        "eBay Chrome attempt=%s via=%s result=%s",
                        attempt + 1,
                        "proxy" if session.get(_ebay_sk(market, "selenium_proxy_url")) else "server IP",
                        browser_err or "page loaded",
                    )
                if browser_html:
                    last_browser_html = browser_html
                    if market == EBAY_MARKET_AU:
                        parsed = _parse_html_to_result_au(browser_html, candidate)
                    else:
                        parsed = _parse_html_to_result(browser_html, candidate)
                    if parsed is not None:
                        logger.info("eBay Selenium HTML success for %s", candidate)
                        _ebay_debug_write_html(session, browser_html, "selenium", candidate)
                        return parsed

                chrome_on_proxy = bool(session.get(_ebay_sk(market, "selenium_proxy_url")))
                if browser_first and (
                    browser_err == "browser_proxy_error"
                    or chrome_on_proxy
                    or not ebay_us_cookie_handoff()
                ):
                    # No hand-off when Chrome never reached eBay, or when Chrome is
                    # on a proxy: eBay refuses the plain request on every other
                    # proxy, and each refusal cools down 3 of them for 5 minutes.
                    html2, status2, err2 = None, None, ""
                else:
                    # One plain request carrying the cookies Chrome just got from eBay.
                    html2, status2, err2 = EbayHTTP.fetch(candidate, eff_region, session, market)

            if html2 and not err2:
                if market == EBAY_MARKET_AU:
                    parsed = _parse_html_to_result_au(html2, candidate)
                else:
                    parsed = _parse_html_to_result(html2, candidate)
                if parsed is not None:
                    logger.info("eBay cookie-handoff HTTP success for %s", candidate)
                    _ebay_debug_write_html(session, html2, "http_cookie", candidate)
                    return parsed

            # Chrome's error says what eBay did; the hand-off's is secondary.
            parts = [browser_err, err2, err] if browser_first else [err2, browser_err, err]
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

            code = ebay_result_error_code(last_error)
            if browser_err == "proxy_exhausted":
                # No free proxy even after waiting: leave the listing Pending
                # rather than wait again on every remaining try.
                give_up = True
                break
            if code == "not_found" or (
                not should_retry_failure(last_error) and code not in ("blocked", "challenge")
            ):
                # Nothing a retry can change (missing listing, page with no price).
                # This used to leave only the inner loop, so every round ran again.
                give_up = True
                break

            if len(candidate_urls) > 1:
                try:
                    current_browser_url = session.get(_ebay_sk(market, "last_browser_url"), "") if session else ""
                    if current_browser_url and urlparse(current_browser_url).netloc != urlparse(candidate).netloc:
                        EbayDriver.close(session, market)
                except Exception:
                    pass

    logger.warning("eBay scrape failed for %s, last_error=%s", url, last_error)

    if last_browser_html:
        save_debug_html(last_browser_html, f"ebay_{market}", url, last_error or "unknown")

    return {
        "price": None,
        "stock": None,
        "title": None,
        "error_code": ebay_result_error_code(last_error),
        "error_message": (last_error or "")[:200],
    }


def close_ebay_market_session(session: dict, market: str):
    if session is None:
        return

    _migrate_legacy_ebay_session(session, market)
    EbayDriver.close(session, market)

    client = session.pop(_ebay_sk(market, "http_client"), None)
    if client:
        try:
            client.close()
        except Exception:
            pass

    session.pop(_ebay_sk(market, "last_user_agent"), None)
    session.pop(_ebay_sk(market, "last_http_url"), None)
    session.pop(_ebay_sk(market, "last_browser_url"), None)
    session.pop(_ebay_sk(market, "last_failed_html"), None)


def close_ebay_session(session: dict):
    """Close US and AU eBay browser/HTTP state held in `session`."""
    close_ebay_market_session(session, EBAY_MARKET_US)
    close_ebay_market_session(session, EBAY_MARKET_AU)
