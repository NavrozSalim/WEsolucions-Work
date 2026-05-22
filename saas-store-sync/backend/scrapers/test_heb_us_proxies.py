"""Unit tests for ``scrapers.heb_us_proxies``."""
from django.test import SimpleTestCase

from scrapers import heb_us_proxies


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
