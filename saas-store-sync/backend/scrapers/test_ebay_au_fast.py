"""Unit tests for eBay AU fast parser (no Selenium)."""
import os
import unittest
from unittest.mock import patch

from scrapers.ebay_au_fast import _parse_fast_html, fast_scrape_enabled


class TestFastScrapeEnabled(unittest.TestCase):
    def test_off_without_cookies(self):
        with patch.dict(os.environ, {"EBAY_AU_COOKIES_FILE": "", "EBAY_AU_COOKIES_JSON": ""}, clear=False):
            self.assertFalse(fast_scrape_enabled())

    def test_on_with_file(self):
        with patch.dict(
            os.environ,
            {"EBAY_AU_COOKIES_FILE": "/tmp/cookies.json", "EBAY_AU_FAST_SCRAPE": "1"},
            clear=False,
        ):
            self.assertTrue(fast_scrape_enabled())


class TestParseFastHtml(unittest.TestCase):
    def test_bin_price_and_stock(self):
        html = (
            "<html><body>"
            '<div class="x-price-primary"><span>AU $9.99</span></div>'
            '<div class="x-quantity__availability">20 available</div>'
            '<h1 class="x-item-title__mainTitle">Test Tuner</h1>'
            "</body></html>"
        )
        out = _parse_fast_html(html, "https://www.ebay.com.au/itm/1")
        self.assertIsNotNone(out)
        self.assertEqual(out["price"], 9.99)
        self.assertEqual(out["stock"], 20)


if __name__ == "__main__":
    unittest.main()
