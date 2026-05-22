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
            heb_us_scraper, "_maybe_bootstrap_http_cookies", return_value=False,
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
            heb_us_scraper, "_maybe_bootstrap_http_cookies", return_value=False,
        ), patch.object(
            heb_us_scraper, "_scrape_heb_selenium",
        ) as mock_selenium:
            result = heb_us_scraper.scrape_heb(self.URL, "USA", session={})

        mock_selenium.assert_not_called()
        self.assertIsNone(result.get("price"))
        self.assertEqual(result.get("error_code"), "http_exhausted")

    def test_http_401_is_treated_as_blocked(self):
        """An HTTP 401 must surface as blocked_http_401 (full cooldown + bootstrap)."""
        with patch.dict(os.environ, self.env, clear=False), patch.object(
            heb_us_scraper, "get_pool", return_value=self.pool,
        ), patch.object(
            heb_us_scraper, "_http_fetch",
            return_value=("Unauthorized", self.URL, "http_401"),
        ), patch.object(
            heb_us_scraper, "_maybe_bootstrap_http_cookies", return_value=False,
        ) as mock_bootstrap, patch.object(
            self.pool, "mark_blocked",
        ) as mock_mark, patch.object(
            heb_us_scraper, "SELENIUM_FALLBACK", False,
        ), patch.object(
            heb_us_scraper, "_scrape_heb_selenium",
        ):
            result = heb_us_scraper.scrape_heb(self.URL, "USA", session={})

        mock_bootstrap.assert_called_once()
        self.assertTrue(mock_mark.called)
        cooldown_used = mock_mark.call_args.kwargs.get("cooldown_sec")
        self.assertEqual(cooldown_used, heb_us_scraper.BLOCK_COOLDOWN_SEC)
        self.assertEqual(result.get("error_code"), "http_exhausted")


class HebCookieTests(SimpleTestCase):
    def tearDown(self):
        heb_us_scraper.reset_proxy_cookie_cache_for_tests()
        for key in ("HEB_COOKIES_JSON", "HEB_COOKIES_FILE", "HEB_HTTP_USE_FILE_COOKIES"):
            os.environ.pop(key, None)

    def test_parses_editthiscookie_export_format(self):
        future = int(__import__("time").time()) + 86400 * 365
        cookies = [
            {
                "domain": ".heb.com",
                "expirationDate": future,
                "name": "reese84",
                "path": "/",
                "sameSite": "no_restriction",
                "secure": True,
                "session": False,
                "value": "token-value",
            },
            {
                "domain": "www.heb.com",
                "name": "CURR_SESSION_STORE",
                "path": "/",
                "sameSite": None,
                "secure": True,
                "session": True,
                "value": "92",
            },
            {
                "domain": ".heb.com",
                "expirationDate": 1,
                "name": "expired",
                "path": "/",
                "value": "old",
            },
            {
                "domain": ".heb.com",
                "expirationDate": future,
                "name": "_ga",
                "path": "/",
                "value": "noise",
            },
        ]
        with patch.dict(os.environ, {"HEB_COOKIES_JSON": json.dumps(cookies)}, clear=False):
            parsed = heb_us_scraper.load_heb_cookies()
        names = {c["name"] for c in parsed}
        self.assertIn("reese84", names)
        self.assertIn("CURR_SESSION_STORE", names)
        self.assertNotIn("expired", names)
        self.assertNotIn("_ga", names)
        reese = next(c for c in parsed if c["name"] == "reese84")
        self.assertEqual(reese["sameSite"], "None")

    def test_http_client_gets_cookies_when_configured(self):
        future = int(__import__("time").time()) + 86400
        env = {
            "HEB_COOKIES_JSON": json.dumps([
                {
                    "domain": ".heb.com",
                    "expirationDate": future,
                    "name": "reese84",
                    "path": "/",
                    "value": "abc",
                }
            ]),
            "HEB_HTTP_USE_FILE_COOKIES": "1",
        }
        assignment = heb_us_proxies.ProxyAssignment(
            index=0, url="http://u:p@a:1", label="a:1",
        )
        session: dict = {}
        with patch.dict(os.environ, env, clear=False):
            client = heb_us_scraper._get_http_client(session, assignment)
        jar = getattr(client, "cookies", None)
        self.assertIsNotNone(jar)
        got = jar.get("reese84", domain=".heb.com")
        self.assertIsNotNone(got)
        self.assertEqual(getattr(got, "value", got), "abc")

    def test_http_skips_file_cookies_until_bootstrap_by_default(self):
        future = int(__import__("time").time()) + 86400
        env = {
            "HEB_COOKIES_JSON": json.dumps([
                {
                    "domain": ".heb.com",
                    "expirationDate": future,
                    "name": "reese84",
                    "path": "/",
                    "value": "abc",
                }
            ]),
            "HEB_HTTP_BOOTSTRAP_SELENIUM": "1",
            "HEB_HTTP_USE_FILE_COOKIES": "0",
        }
        heb_us_scraper.reset_proxy_cookie_cache_for_tests()
        assignment = heb_us_proxies.ProxyAssignment(
            index=0, url="http://u:p@a:1", label="a:1",
        )
        with patch.dict(os.environ, env, clear=False):
            client = heb_us_scraper._get_http_client({}, assignment)
        jar = getattr(client, "cookies", None)
        self.assertIsNotNone(jar)
        self.assertIsNone(jar.get("reese84", domain=".heb.com"))

    def test_maybe_bootstrap_reuses_global_proxy_cache(self):
        heb_us_scraper.reset_proxy_cookie_cache_for_tests()
        assignment = heb_us_proxies.ProxyAssignment(
            index=3, url="http://u:p@proxy:8080", label="proxy:8080",
        )
        cookies = [
            {
                "name": "reese84",
                "value": "cached-token",
                "domain": ".heb.com",
                "path": "/",
            }
        ]
        heb_us_scraper._store_global_proxy_cookies(assignment, cookies)
        session: dict = {}
        with patch.object(heb_us_scraper, "_bootstrap_cookies_via_selenium") as mock_boot:
            ok = heb_us_scraper._maybe_bootstrap_http_cookies(
                session,
                assignment,
                "https://www.heb.com/product-detail/377497",
            )
        self.assertTrue(ok)
        mock_boot.assert_not_called()
        self.assertEqual(session[heb_us_scraper._HTTP_COOKIES_CACHE_KEY], cookies)
