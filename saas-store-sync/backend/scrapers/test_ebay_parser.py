"""Unit tests for eBay parser helpers (run: python -m unittest scrapers.test_ebay_parser -v)."""
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from scrapers.core import parse_price_text
from scrapers.ebay_scraper import EbayParser, _strip_price_suffix


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
