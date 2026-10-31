"""Tests for the Amazon US and eBay US residential proxy pools."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from scrapers import amazon_us_proxies, ebay_us_proxies

_PROXY_ENV = (
    "US_PROXY_URLS",
    "AMAZON_US_PROXY_URLS",
    "EBAY_US_PROXY_URLS",
    "AMAZON_US_DISABLE_PROXIES",
    "EBAY_US_DISABLE_PROXIES",
    "AMAZON_US_SELENIUM_WITH_PROXIES",
    "EBAY_US_FULL_ENGINE_SELENIUM",
)


class _ResetPools(SimpleTestCase):
    def setUp(self):
        amazon_us_proxies.reset_pool_for_tests()
        ebay_us_proxies.reset_pool_for_tests()
        self.addCleanup(amazon_us_proxies.reset_pool_for_tests)
        self.addCleanup(ebay_us_proxies.reset_pool_for_tests)
        saved = {k: os.environ.pop(k) for k in _PROXY_ENV if k in os.environ}
        self.addCleanup(os.environ.update, saved)
        for k in _PROXY_ENV:
            self.addCleanup(os.environ.pop, k, None)


class USProxyUrlTests(_ResetPools):
    def test_amazon_list_wins_over_shared(self):
        urls = amazon_us_proxies.load_amazon_us_proxy_urls(env={
            "AMAZON_US_PROXY_URLS": "http://u:p@amz:1",
            "US_PROXY_URLS": "http://u:p@shared:2",
        })
        self.assertEqual(urls, ["http://u:p@amz:1"])

    def test_ebay_falls_back_to_shared_us_list(self):
        urls = ebay_us_proxies.load_ebay_us_proxy_urls(env={"US_PROXY_URLS": "http://u:p@shared:2"})
        self.assertEqual(urls, ["http://u:p@shared:2"])

    def test_au_and_generic_lists_are_ignored(self):
        env = {
            "PROXY_URLS": "http://u:p@generic:1",
            "COSTCO_AU_PROXY_URLS": "http://u:p@costco:2",
            "EBAY_AU_PROXY_URLS": "http://u:p@ebay-au:3",
        }
        self.assertEqual(amazon_us_proxies.load_amazon_us_proxy_urls(env=env), [])
        self.assertEqual(ebay_us_proxies.load_ebay_us_proxy_urls(env=env), [])

    def test_disable_flag_turns_pool_off(self):
        with patch.dict(os.environ, {
            "AMAZON_US_PROXY_URLS": "http://u:p@amz:1",
            "AMAZON_US_DISABLE_PROXIES": "1",
        }):
            self.assertFalse(amazon_us_proxies.proxies_configured())

    def test_ebay_force_rotate_changes_proxy(self):
        with patch.dict(os.environ, {"EBAY_US_PROXY_URLS": "http://u:p@a:1,http://u:p@b:2"}):
            session: dict = {}
            first = ebay_us_proxies.acquire_proxy(session)
            second = ebay_us_proxies.acquire_proxy(session, force_rotate=True)
        self.assertNotEqual(first.url, second.url)


class AmazonUSProxyHttpTests(_ResetPools):
    def test_session_uses_proxy_and_rotation_resets_zip(self):
        from scrapers.amazon_us_scraper import AmazonHTTP

        with patch.dict(os.environ, {"AMAZON_US_PROXY_URLS": "http://u:p@a:1,http://u:p@b:2"}):
            session = {}
            first = AmazonHTTP._get_session(session)
            session["amazon_http_zip_set"] = True
            second = AmazonHTTP._get_session(session, rotate=True)
        self.assertIn(first.proxies["https"], ("http://u:p@a:1", "http://u:p@b:2"))
        self.assertNotEqual(first.proxies["https"], second.proxies["https"])
        self.assertNotIn("amazon_http_zip_set", session)

    def test_block_rotates_proxy_without_backoff(self):
        from scrapers import amazon_us_scraper
        from scrapers.core import ScrapeResult

        results = [
            ScrapeResult.fail("blocked", "x", "", "amazon_us", "u"),
            ScrapeResult.ok(price=10.0, stock=2, title="t"),
        ]
        with patch.dict(os.environ, {"AMAZON_US_PROXY_URLS": "http://u:p@a:1,http://u:p@b:2"}), \
                patch.object(amazon_us_scraper.AmazonHTTP, "fetch", side_effect=results), \
                patch.object(amazon_us_scraper.AmazonHTTP, "_get_session") as get_session, \
                patch.object(amazon_us_scraper, "backoff_delay") as backoff:
            out = amazon_us_scraper.AmazonHTTP.scrape_with_retry("https://www.amazon.com/dp/B000000001", {})
        self.assertTrue(out.success)
        backoff.assert_not_called()
        get_session.assert_called_once_with({}, rotate=True)

    def test_chrome_fallback_skipped_while_proxies_are_set(self):
        from scrapers import amazon_us_scraper
        from scrapers.core import ScrapeResult

        failed = ScrapeResult.fail("no_price", "x", "", "amazon_us", "u")
        with patch.dict(os.environ, {"AMAZON_US_PROXY_URLS": "http://u:p@a:1"}), \
                patch.object(amazon_us_scraper.AmazonHTTP, "scrape_with_retry", return_value=failed), \
                patch.object(amazon_us_scraper.AmazonDriver, "create") as create:
            out = amazon_us_scraper.scrape_amazon_us("https://www.amazon.com/dp/B000000001", "USA", {})
        create.assert_not_called()
        self.assertIsNone(out["price"])


class EbayUSProxyHttpTests(_ResetPools):
    def test_us_fetch_goes_through_us_proxy(self):
        from scrapers import ebay_common

        resp = MagicMock(status_code=200, text="<html>item</html>", url="https://www.ebay.com/itm/1")
        client = MagicMock()
        client.get.return_value = resp
        with patch.dict(os.environ, {"EBAY_US_PROXY_URLS": "http://u:p@a:1"}), \
                patch.object(ebay_common.EbayHTTP, "_get_client", return_value=client), \
                patch.object(ebay_common, "_is_challenge_or_blocked", return_value=(False, "")), \
                patch.object(ebay_common, "_looks_like_product_html", return_value=True):
            html, status, err = ebay_common.EbayHTTP.fetch(
                "https://www.ebay.com/itm/1", "USA", {}, ebay_common.EBAY_MARKET_US,
            )
        self.assertEqual((status, err), (200, ""))
        self.assertEqual(client.proxies, {"http": "http://u:p@a:1", "https": "http://u:p@a:1"})

    def test_missing_item_does_not_burn_the_proxy(self):
        from scrapers import ebay_common

        resp = MagicMock(status_code=404, text="gone", url="https://www.ebay.com/itm/1")
        client = MagicMock()
        client.get.return_value = resp
        with patch.dict(os.environ, {"EBAY_US_PROXY_URLS": "http://u:p@a:1,http://u:p@b:2"}), \
                patch.object(ebay_common.EbayHTTP, "_get_client", return_value=client):
            _html, status, err = ebay_common.EbayHTTP.fetch(
                "https://www.ebay.com/itm/1", "USA", {}, ebay_common.EBAY_MARKET_US,
            )
            pool = ebay_us_proxies.get_ebay_us_pool()
            self.assertEqual(pool.seconds_until_ready(), 0.0)
        self.assertEqual((status, err), (404, "http_404"))
        self.assertEqual(client.get.call_count, 1)

    def test_http_only_while_us_proxies_are_set(self):
        from scrapers import ebay_common

        with patch.dict(os.environ, {"EBAY_US_PROXY_URLS": "http://u:p@a:1"}):
            self.assertTrue(ebay_common._ebay_http_only_with_proxies(ebay_common.EBAY_MARKET_US))
        ebay_us_proxies.reset_pool_for_tests()
        with patch.dict(os.environ, {
            "EBAY_US_PROXY_URLS": "http://u:p@a:1",
            "EBAY_US_FULL_ENGINE_SELENIUM": "1",
        }):
            self.assertFalse(ebay_common._ebay_http_only_with_proxies(ebay_common.EBAY_MARKET_US))
