"""Orchestration tests for ``scrape_ebay_au``.

Verifies the 3-step strategy (HTTP-first → fast Selenium → full US engine)
so a fast-path miss never gives up prematurely.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from scrapers import ebay_au_scraper as au


_TEST_URL = "https://www.ebay.com.au/itm/123456789012"


class TestScrapeEbayAuOrchestration(unittest.TestCase):
    def setUp(self):
        self.cookies_patch = patch.object(
            au, "fast_scrape_enabled", return_value=True,
        )
        self.cookies_patch.start()

    def tearDown(self):
        self.cookies_patch.stop()

    def test_http_first_hit_returns_immediately(self):
        hit = {"price": 35.0, "stock": 1, "title": "T"}
        with patch.object(au.EbayHTTP, "fetch", return_value=("<html>x</html>", 200, "")), \
             patch.object(au, "_parse_html_to_result", return_value=hit), \
             patch.object(au, "_ensure_cookies_in_http_client") as cookies_mock, \
             patch.object(au, "scrape_ebay_au_fast") as fast_mock, \
             patch.object(au, "scrape_ebay_for_market") as full_mock:
            result = au.scrape_ebay_au(_TEST_URL, "AU", {})
        self.assertEqual(result, hit)
        fast_mock.assert_not_called()
        full_mock.assert_not_called()
        cookies_mock.assert_called_once()

    def test_http_first_ended_listing_is_terminal(self):
        ended = {"price": None, "stock": 0, "title": "Ended item"}
        with patch.object(au.EbayHTTP, "fetch", return_value=("<html>ended</html>", 200, "")), \
             patch.object(au, "_parse_html_to_result", return_value=ended), \
             patch.object(au, "scrape_ebay_au_fast") as fast_mock, \
             patch.object(au, "scrape_ebay_for_market") as full_mock:
            result = au.scrape_ebay_au(_TEST_URL, "AU", {})
        self.assertEqual(result, ended)
        fast_mock.assert_not_called()
        full_mock.assert_not_called()

    def test_http_blocked_falls_through_to_fast_selenium(self):
        hit = {"price": 22.5, "stock": 3, "title": "T"}
        with patch.object(au.EbayHTTP, "fetch", return_value=("", None, "challenge")), \
             patch.object(au, "_parse_html_to_result") as parse_mock, \
             patch.object(au, "scrape_ebay_au_fast", return_value=hit) as fast_mock, \
             patch.object(au, "scrape_ebay_for_market") as full_mock:
            result = au.scrape_ebay_au(_TEST_URL, "AU", {})
        self.assertEqual(result, hit)
        parse_mock.assert_not_called()
        fast_mock.assert_called_once()
        full_mock.assert_not_called()

    def test_both_fast_paths_miss_runs_full_engine(self):
        full_hit = {"price": 99.0, "stock": 7, "title": "T"}
        with patch.object(au.EbayHTTP, "fetch", return_value=("", None, "challenge")), \
             patch.object(au, "scrape_ebay_au_fast", return_value=None), \
             patch.object(au, "scrape_ebay_for_market", return_value=full_hit) as full_mock:
            result = au.scrape_ebay_au(_TEST_URL, "AU", {})
        self.assertEqual(result, full_hit)
        full_mock.assert_called_once()

    def test_http_returns_html_without_parse_falls_through(self):
        """HTTP succeeded but parser says 'not a real PDP' (returns None) → keep trying."""
        full_hit = {"price": 55.0, "stock": 2, "title": "T"}
        with patch.object(au.EbayHTTP, "fetch", return_value=("<html>...</html>", 200, "")), \
             patch.object(au, "_parse_html_to_result", return_value=None), \
             patch.object(au, "scrape_ebay_au_fast", return_value=None), \
             patch.object(au, "scrape_ebay_for_market", return_value=full_hit) as full_mock:
            result = au.scrape_ebay_au(_TEST_URL, "AU", {})
        self.assertEqual(result, full_hit)
        full_mock.assert_called_once()

    def test_full_engine_failure_returns_title_fallback(self):
        with patch.object(au.EbayHTTP, "fetch", return_value=("", None, "challenge")), \
             patch.object(au, "scrape_ebay_au_fast", return_value=None), \
             patch.object(au, "scrape_ebay_for_market",
                          return_value={"price": None, "stock": None, "title": None}), \
             patch.object(au, "_title_from_session_html", return_value="Last Title"):
            result = au.scrape_ebay_au(_TEST_URL, "AU", {})
        self.assertIsNone(result["price"])
        self.assertIsNone(result["stock"])
        self.assertEqual(result["title"], "Last Title")

    def test_fast_selenium_disabled_when_no_cookies(self):
        """No cookies configured → skip fast Selenium, go straight to full engine."""
        full_hit = {"price": 12.0, "stock": 1, "title": "T"}
        self.cookies_patch.stop()
        try:
            with patch.object(au, "fast_scrape_enabled", return_value=False), \
                 patch.object(au.EbayHTTP, "fetch", return_value=("", None, "challenge")), \
                 patch.object(au, "scrape_ebay_au_fast") as fast_mock, \
                 patch.object(au, "scrape_ebay_for_market", return_value=full_hit) as full_mock:
                result = au.scrape_ebay_au(_TEST_URL, "AU", {})
        finally:
            self.cookies_patch.start()
        self.assertEqual(result, full_hit)
        fast_mock.assert_not_called()
        full_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
