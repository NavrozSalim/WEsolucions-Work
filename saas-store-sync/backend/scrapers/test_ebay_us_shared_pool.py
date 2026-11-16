"""eBay US proxies: rest and use shared by all workers, waiting for a free proxy."""
from __future__ import annotations

import os
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase

from catalog.scrape_failure import leave_pending_after_block
from scrapers import ebay_common, ebay_us_proxies
from scrapers.ebay_common import EBAY_MARKET_US, scrape_ebay_for_market

_ENV = {
    "EBAY_US_PROXY_URLS": "http://u:p@a:1,http://u:p@b:2,http://u:p@c:3",
    "EBAY_US_MIN_REQUEST_GAP_SEC": "0",
    "EBAY_US_PROXY_SHARED": "1",
}


class SharedPoolTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        ebay_us_proxies.reset_pool_for_tests()
        self.addCleanup(ebay_us_proxies.reset_pool_for_tests)
        self.addCleanup(cache.clear)
        env = patch.dict(os.environ, _ENV)
        env.start()
        self.addCleanup(env.stop)

    def _as(self, worker):
        return patch.object(ebay_us_proxies, "_owner", return_value=worker)

    def test_a_proxy_held_by_one_worker_is_not_given_to_another(self):
        taken = set()
        for worker in ("w1", "w2", "w3"):
            with self._as(worker):
                got = ebay_us_proxies.acquire_shared(None)
            self.assertIsNotNone(got)
            taken.add(got.label)
        self.assertEqual(len(taken), 3)
        with self._as("w4"):
            self.assertIsNone(ebay_us_proxies.acquire_shared(None))

    def test_a_worker_keeps_its_own_proxy(self):
        with self._as("w1"):
            first = ebay_us_proxies.acquire_shared(None)
            again = ebay_us_proxies.acquire_shared(first)
        self.assertEqual(first.label, again.label)

    def test_a_refused_proxy_rests_for_every_worker(self):
        with self._as("w1"):
            got = ebay_us_proxies.acquire_shared(None)
            ebay_us_proxies.mark_proxy_blocked(got)
        for worker in ("w2", "w3", "w4", "w5"):
            with self._as(worker):
                other = ebay_us_proxies.acquire_shared(None)
            if other is not None:
                self.assertNotEqual(other.label, got.label)
        self.assertTrue(ebay_us_proxies.is_resting(got.label))

    def test_waiting_gives_up_after_the_limit(self):
        for label in ("a:1", "b:2", "c:3"):
            cache.set("ebay_us_proxy:rest:" + label, "1", 60)
        with patch.dict(os.environ, {"EBAY_US_PROXY_MAX_WAIT_SEC": "0"}):
            with self.assertRaises(ebay_us_proxies.ProxyPoolBusy):
                ebay_us_proxies.acquire_shared_wait(None)

    def test_waiting_returns_a_proxy_once_one_frees_up(self):
        for label in ("a:1", "b:2", "c:3"):
            cache.set("ebay_us_proxy:rest:" + label, "1", 60)
        calls = {"n": 0}

        def fake_sleep(_sec):
            calls["n"] += 1
            cache.delete("ebay_us_proxy:rest:b:2")

        with patch.object(ebay_us_proxies.time, "sleep", side_effect=fake_sleep):
            got = ebay_us_proxies.acquire_shared_wait(None)
        self.assertEqual(got.label, "b:2")
        self.assertEqual(calls["n"], 1)


@patch.object(ebay_common, "random_delay", lambda *a, **k: None)
@patch.object(ebay_common, "backoff_delay", lambda *a, **k: None)
class NoFreeProxyTests(SimpleTestCase):
    def test_no_free_proxy_leaves_the_listing_after_one_try(self):
        with patch.object(ebay_common.EbayBrowserSession, "warm_and_fetch",
                          return_value=(None, "proxy_exhausted")) as browser:
            out = scrape_ebay_for_market("https://www.ebay.com/itm/1", "USA", {}, market=EBAY_MARKET_US)
        browser.assert_called_once()
        self.assertEqual(out["error_code"], "blocked")
        self.assertEqual(out["error_message"], "proxy_exhausted")

    def test_tries_per_listing_default_to_five(self):
        os.environ.pop("EBAY_US_CHROME_ATTEMPTS", None)
        self.assertEqual(ebay_common.ebay_us_chrome_attempts(), 5)
        with patch.dict(os.environ, {"EBAY_US_CHROME_ATTEMPTS": "2"}):
            self.assertEqual(ebay_common.ebay_us_chrome_attempts(), 2)


class NoFreeProxyCountTests(SimpleTestCase):
    def test_waiting_for_a_proxy_does_not_count_toward_the_fallback(self):
        class PM:
            failed_sync_count = 2
            scrape_error = None
            saved = None

            def save(self, update_fields=None):
                self.saved = update_fields

        pm = PM()
        self.assertTrue(leave_pending_after_block(pm, "blocked", "proxy_exhausted"))
        self.assertEqual(pm.failed_sync_count, 2)
        self.assertEqual(pm.saved, ["scrape_error"])
        self.assertIn("proxy_exhausted", pm.scrape_error)
