"""Unit tests for ``scrapers.heb_us_proxies`` and HEB HTTP-first scraping."""
from __future__ import annotations

import json
import os
from unittest.mock import patch

from django.test import SimpleTestCase

from scrapers import heb_us_proxies, heb_us_scraper


def _heb_pdp_html(*, price: float = 4.98, title: str = "HEB Test Milk", stock_qty: int = 12) -> str:
    next_data = {
        "props": {
            "pageProps": {
                "pdpData": {
                    "product": {
                        "name": title,
                        "price": {"value": price},
                        "inventory": {"quantity": stock_qty},
                    }
                }
            }
        }
    }
    pad = " " * 2500
    return (
        f"<!DOCTYPE html><html><head><title>{title}</title></head><body>"
        f"<h1>{title}</h1>"
        f"<script id='__NEXT_DATA__' type='application/json'>"
        f"{json.dumps(next_data)}"
        f"</script>{pad}</body></html>"
    )


class HebUsProxyLoadTests(SimpleTestCase):
    def setUp(self):
        heb_us_proxies.reset_pool_for_tests()

    def tearDown(self):
        heb_us_proxies.reset_pool_for_tests()

    def test_loads_heb_us_proxy_urls(self):
        env = {
            "HEB_US_PROXY_URLS": "http://u:p@a:1, http://u:p@b:2",
        }
        urls = heb_us_proxies.load_proxy_urls(env=env)
        self.assertEqual(len(urls), 2)
        self.assertIn("a:1", urls[0])
        self.assertIn("b:2", urls[1])

    def test_heb_us_takes_precedence_over_generic_proxy_urls(self):
        env = {
            "HEB_US_PROXY_URLS": "http://u:p@heb-only:8080",
            "PROXY_URLS": "http://u:p@generic:9090",
        }
        urls = heb_us_proxies.load_proxy_urls(env=env)
        self.assertEqual(len(urls), 1)
        self.assertIn("heb-only", urls[0])

    def test_proxies_configured_false_when_empty(self):
        self.assertFalse(heb_us_proxies.proxies_configured(env={}))

    def test_proxies_configured_true_when_set(self):
        env = {"HEB_US_PROXY_URL": "http://u:p@a:1"}
        self.assertTrue(heb_us_proxies.proxies_configured(env=env))


class HebHttpFirstTests(SimpleTestCase):
    URL = "https://www.heb.com/product-detail/377497"

    def setUp(self):
        heb_us_proxies.reset_pool_for_tests()
        self.pool = heb_us_proxies.HebUsProxyPool(
            ["http://u:p@a:1", "http://u:p@b:2"], min_gap_sec=0.0,
        )
        self.env = {
            "HEB_US_PROXY_URLS": "http://u:p@a:1,http://u:p@b:2",
            "HEB_USE_APIFY": "0",
            "HEB_HTTP_FIRST": "1",
            "HEB_SELENIUM_FALLBACK": "1",
        }

    def tearDown(self):
        heb_us_proxies.reset_pool_for_tests()
        for key in (
            "HEB_US_PROXY_URLS",
            "HEB_USE_APIFY",
            "HEB_HTTP_FIRST",
            "HEB_SELENIUM_FALLBACK",
        ):
            os.environ.pop(key, None)

    def test_http_first_returns_price_without_selenium(self):
        html = _heb_pdp_html()
        with patch.dict(os.environ, self.env, clear=False), patch.object(
            heb_us_scraper, "get_pool", return_value=self.pool,
        ), patch.object(
            heb_us_scraper, "_http_fetch",
            return_value=(html, self.URL, ""),
        ) as mock_fetch, patch.object(
            heb_us_scraper, "_scrape_heb_selenium",
        ) as mock_selenium:
            result = heb_us_scraper.scrape_heb(self.URL, "USA", session={})

        mock_fetch.assert_called_once()
        mock_selenium.assert_not_called()
        self.assertEqual(result["price"], 4.98)
        self.assertEqual(result["stock"], 3)
        self.assertIn("HEB Test Milk", result["title"])

    def test_http_blocked_falls_back_to_selenium(self):
        with patch.dict(os.environ, self.env, clear=False), patch.object(
            heb_us_scraper, "get_pool", return_value=self.pool,
        ), patch.object(
            heb_us_scraper, "_http_fetch",
            return_value=("<html>blocked</html>", self.URL, "http_403"),
        ), patch.object(
            heb_us_scraper, "_scrape_heb_selenium",
            return_value={"price": 9.99, "stock": 1, "title": "Fallback"},
        ) as mock_selenium:
            result = heb_us_scraper.scrape_heb(self.URL, "USA", session={})

        mock_selenium.assert_called_once()
        self.assertEqual(result["price"], 9.99)

    def test_http_only_when_selenium_fallback_disabled(self):
        env = {**self.env, "HEB_SELENIUM_FALLBACK": "0"}
        with patch.dict(os.environ, env, clear=False), patch.object(
            heb_us_scraper, "get_pool", return_value=self.pool,
        ), patch.object(
            heb_us_scraper, "SELENIUM_FALLBACK", False,
        ), patch.object(
            heb_us_scraper, "_http_fetch",
            return_value=("<html>blocked</html>", self.URL, "http_403"),
        ), patch.object(
            heb_us_scraper, "_scrape_heb_selenium",
        ) as mock_selenium:
            result = heb_us_scraper.scrape_heb(self.URL, "USA", session={})

        mock_selenium.assert_not_called()
        self.assertIsNone(result.get("price"))
        self.assertEqual(result.get("error_code"), "http_exhausted")
