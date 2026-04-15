"""
Costco AU product scraper (HTTP + BeautifulSoup).

Public API:
  scrape_costco_au(vendor_url, region, session=None) -> {"price": float|None, "stock": int|None, "title": str|None}
  close_costco_au_session(session)
"""

import json
import logging
import re
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .core import ScrapeResult, detect_block, parse_price_text, random_delay

logger = logging.getLogger("scrapers.costco_au")

RETRY_LIMIT = 2
FETCH_TIMEOUT = 30

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
]


def _get_session(session_dict: dict | None) -> requests.Session:
    key = "costco_au_http_session"
    if session_dict is not None and key in session_dict:
        return session_dict[key]

    s = requests.Session()
    s.headers.update(
        {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-AU,en-US;q=0.7,en;q=0.3",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
    )
    if session_dict is not None:
        session_dict[key] = s
    return s


def _extract_title(soup: BeautifulSoup) -> Optional[str]:
    for sel in ("h1", "meta[property='og:title']", "meta[name='twitter:title']"):
        el = soup.select_one(sel)
        if not el:
            continue
        text = (el.get("content") or "").strip() if el.name == "meta" else el.get_text(" ", strip=True)
        if text and len(text) > 2:
            return text[:500]
    return None


def _extract_price(soup: BeautifulSoup, html: str) -> Optional[float]:
    for sel in (
        "meta[property='product:price:amount']",
        "meta[itemprop='price']",
        "[itemprop='price']",
        "[data-testid*='price']",
        "span[class*='price' i]",
    ):
        el = soup.select_one(sel)
        if not el:
            continue
        raw = (el.get("content") or "").strip() if el.name == "meta" else el.get_text(" ", strip=True)
        p = parse_price_text(raw)
        if p is not None:
            return p

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
                    p = parse_price_text(str(node.get("price")))
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
            return parsed

    html = html or ""
    for pat in (
        r'"product:price:amount"\s*content\s*=\s*"([^"]+)"',
        r'"price"\s*:\s*"?\$?(\d+(?:\.\d{1,2})?)',
        r'"finalPrice"\s*:\s*"?\$?(\d+(?:\.\d{1,2})?)',
    ):
        m = re.search(pat, html, re.IGNORECASE)
        if not m:
            continue
        p = parse_price_text(m.group(1))
        if p is not None:
            return p
    return None


def _extract_stock(soup: BeautifulSoup, html: str) -> int:
    """
    Requested business rule:
    - If Add to Cart is available -> 3
    - Otherwise -> 0
    """
    text = ((soup.get_text(" ", strip=True) or "") + " " + (html or "")).lower()
    if any(x in text for x in ("out of stock", "sold out", "unavailable")):
        return 0

    for sel in (
        "button[data-testid*='add' i]",
        "button.add-to-cart",
        "button.btn-block",
        "button.notranslate",
        "button",
    ):
        for btn in soup.select(sel):
            btn_text = (btn.get_text(" ", strip=True) or "").lower()
            if "add to cart" not in btn_text:
                continue
            disabled = btn.has_attr("disabled")
            aria_disabled = (btn.get("aria-disabled") or "").lower() == "true"
            cls = " ".join(btn.get("class") or []).lower()
            class_disabled = "disabled" in cls
            if not (disabled or aria_disabled or class_disabled):
                return 3
    return 0


def _is_blocked(html: str) -> tuple[bool, str]:
    blocked, reason = detect_block(html)
    if blocked:
        return True, reason
    lower = (html or "").lower()
    for needle in (
        "access denied",
        "pardon our interruption",
        "request unsuccessful",
        "captcha",
        "verify you are human",
    ):
        if needle in lower:
            return True, "waf_challenge"
    return False, ""


def scrape_costco_au(vendor_url: str, region: str, session: dict = None) -> dict:
    s = _get_session(session)
    last = None

    for attempt in range(RETRY_LIMIT + 1):
        if attempt:
            random_delay(1.0, 2.2)

        headers = {"User-Agent": USER_AGENTS[attempt % len(USER_AGENTS)]}
        try:
            resp = s.get(vendor_url, timeout=FETCH_TIMEOUT, headers=headers, allow_redirects=True)
        except requests.Timeout:
            last = ScrapeResult.fail("timeout", "Costco AU HTTP timeout", "", "costco_au", vendor_url)
            continue
        except requests.RequestException as exc:
            last = ScrapeResult.fail("request_error", str(exc), "", "costco_au", vendor_url)
            continue

        html = resp.text or ""
        if resp.status_code != 200:
            last = ScrapeResult.fail(
                f"http_{resp.status_code}",
                f"Costco AU HTTP {resp.status_code}",
                html,
                "costco_au",
                vendor_url,
            )
            continue

        blocked, reason = _is_blocked(html)
        if blocked:
            logger.warning("Costco AU blocked (%s) attempt %d url=%s", reason, attempt, vendor_url)
            last = ScrapeResult.fail(f"blocked_{reason}", f"Blocked: {reason}", html, "costco_au", vendor_url)
            continue

        soup = BeautifulSoup(html, "lxml")
        title = _extract_title(soup)
        price = _extract_price(soup, html)
        stock = _extract_stock(soup, html)

        if price is None:
            last = ScrapeResult.fail("no_price", "Price not found on Costco AU page", html, "costco_au", vendor_url)
            continue

        return ScrapeResult.ok(price=price, stock=stock, title=title).to_legacy()

    return (
        last or ScrapeResult.fail("max_retries", "Costco AU retries exhausted", "", "costco_au", vendor_url)
    ).to_legacy()


def close_costco_au_session(session):
    if session is None:
        return
    s = session.pop("costco_au_http_session", None)
    try:
        if s:
            s.close()
    except Exception:
        pass

