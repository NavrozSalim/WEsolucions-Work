"""Tests for eBay AU residential proxy pool."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from scrapers import ebay_au_proxies as proxies


class TestEbayAuProxies(unittest.TestCase):
    def tearDown(self):
        proxies.reset_pool_for_tests()
        for key in (
            "EBAY_AU_PROXY_URLS",
            "COSTCO_AU_PROXY_URLS",
            "EBAY_AU_DISABLE_PROXIES",
            "EBAY_AU_MIN_REQUEST_GAP_SEC",
            "EBAY_AU_HTTP_RETRIES",
        ):
            os.environ.pop(key, None)

    def test_load_ebay_specific_urls_first(self):
        env = {
            "EBAY_AU_PROXY_URLS": "http://u:p@ebay-proxy.example:8080",
            "COSTCO_AU_PROXY_URLS": "http://u:p@costco-proxy.example:8080",
        }
        urls = proxies.load_ebay_au_proxy_urls(env)
        self.assertEqual(len(urls), 1)
        self.assertIn("ebay-proxy.example", urls[0])

    def test_falls_back_to_costco_urls(self):
        env = {"COSTCO_AU_PROXY_URLS": "http://u:p@shared.example:8080"}
        urls = proxies.load_ebay_au_proxy_urls(env)
        self.assertEqual(len(urls), 1)
        self.assertIn("shared.example", urls[0])

    def test_pool_acquire_and_remember(self):
        with patch.dict(
            os.environ,
            {"COSTCO_AU_PROXY_URLS": "http://u:p@p1.example:8080,http://u:p@p2.example:8080"},
            clear=False,
        ):
            proxies.reset_pool_for_tests()
            session: dict = {}
            a1 = proxies.acquire_proxy(session)
            self.assertIsNotNone(a1)
            proxies.remember_proxy(session, a1)
            self.assertEqual(session[proxies._SESSION_PROXY_URL], a1.url)
            a2 = proxies.acquire_proxy(session)
            self.assertEqual(a1.url, a2.url)

    def test_force_rotate_changes_proxy(self):
        with patch.dict(
            os.environ,
            {"COSTCO_AU_PROXY_URLS": "http://u:p@p1.example:8080,http://u:p@p2.example:8080"},
            clear=False,
        ):
            proxies.reset_pool_for_tests()
            session: dict = {}
            a1 = proxies.acquire_proxy(session)
            a2 = proxies.acquire_proxy(session, force_rotate=True)
            self.assertIsNotNone(a1)
            self.assertIsNotNone(a2)
            self.assertNotEqual(a1.url, a2.url)

    def test_proxy_chrome_arg_strips_credentials(self):
        arg = proxies.proxy_chrome_arg("http://user:pass@host.example:1234")
        self.assertEqual(arg, "http://host.example:1234")

    def test_disabled_when_flag_set(self):
        with patch.dict(
            os.environ,
            {
                "COSTCO_AU_PROXY_URLS": "http://u:p@p1.example:8080",
                "EBAY_AU_DISABLE_PROXIES": "1",
            },
            clear=False,
        ):
            proxies.reset_pool_for_tests()
            self.assertFalse(proxies.proxies_configured())

    def test_session_proxy_assignment_roundtrip(self):
        assignment = proxies.ProxyAssignment(index=0, url="http://h:1", label="h:1")
        session = {}
        proxies.remember_proxy(session, assignment)
        got = proxies.session_proxy_assignment(session)
        self.assertIsNotNone(got)
        self.assertEqual(got.url, assignment.url)
        self.assertEqual(got.index, 0)


if __name__ == "__main__":
    unittest.main()
