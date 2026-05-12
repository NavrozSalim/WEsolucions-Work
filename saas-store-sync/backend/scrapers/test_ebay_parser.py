"""Unit tests for eBay parser helpers (run: python -m unittest scrapers.test_ebay_parser -v)."""
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from scrapers.core import parse_price_text
from scrapers.ebay_scraper import (
    EbayParser,
    _effective_ebay_region,
    _normalize_url,
    _strip_price_suffix,
)


class TestNormalizeUrl(unittest.TestCase):
    def test_au_hostname_wins_over_usa_region(self):
        u = "https://www.ebay.com.au/itm/Some-Title/123456789012"
        self.assertEqual(
            _normalize_url(u, "USA"),
            "https://www.ebay.com.au/itm/123456789012",
        )

    def test_au_region_promotes_com_to_au(self):
        u = "https://www.ebay.com/itm/123456789012"
        self.assertEqual(
            _normalize_url(u, "AU"),
            "https://www.ebay.com.au/itm/123456789012",
        )

    def test_au_preserves_query_for_variation(self):
        u = "https://www.ebay.com.au/itm/Some-Title/123456789012?var=440888889999"
        self.assertEqual(
            _normalize_url(u, "USA"),
            "https://www.ebay.com.au/itm/123456789012?var=440888889999",
        )


class TestEffectiveEbayRegion(unittest.TestCase):
    def test_normalized_au_url_forces_au(self):
        self.assertEqual(
            _effective_ebay_region("USA", "https://www.ebay.com.au/itm/1"),
            "AU",
        )


class TestEbayBuyNowDisplayPrice(unittest.TestCase):
    """Prefer visible sale price over struck 'was' amount in primary BIN block."""

    def test_was_now_picks_lower_non_strike(self):
        html = """<html><body>
        <section data-testid="x-item-price">
          <div data-testid="x-price-primary">
            <span class="ux-textspans ux-textspans--STRIKETHROUGH">AU $21.60</span>
            <span class="ux-textspans ux-textspans--BOLD">AU $18.00</span>
          </div>
        </section>
        </body></html>"""
        soup = BeautifulSoup(html, "lxml")
        self.assertEqual(EbayParser.extract_price(soup, html), 18.0)


class TestEbayPriceSuffix(unittest.TestCase):
    def test_strip_buy_it_now(self):
        raw = "US $19.99Buy It Now"
        cleaned = _strip_price_suffix(raw)
        self.assertEqual(parse_price_text(cleaned), 19.99)

    def test_strip_best_offer(self):
        raw = "AU $12.50 or Best Offer"
        cleaned = _strip_price_suffix(raw)
        self.assertEqual(parse_price_text(cleaned), 12.5)


class TestEbayDebugSnapshot(unittest.TestCase):
    def test_madrona_price_in_saved_html(self):
        p = Path(__file__).resolve().parent / "debug_html" / "ebay_no_price_20260327_215350_406073482378.html"
        if not p.is_file():
            self.skipTest("debug HTML snapshot not present")
        html = p.read_text(encoding="utf-8", errors="replace")
        soup = BeautifulSoup(html, "lxml")
        self.assertEqual(EbayParser.extract_price(soup, html), 34.69)


if __name__ == "__main__":
    unittest.main()
