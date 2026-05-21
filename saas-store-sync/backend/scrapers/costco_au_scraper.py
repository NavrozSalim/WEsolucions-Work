"""
Costco AU server scraper (``costco.com.au``).

Replaces the Windows desktop runner with a Celery-driven scraper that runs on
the AU VPS using **static residential proxies**. Public API mirrors the other
server scrapers in this package:

    scrape_costco_au(vendor_url, region, session) ->
        {"price": float|None, "stock": int|None, "title": str|None, ...}
    close_costco_au_session(session)

Strategy (in order):

1. **HTTP-first via curl_cffi** (Chrome TLS impersonation) through the assigned
   residential proxy — handles the vast majority of Costco AU PDPs.
2. **Selenium fallback with proxy-server flag** — opt-in via
   ``COSTCO_AU_SELENIUM_FALLBACK=1``; off by default since residential
   proxies + curl_cffi normally bypass Cloudflare without a browser.

The HTML parsing logic (price, inventory, homepage-redirect detection,
challenge detection) is ported verbatim from the proven desktop worker at
``Costco Scraper\\_main_template.py`` so server results match desktop output
1:1.

Configuration:

    COSTCO_AU_PROXY_URLS                 list of proxy URLs (required)
    COSTCO_AU_MIN_REQUEST_GAP_SEC        per-proxy throttle (default 20)
    COSTCO_AU_HTTP_TIMEOUT_SEC           per-request timeout (default 25)
    COSTCO_AU_HTTP_RETRIES               retries before rotating proxy (default 2)
    COSTCO_AU_SELENIUM_FALLBACK          1 to enable browser fallback (default 0)
    COSTCO_AU_BLOCK_COOLDOWN_SEC         seconds to skip a banned proxy (default 600)
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Optional

from bs4 import BeautifulSoup

from .core import ScrapeResult
from .costco_au_proxies import (
    CostcoAuProxyPool,
    ProxyAssignment,
    get_pool,
)

logger = logging.getLogger("scrapers.costco_au")

VENDOR_TAG = "costco_au"
_HTTP_SESSION_KEY = "costco_au_http_client"
_HTTP_PROXY_KEY = "costco_au_http_proxy"
_DRIVER_KEY = "costco_au_driver"
_DRIVER_PROXY_KEY = "costco_au_driver_proxy"

_BS4_PARSER = "lxml"
try:
    import lxml  # noqa: F401
except ImportError:  # pragma: no cover - exercised via warning only
    _BS4_PARSER = "html.parser"
    logger.warning("lxml not installed for Costco AU; using slower html.parser")


# ---------------------------------------------------------------------------
# Env helpers
# ---------------------------------------------------------------------------

def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, "") or default))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.environ.get(name, "") or "").strip().lower()
    if raw in ("1", "true", "yes", "y", "on"):
        return True
    if raw in ("0", "false", "no", "n", "off"):
        return False
    return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


HTTP_TIMEOUT_SEC = _env_int("COSTCO_AU_HTTP_TIMEOUT_SEC", 25)
HTTP_RETRIES = _env_int("COSTCO_AU_HTTP_RETRIES", 2)
SELENIUM_FALLBACK = _env_bool("COSTCO_AU_SELENIUM_FALLBACK", False)
BLOCK_COOLDOWN_SEC = _env_float("COSTCO_AU_BLOCK_COOLDOWN_SEC", 600.0)


# ---------------------------------------------------------------------------
# Parsing helpers (ported verbatim from desktop _main_template.py)
# ---------------------------------------------------------------------------

def _parse_price_text(text: str) -> Optional[float]:
    if not text:
        return None
    cleaned = (
        str(text).replace(",", "").replace("\xa0", " ").replace("$", "").strip()
    )
    m = re.search(r"(\d+(?:\.\d+)?)", cleaned)
    if not m:
        return None
    try:
        val = float(m.group(1))
        if 0.01 <= val < 999_999:
            return val
    except ValueError:
        pass
    return None


def product_id_from_url(url: str) -> Optional[str]:
    m = re.search(r"/p/(\d+)(?:[/?#]|$)", url or "")
    return m.group(1) if m else None


def _text_first(soup: BeautifulSoup, selector: str) -> Optional[str]:
    el = soup.select_one(selector)
    if not el:
        return None
    if el.name == "meta":
        content = (el.get("content") or "").strip()
        return content or None
    text = el.get_text(" ", strip=True)
    return text or None


def _extract_title(soup: BeautifulSoup) -> Optional[str]:
    text = _text_first(soup, "h1")
    if text:
        return text[:500]
    for sel in ("meta[property='og:title']", "meta[name='twitter:title']", "title"):
        val = _text_first(soup, sel)
        if val:
            return val[:500]
    return None


def _extract_prices(soup: BeautifulSoup) -> tuple[Optional[float], Optional[float]]:
    """Return ``(normal_price, sale_price)`` — either may be None."""
    normal = None
    normal_raw = _text_first(soup, ".price-original span.notranslate")
    if normal_raw:
        normal = _parse_price_text(normal_raw)

    if normal is None:
        for sel in (".original-price", "span.product-price-amount"):
            raw = _text_first(soup, sel)
            if raw:
                p = _parse_price_text(raw)
                if p is not None:
                    normal = p
                    break

    sale = None
    for sel in (
        ".you-pay-value",
        ".you-pay-value span.notranslate",
        "span.you-pay-value",
        "[class*='you-pay-value']",
    ):
        sale_raw = _text_first(soup, sel)
        if sale_raw:
            sale = _parse_price_text(sale_raw)
            if sale is not None:
                break

    if normal is None and sale is None:
        for sel in (
            "meta[property='product:price:amount']",
            "meta[itemprop='price']",
            "[itemprop='price']",
        ):
            raw = _text_first(soup, sel)
            if raw:
                p = _parse_price_text(raw)
                if p is not None:
                    normal = p
                    break
        if normal is None:
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
                        if "price" in node:
                            p = _parse_price_text(str(node["price"]))
                            if p is not None:
                                return p
                        for v in node.values():
                            got = walk(v)
                            if got is not None:
                                return got
                    elif isinstance(node, list):
                        for v in node:
                            got = walk(v)
                            if got is not None:
                                return got
                    return None

                parsed = walk(data)
                if parsed is not None:
                    normal = parsed
                    break

    return normal, sale


_LESS_PRICE_CLASS_SELECTORS = (
    ".less-value",
    ".price-less",
    ".price-less span.notranslate",
    "[class*='price-less']",
    "[class*='less-value']",
    ".member-savings-value",
    ".instant-savings-value",
    ".you-save-value",
    ".discount-amount",
    "[class*='savings-value']",
    "[class*='member-savings']",
    "[class*='discount-value']",
)

_LESS_TEXT_RE = re.compile(
    r"less[^A-Za-z0-9]{0,80}[-\u2212]?\s*\$?\s*(\d+(?:,\d{3})*(?:\.\d+)?)",
    re.IGNORECASE,
)

_DOLLAR_AMOUNT_RE = re.compile(
    r"\$\s*(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)",
)

# Embedded JSON / Angular state keys seen on costco.com.au PDPs.
_SALE_JSON_KEY_RE = re.compile(
    r'"(?:youPayPrice|yourPrice|memberPrice|salePrice|discountedPrice|promoPrice|'
    r'currentPrice|displayPrice|finalPrice|offerPrice|netPrice)"\s*:\s*'
    r'(?:"(\d+(?:\.\d+)?)"|(\d+(?:\.\d+)?))',
    re.IGNORECASE,
)

_DISCOUNT_JSON_KEY_RE = re.compile(
    r'"(?:discountAmount|instantSavings|savingsAmount|amountOff|lessAmount|'
    r'discountValue|savingsValue|discount|savings)"\s*:\s*'
    r'(?:"(\d+(?:\.\d+)?)"|(\d+(?:\.\d+)?))',
    re.IGNORECASE,
)

_WAS_PRICE_JSON_RE = re.compile(
    r'"wasPrice"\s*:\s*\{[^}]{0,120}?"value"\s*:\s*(\d+(?:\.\d+)?)',
    re.IGNORECASE,
)

_PRICE_VALUE_JSON_RE = re.compile(
    r'"price"\s*:\s*\{[^}]{0,120}?"value"\s*:\s*(\d+(?:\.\d+)?)',
    re.IGNORECASE,
)


def _float_from_re_groups(groups: tuple) -> Optional[float]:
    for g in groups:
        if g is None:
            continue
        val = _parse_price_text(str(g))
        if val is not None:
            return val
    return None


def _product_html_window(html: str, url: str, *, radius: int = 20_000) -> str:
    """Return a slice of HTML centred on the product id when possible."""
    if not html:
        return ""
    pid = product_id_from_url(url)
    if pid:
        idx = html.find(pid)
        if idx >= 0:
            start = max(0, idx - radius)
            end = min(len(html), idx + radius)
            return html[start:end]
    return html


def _amounts_near_markers(html: str, markers: tuple[str, ...], *, window: int = 5000) -> list[float]:
    amounts: list[float] = []
    if not html:
        return amounts
    low = html.lower()
    seen: set[float] = set()
    for marker in markers:
        start = 0
        while True:
            idx = low.find(marker.lower(), start)
            if idx < 0:
                break
            chunk = html[idx : idx + window]
            for m in _DOLLAR_AMOUNT_RE.finditer(chunk):
                val = _parse_price_text(m.group(1))
                if val is not None and val not in seen:
                    seen.add(val)
                    amounts.append(val)
            start = idx + len(marker)
    return amounts


def _pick_sale_from_amounts(normal: float, amounts: list[float]) -> Optional[float]:
    """Choose the lowest plausible sale price below ``normal``."""
    if normal is None or not amounts:
        return None
    below = [a for a in amounts if 0 < a < normal]
    if not below:
        return None
    sale = min(below)
    # Ignore tiny deltas that are probably shipping/fees, not member price.
    if sale >= normal * 0.995:
        return None
    return round(sale, 2)


def _sale_from_price_was_pair(obj: dict) -> Optional[float]:
    """Return sale ``price.value`` when ``wasPrice.value`` is higher (Hybris/OCC shape)."""
    price_node = obj.get("price")
    was_node = obj.get("wasPrice")
    if not isinstance(price_node, dict) or not isinstance(was_node, dict):
        return None
    sale = _parse_price_text(str(price_node.get("value", "")))
    was = _parse_price_text(str(was_node.get("value", "")))
    if sale is not None and was is not None and 0 < sale < was:
        return round(sale, 2)
    return None


def _find_promo_price_in_json(node) -> Optional[float]:
    if isinstance(node, dict):
        got = _sale_from_price_was_pair(node)
        if got is not None:
            return got
        for v in node.values():
            got = _find_promo_price_in_json(v)
            if got is not None:
                return got
    elif isinstance(node, list):
        for item in node:
            got = _find_promo_price_in_json(item)
            if got is not None:
                return got
    return None


def _extract_sale_from_json_scripts(soup: BeautifulSoup) -> Optional[float]:
    """Parse ``<script>`` JSON blobs for ``price`` + ``wasPrice`` pairs."""
    for script in soup.select("script"):
        raw = (script.string or script.get_text() or "").strip()
        if not raw or len(raw) < 12:
            continue
        if "price" not in raw.lower():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        found = _find_promo_price_in_json(data)
        if found is not None:
            return found
    return None


def _extract_sale_from_embedded_json(
    html: str, url: str, normal: Optional[float], soup: Optional[BeautifulSoup] = None,
) -> Optional[float]:
    """Scan Angular/JSON blobs for Your Price or discount fields."""
    if not html:
        return None

    if soup is not None:
        from_scripts = _extract_sale_from_json_scripts(soup)
        if from_scripts is not None:
            return from_scripts

    chunks = [_product_html_window(html, url), html]
    sale_candidates: list[float] = []
    discount_candidates: list[float] = []
    was_prices: list[float] = []
    price_values: list[float] = []

    for chunk in chunks:
        if not chunk:
            continue
        for m in _SALE_JSON_KEY_RE.finditer(chunk):
            val = _float_from_re_groups(m.groups())
            if val is not None:
                sale_candidates.append(val)
        for m in _DISCOUNT_JSON_KEY_RE.finditer(chunk):
            val = _float_from_re_groups(m.groups())
            if val is not None:
                discount_candidates.append(val)
        for m in _WAS_PRICE_JSON_RE.finditer(chunk):
            val = _parse_price_text(m.group(1))
            if val is not None:
                was_prices.append(val)
        for m in _PRICE_VALUE_JSON_RE.finditer(chunk):
            val = _parse_price_text(m.group(1))
            if val is not None:
                price_values.append(val)

        # Generic nested JSON price objects: "value": 399.99 near "currencyIso":"AUD"
        for m in re.finditer(
            r'"value"\s*:\s*(\d+(?:\.\d+)?)[^}]{0,80}?"currencyIso"\s*:\s*"AUD"',
            chunk,
            re.IGNORECASE,
        ):
            val = _parse_price_text(m.group(1))
            if val is not None:
                price_values.append(val)
        for m in re.finditer(
            r'"currencyIso"\s*:\s*"AUD"[^}]{0,80}?"value"\s*:\s*(\d+(?:\.\d+)?)',
            chunk,
            re.IGNORECASE,
        ):
            val = _parse_price_text(m.group(1))
            if val is not None:
                price_values.append(val)

    ref_normal = normal
    if ref_normal is None and was_prices:
        ref_normal = max(was_prices)

    for sale in sale_candidates:
        if ref_normal is None or (0 < sale < ref_normal):
            return round(sale, 2)

    if ref_normal is not None:
        for discount in discount_candidates:
            if 0 < discount < ref_normal:
                computed = round(ref_normal - discount, 2)
                if computed > 0:
                    return computed

    if ref_normal is not None and price_values:
        picked = _pick_sale_from_amounts(ref_normal, price_values)
        if picked is not None:
            return picked

    if was_prices and price_values:
        was = max(was_prices)
        picked = _pick_sale_from_amounts(was, price_values)
        if picked is not None:
            return picked

    return None


def _extract_less_amount(soup: BeautifulSoup, html: str) -> Optional[float]:
    """Return the ``Less ... -$X.XX`` discount amount when present.

    Costco AU HOT BUY pages render the discount value directly in the initial HTML,
    even though ``span.you-pay-value`` hydrates later. We can compute Your Price
    locally as ``Online Price - Less`` without a Selenium round trip.
    """
    for sel in _LESS_PRICE_CLASS_SELECTORS:
        raw = _text_first(soup, sel)
        if raw:
            amount = _parse_price_text(raw)
            if amount is not None and 0 < amount < 1_000_000:
                return amount

    try:
        text = soup.get_text(" ", strip=True) if soup else ""
    except Exception:
        text = ""

    for haystack in (text, html or ""):
        if not haystack:
            continue
        m = _LESS_TEXT_RE.search(haystack)
        if m:
            amount = _parse_price_text(m.group(1))
            if amount is not None and 0 < amount < 1_000_000:
                return amount

    # Negative dollar amount in markup, e.g. ``>$190.00<`` in a Less row.
    for m in re.finditer(r"[-\u2212]\s*\$\s*(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)", html or ""):
        amount = _parse_price_text(m.group(1))
        if amount is not None and 0 < amount < 1_000_000:
            return amount

    return None


def _resolve_sale_price(
    soup: BeautifulSoup,
    html: str,
    url: str,
    normal: Optional[float],
    sale: Optional[float],
) -> Optional[float]:
    """Return Your Price using DOM, Less math, embedded JSON, or nearby dollar amounts."""
    if sale is not None:
        return sale

    if normal is not None:
        less = _extract_less_amount(soup, html)
        if less is not None and 0 < less < normal:
            computed = round(normal - less, 2)
            logger.info(
                "Costco AU computed Your Price from Less math: %.2f - %.2f = %.2f",
                normal, less, computed,
            )
            return computed

    embedded = _extract_sale_from_embedded_json(html, url, normal, soup)
    if embedded is not None:
        logger.info("Costco AU Your Price from embedded JSON: %.2f", embedded)
        return embedded

    if normal is not None:
        amounts = _amounts_near_markers(
            html,
            (
                "price-original",
                "you-pay-value",
                "you-pay",
                "online price",
                "your price",
                "hot buy",
            ),
        )
        picked = _pick_sale_from_amounts(normal, amounts)
        if picked is not None:
            logger.info(
                "Costco AU Your Price from nearby amounts (normal=%.2f sale=%.2f)",
                normal, picked,
            )
            return picked

    # Empty ``you-pay-value`` placeholder — check data-* attrs Angular may pre-seed.
    for el in soup.select("[class*='you-pay'], [class*='you-pay-value']"):
        for attr in ("data-price", "data-value", "ng-reflect-price", "content"):
            raw = (el.get(attr) or "").strip()
            if not raw:
                continue
            val = _parse_price_text(raw)
            if val is not None and (normal is None or (0 < val < normal)):
                logger.info("Costco AU Your Price from %s attr: %.2f", attr, val)
                return val

    return None


def _html_expects_you_pay_price(
    html: str,
    soup: BeautifulSoup,
    *,
    normal: Optional[float],
    sale: Optional[float],
    url: str = "",
) -> bool:
    """Return True when the PDP shows promo pricing but ``you-pay-value`` is not parsed yet.

    Costco AU HOT BUY pages render ``price-original`` (Online Price) in the initial HTML
    but hydrate ``span.you-pay-value`` (Your Price) via Angular. HTTP-first would otherwise
    return the higher online price and treat the scrape as success.
    """
    if sale is not None:
        return False
    if normal is None:
        return False

    low = (html or "").lower()
    has_online = (
        soup.select_one(".price-original span.notranslate") is not None
        or "online price" in low
    )
    if not has_online:
        return False

    promo_hints = (
        "your price",
        "hot buy",
        "price valid from",
        "while stock lasts",
        "online price",
    )
    if any(h in low for h in promo_hints):
        return True

    if "less" in low and (
        "-$" in html
        or "−$" in html
        or re.search(r"less[^<]{0,80}[-\u2212]?\s*\$", low)
    ):
        return True

    # Empty ``you-pay-value`` alongside ``price-original`` → promo page (Angular hydrates later).
    if soup.select_one(".price-original"):
        for el in soup.select("[class*='you-pay-value'], [class*='you-pay']"):
            txt = (el.get_text(" ", strip=True) or "").strip()
            if not txt:
                return True

    # Embedded wasPrice + price.value pair in product JSON/state blobs.
    chunk = _product_html_window(html, url)
    if chunk and _WAS_PRICE_JSON_RE.search(chunk) and _PRICE_VALUE_JSON_RE.search(chunk):
        return True

    return False


def _button_disabled(el) -> bool:
    if el is None:
        return True
    if el.has_attr("disabled"):
        return True
    if (el.get("aria-disabled") or "").strip().lower() == "true":
        return True
    classes = " ".join(el.get("class") or []).lower()
    return "disabled" in classes or "outofstock" in classes


def _extract_inventory(soup: BeautifulSoup, url: str) -> int:
    """3 = in stock, 0 = out of stock / unknown (matches desktop runner)."""
    pid = product_id_from_url(url)
    if pid:
        specific = soup.select_one(f"button[data-cy='addtocart-button-{pid}']")
        if specific:
            return 0 if _button_disabled(specific) else 3

    oos = soup.select_one(
        "sip-add-to-cart-form button.btn.btn-block.btn-primary.disabled.outOfStock"
    )
    if oos is not None:
        return 0

    for el in soup.select(
        "button[data-cy^='addtocart-button-'], sip-add-to-cart-form button"
    ):
        label = (el.get_text(" ", strip=True) or "").lower()
        if "add to cart" not in label:
            continue
        return 0 if _button_disabled(el) else 3

    return 0


# ---------------------------------------------------------------------------
# Challenge / block detection (ported)
# ---------------------------------------------------------------------------

_CF_STRICT_MARKERS = (
    "challenges.cloudflare.com",
    "cf-challenge-running",
    "cdn-cgi/challenge-platform",
    "cf-chl-bypass",
    "cf-chl-widget",
    "__cf_chl_tk",
    "window._cf_chl_opt",
    'id="challenge-form"',
    "id='challenge-form'",
)

_CF_TITLE_PHRASES = (
    "just a moment",
    "attention required",
    "access denied",
    "pardon our interruption",
    "you have been blocked",
)

_POSITIVE_SIGNALS = (
    "sip-add-to-cart-form",
    'data-cy="addtocart-button-',
    "data-cy='addtocart-button-",
    'class="price-original"',
    "class='price-original'",
    "you-pay-value",
)

_TITLE_RE = re.compile(r"<title[^>]*>([^<]{0,300})</title>", re.IGNORECASE)


def html_is_challenge(html: str) -> tuple[bool, str]:
    """Return ``(is_challenge, reason)``. Public for unit testing."""
    if not html:
        return True, "empty_response"
    if len(html) < 500 and "<html" not in html.lower():
        return True, "truncated"

    low = html.lower()

    # A real PDP shouldn't have CF markers; if we see product-specific elements,
    # treat it as not challenged even if the page also has CF analytics.
    for signal in _POSITIVE_SIGNALS:
        if signal in low:
            return False, ""

    for marker in _CF_STRICT_MARKERS:
        if marker.lower() in low:
            return True, "cloudflare"

    m = _TITLE_RE.search(html)
    if m:
        title = m.group(1).strip().lower()
        for phrase in _CF_TITLE_PHRASES:
            if phrase in title:
                return True, f"title_{phrase.split()[0]}"

    return False, ""


_HOMEPAGE_TITLE_PATTERNS = (
    "member warehouse for bulk buys",
    "costco australia",
    "| costco aus",
)


def is_homepage_redirect(url: str, final_url: str, soup: BeautifulSoup) -> bool:
    """Return True when a ``/p/<id>`` request silently resolved to the home page.

    Mirrors the desktop ``_is_homepage_redirect`` logic — needs two positive
    signals to avoid false positives on slow Angular hydration.
    """
    requested_pid = product_id_from_url(url)
    if not requested_pid:
        return False

    canonical_is_homepage = False
    for sel, attr in (
        ("link[rel='canonical']", "href"),
        ("meta[property='og:url']", "content"),
    ):
        el = soup.select_one(sel)
        val = ((el.get(attr) or "") if el else "").strip()
        if not val:
            continue
        base = val.split("?", 1)[0].split("#", 1)[0].rstrip("/").lower()
        if base in ("https://www.costco.com.au", "http://www.costco.com.au"):
            canonical_is_homepage = True
            break

    title_is_homepage = False
    title_el = soup.select_one("title")
    title = (title_el.get_text(strip=True).lower() if title_el else "")
    for pat in _HOMEPAGE_TITLE_PATTERNS:
        if pat in title:
            title_is_homepage = True
            break

    if canonical_is_homepage and title_is_homepage:
        return True

    dom_is_barren = (
        soup.select_one("h1") is None
        and soup.select_one("sip-add-to-cart-form") is None
        and soup.select_one(f"button[data-cy='addtocart-button-{requested_pid}']") is None
    )
    if (canonical_is_homepage or title_is_homepage) and dom_is_barren:
        return True

    final = (final_url or "").strip().lower()
    if final:
        final_pid = product_id_from_url(final)
        if final_pid and final_pid == requested_pid:
            return False
        if not final_pid and final.rstrip("/").endswith("costco.com.au") and dom_is_barren:
            return True

    return False


def parse_costco_pdp(url: str, html: str, final_url: str = "") -> ScrapeResult:
    """Parse a Costco AU product page HTML payload into a :class:`ScrapeResult`.

    Returns:
        ``ScrapeResult.ok(price, stock, title)`` on success.
        ``ScrapeResult.fail(...)`` with one of:
            * ``blocked_<reason>`` — challenge / WAF page
            * ``product_not_found`` — homepage redirect for /p/<id>
            * ``no_price``         — page loaded but no price selector matched
    """
    if not html:
        return ScrapeResult.fail("empty_response", "Empty HTML", "", VENDOR_TAG, url)

    challenged, reason = html_is_challenge(html)
    if challenged:
        return ScrapeResult.fail(
            f"blocked_{reason}", f"Blocked: {reason}", html, VENDOR_TAG, url,
        )

    soup = BeautifulSoup(html, _BS4_PARSER)

    if is_homepage_redirect(url, final_url or "", soup):
        return ScrapeResult.fail(
            "product_not_found", "Request redirected to Costco homepage", "",
            VENDOR_TAG, url,
        )

    title = _extract_title(soup)
    normal, sale = _extract_prices(soup)
    stock = _extract_inventory(soup, url)

    expects_sale = _html_expects_you_pay_price(
        html, soup, normal=normal, sale=sale, url=url,
    )
    if expects_sale:
        sale = _resolve_sale_price(soup, html, url, normal, sale)
        if sale is None:
            return ScrapeResult.fail(
                "incomplete_sale_price",
                "Your Price not found in DOM, Less math, or embedded JSON",
                html,
                VENDOR_TAG,
                url,
            )

    price = sale if sale is not None else normal

    if price is None:
        return ScrapeResult.fail("no_price", "Price not found on PDP", html, VENDOR_TAG, url)

    return ScrapeResult.ok(price=price, stock=stock, title=title)


# ---------------------------------------------------------------------------
# HTTP client (curl_cffi for Cloudflare TLS bypass, requests as fallback)
# ---------------------------------------------------------------------------

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-AU,en;q=0.9",
    "Cache-Control": "no-cache",
    "Upgrade-Insecure-Requests": "1",
}


def _get_http_client(session: dict, assignment: ProxyAssignment):
    """Return a ``curl_cffi`` (preferred) or ``requests`` Session, bound to ``assignment``.

    Recreated whenever the assigned proxy changes so cookies + connection pool
    stay attached to one IP.
    """
    existing = session.get(_HTTP_SESSION_KEY) if session is not None else None
    existing_proxy = session.get(_HTTP_PROXY_KEY) if session is not None else None
    if existing is not None and existing_proxy == assignment.url:
        return existing
    if existing is not None:
        try:
            existing.close()
        except Exception:
            pass

    client = None
    try:
        from curl_cffi import requests as curl_requests
        client = curl_requests.Session()
        # curl_cffi proxies kwarg works the same as requests.
        client.proxies = assignment.as_requests_proxy()
        client.headers.update(_HEADERS)
        client._costco_au_use_impersonate = True  # type: ignore[attr-defined]
    except Exception:
        import requests as _requests
        client = _requests.Session()
        client.proxies = assignment.as_requests_proxy()
        client.headers.update(_HEADERS)
        client._costco_au_use_impersonate = False  # type: ignore[attr-defined]

    if session is not None:
        session[_HTTP_SESSION_KEY] = client
        session[_HTTP_PROXY_KEY] = assignment.url

    return client


def _http_fetch(url: str, session: dict, assignment: ProxyAssignment) -> tuple[str, str, str]:
    """Fetch one URL through ``assignment``. Returns ``(html, final_url, error_tag)``.

    ``error_tag`` is ``""`` on success, otherwise one of ``http_<status>``,
    ``request_error: ...``, ``timeout``.
    """
    client = _get_http_client(session, assignment)
    kwargs = {
        "timeout": HTTP_TIMEOUT_SEC,
        "allow_redirects": True,
    }
    if getattr(client, "_costco_au_use_impersonate", False):
        kwargs["impersonate"] = "chrome131"

    try:
        resp = client.get(url, **kwargs)
    except Exception as exc:
        cls_name = type(exc).__name__
        return "", "", f"request_error: {cls_name}: {str(exc)[:200]}"

    final_url = getattr(resp, "url", url) or url
    status = getattr(resp, "status_code", None)
    html = getattr(resp, "text", "") or ""

    if status != 200:
        return html, str(final_url), f"http_{status}"

    return html, str(final_url), ""


# ---------------------------------------------------------------------------
# Selenium fallback (opt-in)
# ---------------------------------------------------------------------------

def _get_selenium_driver(session: dict, assignment: ProxyAssignment):
    """Create or reuse a headless Chrome driver bound to ``assignment``.

    The proxy URL is passed via ``--proxy-server`` and credentials are stripped
    (only ``scheme://host:port`` is supported by Chrome flags). For
    user:pass proxies set ``COSTCO_AU_PROXY_REQUIRES_BASIC_AUTH=0`` and use IP
    allowlisting on the residential provider — the simplest path that works.
    """
    existing = session.get(_DRIVER_KEY)
    existing_proxy = session.get(_DRIVER_PROXY_KEY)
    if existing is not None and existing_proxy == assignment.url:
        return existing
    if existing is not None:
        _quit_driver(existing)

    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--lang=en-AU")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument(f"--user-agent={_HEADERS['User-Agent']}")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    # Chrome only accepts scheme://host:port — strip credentials, rely on IP allowlist.
    from urllib.parse import urlparse
    p = urlparse(assignment.url)
    proxy_arg = f"{p.scheme}://{p.hostname}:{p.port}"
    opts.add_argument(f"--proxy-server={proxy_arg}")

    chromedriver_path = os.environ.get("CHROMEDRIVER_PATH")
    if chromedriver_path and os.path.isfile(chromedriver_path):
        service = Service(executable_path=chromedriver_path)
    else:
        try:
            from webdriver_manager.chrome import ChromeDriverManager
            service = Service(ChromeDriverManager().install())
        except Exception:
            service = Service()

    driver = webdriver.Chrome(service=service, options=opts)
    try:
        driver.set_page_load_timeout(HTTP_TIMEOUT_SEC + 15)
    except Exception:
        pass

    if session is not None:
        session[_DRIVER_KEY] = driver
        session[_DRIVER_PROXY_KEY] = assignment.url
    return driver


def _quit_driver(driver) -> None:
    if driver is None:
        return
    try:
        driver.quit()
    except Exception:
        pass


def _selenium_fetch(url: str, session: dict, assignment: ProxyAssignment) -> tuple[str, str, str]:
    """Render ``url`` with Selenium through ``assignment``. Heavy fallback."""
    try:
        driver = _get_selenium_driver(session, assignment)
    except Exception as exc:
        return "", "", f"selenium_init_error: {exc}"

    try:
        driver.get(url)
    except Exception as exc:
        return "", "", f"selenium_navigate_error: {exc}"

    # Give Angular time to hydrate ``you-pay-value`` (Your Price on HOT BUY promos).
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            html = driver.page_source or ""
        except Exception:
            html = ""
        challenged, _ = html_is_challenge(html)
        if challenged:
            time.sleep(0.5)
            continue

        soup = BeautifulSoup(html, _BS4_PARSER)
        normal, sale = _extract_prices(soup)
        if sale is not None:
            break
        if not _html_expects_you_pay_price(html, soup, normal=normal, sale=sale, url=url) and (
            "sip-add-to-cart-form" in html
            or "data-cy=\"addtocart-button-" in html
            or soup.select_one(".price-original span.notranslate") is not None
        ):
            break
        time.sleep(0.5)

    try:
        html = driver.page_source or ""
    except Exception:
        html = ""
    final_url = ""
    try:
        final_url = driver.current_url or ""
    except Exception:
        pass

    return html, final_url, ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scrape_costco_au(
    vendor_url: str,
    region: str,
    session: Optional[dict] = None,
    *,
    pool: Optional[CostcoAuProxyPool] = None,
) -> dict:
    """Scrape one Costco AU product page through the residential proxy pool.

    ``pool`` is for tests — production calls let the module pick the env pool.
    """
    if session is None:
        session = {}

    active_pool = pool if pool is not None else get_pool()
    if active_pool is None or active_pool.size == 0:
        logger.warning(
            "Costco AU scrape requested but no residential proxies configured "
            "(set COSTCO_AU_PROXY_URLS). Falling back to ingest-only behavior "
            "for url=%s", vendor_url,
        )
        return {
            "price": None,
            "stock": None,
            "title": None,
            "error_code": "costco_no_proxy",
            "error_message": (
                "Costco AU server scrape requires COSTCO_AU_PROXY_URLS to be set "
                "on the AU worker. Configure your residential proxies and restart "
                "the worker, or switch this vendor back to ingest-only."
            ),
        }

    attempts = max(1, HTTP_RETRIES + 1)
    last_error: Optional[str] = None
    last_html_saved = False

    for attempt in range(attempts):
        assignment = active_pool.acquire(force_rotate=attempt > 0)
        if assignment is None:
            last_error = "no_proxy_available"
            break

        active_pool.wait_for_gap(assignment)
        logger.info(
            "Costco AU GET %s (proxy=%s attempt=%d/%d)",
            vendor_url[:120], assignment.label, attempt + 1, attempts,
        )
        html, final_url, http_err = _http_fetch(vendor_url, session, assignment)

        if http_err:
            last_error = http_err
            # 4xx/5xx — try next proxy; transient request error — try next proxy too.
            active_pool.mark_blocked(assignment, cooldown_sec=BLOCK_COOLDOWN_SEC / 6)
            continue

        result = parse_costco_pdp(vendor_url, html, final_url)
        if result.success:
            active_pool.mark_success(assignment)
            logger.info(
                "Costco AU OK url=%s price=%s stock=%s proxy=%s",
                vendor_url[:80], result.price, result.stock, assignment.label,
            )
            return result.to_legacy()

        last_error = result.error_code
        last_html_saved = result.raw_html_saved

        if result.error_code == "product_not_found":
            return result.to_legacy()

        # ``incomplete_sale_price`` — HTML has Online Price but no Your Price + no Less
        # math available. Almost always a transient hydration miss, not a proxy ban —
        # don't burn the proxy, just rotate to a fresh one for the next attempt.
        if result.error_code == "incomplete_sale_price":
            continue

        if result.error_code.startswith("blocked_"):
            active_pool.mark_blocked(assignment, cooldown_sec=BLOCK_COOLDOWN_SEC)
        else:
            active_pool.mark_blocked(assignment, cooldown_sec=BLOCK_COOLDOWN_SEC / 6)

    if SELENIUM_FALLBACK:
        assignment = active_pool.acquire(force_rotate=True)
        if assignment is not None:
            active_pool.wait_for_gap(assignment)
            logger.info(
                "Costco AU Selenium fallback url=%s proxy=%s",
                vendor_url[:120], assignment.label,
            )
            html, final_url, sel_err = _selenium_fetch(vendor_url, session, assignment)
            if not sel_err:
                result = parse_costco_pdp(vendor_url, html, final_url)
                if result.success:
                    active_pool.mark_success(assignment)
                    return result.to_legacy()
                last_error = result.error_code
                last_html_saved = result.raw_html_saved
            else:
                last_error = sel_err

    logger.warning(
        "Costco AU scrape exhausted: url=%s last_error=%s",
        vendor_url[:80], last_error,
    )
    return {
        "price": None,
        "stock": None,
        "title": None,
        "error_code": last_error or "scrape_failed",
        "error_message": f"Costco AU scrape failed after {attempts} attempts ({last_error}).",
        "raw_html_saved": last_html_saved,
    }


def close_costco_au_session(session: Optional[dict]) -> None:
    """Release HTTP / driver resources held in ``session``."""
    if not session:
        return
    client = session.pop(_HTTP_SESSION_KEY, None)
    if client is not None:
        try:
            client.close()
        except Exception:
            pass
    session.pop(_HTTP_PROXY_KEY, None)
    driver = session.pop(_DRIVER_KEY, None)
    _quit_driver(driver)
    session.pop(_DRIVER_PROXY_KEY, None)


__all__ = [
    "scrape_costco_au",
    "close_costco_au_session",
    "parse_costco_pdp",
    "html_is_challenge",
    "is_homepage_redirect",
    "product_id_from_url",
]
