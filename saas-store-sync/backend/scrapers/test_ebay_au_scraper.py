"""Orchestration tests for ``scrape_ebay_au``.

Verifies the 3-step strategy (HTTP-first → fast Selenium → full US engine)
so a fast-path miss never gives up prematurely.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from scrapers import ebay_au_scraper as au
from scrapers import ebay_common as ec


_TEST_URL = "https://www.ebay.com.au/itm/123456789012"


class TestEbayAuHeaders(unittest.TestCase):
    """Header signature for AU requests must match what the working Costco AU
    scraper uses on the same proxy pool (en-AU locale + Chrome 131 UA matching
    curl_cffi ``impersonate='chrome131'``).
    """

    def test_au_market_uses_au_accept_language(self):
        headers = ec.EbayHTTP._get_headers(
            "https://www.ebay.com.au/itm/123", "AU", {}, ec.EBAY_MARKET_AU,
        )
        self.assertIn("en-AU", headers["Accept-Language"])
        self.assertTrue(headers["Accept-Language"].startswith("en-AU"))

    def test_au_market_uses_pinned_chrome_131_ua(self):
        headers = ec.EbayHTTP._get_headers(
            "https://www.ebay.com.au/itm/123", "AU", {}, ec.EBAY_MARKET_AU,
        )
        self.assertIn("Chrome/131.0.0.0", headers["User-Agent"])

    def test_us_market_keeps_us_locale(self):
        headers = ec.EbayHTTP._get_headers(
            "https://www.ebay.com/itm/123", "US", {}, ec.EBAY_MARKET_US,
        )
        self.assertTrue(headers["Accept-Language"].startswith("en-US"))


class TestScrapeEbayAuOrchestration(unittest.TestCase):
    def setUp(self):
        self.cookies_patch = patch.object(
            au, "fast_scrape_enabled", return_value=True,
        )
        self.cookies_patch.start()
        self.proxy_patch = patch.object(
            au, "proxies_configured", return_value=False,
        )
        self.proxy_patch.start()

    def tearDown(self):
        self.proxy_patch.stop()
        self.cookies_patch.stop()

    def test_http_first_hit_returns_immediately(self):
        hit = {"price": 35.0, "stock": 1, "title": "T"}
        with patch.object(au.EbayHTTP, "fetch", return_value=("<html>x</html>", 200, "")), \
             patch.object(au, "_parse_html_to_result_au", return_value=hit), \
             patch.object(au, "_au_http_shipping_resolved", return_value=True), \
             patch.object(au, "_ensure_cookies_in_http_client") as cookies_mock, \
             patch.object(au, "scrape_ebay_au_fast") as fast_mock, \
             patch.object(au, "scrape_ebay_for_market") as full_mock:
            result = au.scrape_ebay_au(_TEST_URL, "AU", {})
        self.assertEqual(result, hit)
        fast_mock.assert_not_called()
        full_mock.assert_not_called()
        cookies_mock.assert_called_once()

    def test_http_first_unhydrated_shipping_falls_through_to_fast_selenium(self):
        http_hit = {"price": 32.0, "stock": 5, "title": "Logitech"}
        selenium_hit = {"price": 44.99, "stock": 5, "title": "Logitech"}
        with patch.object(au.EbayHTTP, "fetch", return_value=("<html>partial</html>", 200, "")), \
             patch.object(au, "_parse_html_to_result_au", return_value=http_hit), \
             patch.object(au, "_au_http_shipping_resolved", return_value=False), \
             patch.object(au, "scrape_ebay_au_fast", return_value=selenium_hit) as fast_mock, \
             patch.object(au, "scrape_ebay_for_market") as full_mock:
            result = au.scrape_ebay_au(_TEST_URL, "AU", {})
        self.assertEqual(result, selenium_hit)
        fast_mock.assert_called_once()
        full_mock.assert_not_called()

    def test_http_first_ended_listing_is_terminal(self):
        ended = {"price": None, "stock": 0, "title": "Ended item"}
        with patch.object(au.EbayHTTP, "fetch", return_value=("<html>ended</html>", 200, "")), \
             patch.object(au, "_parse_html_to_result_au", return_value=ended), \
             patch.object(au, "scrape_ebay_au_fast") as fast_mock, \
             patch.object(au, "scrape_ebay_for_market") as full_mock:
            result = au.scrape_ebay_au(_TEST_URL, "AU", {})
        self.assertEqual(result, ended)
        fast_mock.assert_not_called()
        full_mock.assert_not_called()

    def test_http_blocked_skips_fast_selenium_goes_to_full_engine(self):
        full_hit = {"price": 22.5, "stock": 3, "title": "T"}
        session = {}
        with patch.object(au, "proxies_configured", return_value=False), \
             patch.object(au.EbayHTTP, "fetch", return_value=("", None, "challenge")), \
             patch.object(au, "scrape_ebay_au_fast") as fast_mock, \
             patch.object(au, "scrape_ebay_for_market", return_value=full_hit) as full_mock:
            result = au.scrape_ebay_au(_TEST_URL, "AU", session)
        self.assertEqual(result, full_hit)
        fast_mock.assert_not_called()
        full_mock.assert_called_once()
        self.assertEqual(session.get(au._AU_BLOCKED_KEY), "challenge")

    def test_proxies_enabled_skips_fast_selenium_entirely(self):
        """Webshare proxies can't auth Chrome --proxy-server, so fast Selenium is useless.

        When the HTTP block is a non-proxy-block kind (e.g. ``not_product_like``) we
        still continue to the full engine; this guards that fall-through path.
        """
        full_hit = {"price": 22.5, "stock": 3, "title": "T"}
        session = {}
        with patch.object(au, "proxies_configured", return_value=True), \
             patch.object(au.EbayHTTP, "fetch", return_value=("", None, "not_product_like")), \
             patch.object(au, "scrape_ebay_au_fast") as fast_mock, \
             patch.object(au, "scrape_ebay_for_market", return_value=full_hit) as full_mock:
            result = au.scrape_ebay_au(_TEST_URL, "AU", session)
        self.assertEqual(result, full_hit)
        fast_mock.assert_not_called()
        full_mock.assert_called_once()

    def test_proxies_enabled_bails_fast_on_http_403(self):
        """When proxies are on and HTTP-first hits ``http_403``, skip the full engine.

        Repeating the same blocked HTTP fetch on cooled-down proxies wastes 20-40s
        per item in production (every eBay AU PDP log line in the 15:00 window).
        """
        session = {}
        with patch.object(au, "proxies_configured", return_value=True), \
             patch.object(au.EbayHTTP, "fetch", return_value=("", None, "http_403")), \
             patch.object(au, "scrape_ebay_au_fast") as fast_mock, \
             patch.object(au, "scrape_ebay_for_market") as full_mock, \
             patch.object(au, "_title_from_session_html", return_value=None):
            result = au.scrape_ebay_au(_TEST_URL, "AU", session)
        self.assertIsNone(result["price"])
        fast_mock.assert_not_called()
        full_mock.assert_not_called()

    def test_proxies_disabled_still_runs_full_engine_after_block(self):
        """Without proxies the full engine *can* try Selenium, so we still call it."""
        full_hit = {"price": 22.5, "stock": 3, "title": "T"}
        session = {}
        with patch.object(au, "proxies_configured", return_value=False), \
             patch.object(au.EbayHTTP, "fetch", return_value=("", None, "http_403")), \
             patch.object(au, "scrape_ebay_au_fast", return_value=None), \
             patch.object(au, "scrape_ebay_for_market", return_value=full_hit) as full_mock:
            result = au.scrape_ebay_au(_TEST_URL, "AU", session)
        self.assertEqual(result, full_hit)
        full_mock.assert_called_once()

    def test_proxies_enabled_full_engine_capped_to_one_attempt_after_block(self):
        """If the bail flag is off but a block was recorded, cap full engine at 1 attempt."""
        with patch.object(au, "proxies_configured", return_value=True), \
             patch.object(au, "_bail_on_http_block_when_proxied", return_value=False), \
             patch.object(au.EbayHTTP, "fetch", return_value=("", None, "http_403")), \
             patch.object(au, "scrape_ebay_au_fast", return_value=None), \
             patch.object(au, "scrape_ebay_for_market", return_value={"price": 1.0}) as full_mock:
            au.scrape_ebay_au(_TEST_URL, "AU", {})
        _, kwargs = full_mock.call_args
        self.assertEqual(kwargs.get("max_attempts"), 1)

    def test_http_first_parse_miss_retries_with_warm_session(self):
        """HTTP returns HTML but parser misses → retry HTTP once (warm BIN hydration)."""
        miss_then_hit = {"price": 49.99, "stock": 1, "title": "T"}
        fetch_results = [
            ("<html>nopricyet</html>", 200, ""),
            ("<html>now-with-price</html>", 200, ""),
        ]
        parse_results = [None, miss_then_hit]

        def fetch_side(*_a, **_k):
            return fetch_results.pop(0)

        def parse_side(*_a, **_k):
            return parse_results.pop(0)

        with patch.object(au.EbayHTTP, "fetch", side_effect=fetch_side) as fetch_mock, \
             patch.object(au, "_parse_html_to_result_au", side_effect=parse_side), \
             patch.object(au, "_au_http_shipping_resolved", return_value=True), \
             patch.object(au, "scrape_ebay_au_fast") as fast_mock, \
             patch.object(au, "scrape_ebay_for_market") as full_mock:
            result = au.scrape_ebay_au(_TEST_URL, "AU", {})
        self.assertEqual(result, miss_then_hit)
        self.assertEqual(fetch_mock.call_count, 2)
        fast_mock.assert_not_called()
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
             patch.object(au, "_parse_html_to_result_au", return_value=None), \
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
