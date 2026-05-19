"""Unit tests for the Costco AU server scraper.

Covers:

* HTML parsing on every shape produced by the desktop runner (price selectors,
  sale + normal, JSON-LD fallback, inventory states).
* Cloudflare / challenge / homepage-redirect detection.
* Proxy pool normalization, sticky assignment, rotation, cooldown.
* Dispatcher integration (``scrapers.get_price_and_stock`` -> Costco scraper).
* ``_is_ingest_only_product`` toggles between desktop ingest and server scrape
  based on ``COSTCO_AU_PROXY_URLS``.

The scraper module is fully importable without proxies / network access; tests
mock out the HTTP layer.
"""
from __future__ import annotations

import threading
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from scrapers import costco_au_proxies, costco_au_scraper


# ============================================================================
# Proxy pool
# ============================================================================

class LoadProxyUrlsTests(SimpleTestCase):
    """``load_proxy_urls`` respects precedence and normalises every form."""

    def test_returns_empty_list_when_nothing_configured(self):
        self.assertEqual(costco_au_proxies.load_proxy_urls(env={}), [])

    def test_costco_specific_takes_precedence_over_generic(self):
        urls = costco_au_proxies.load_proxy_urls(env={
            "COSTCO_AU_PROXY_URLS": "http://u:p@a:1, http://u:p@b:2",
            "PROXY_URLS": "http://x:y@other:9",
        })
        self.assertEqual(urls, ["http://u:p@a:1", "http://u:p@b:2"])

    def test_falls_back_to_proxy_urls_when_no_costco_specific(self):
        urls = costco_au_proxies.load_proxy_urls(env={
            "PROXY_URLS": "http://u:p@a:1\nhttp://u:p@b:2",
        })
        self.assertEqual(urls, ["http://u:p@a:1", "http://u:p@b:2"])

    def test_single_url_fallback(self):
        urls = costco_au_proxies.load_proxy_urls(env={"COSTCO_AU_PROXY_URL": "http://u:p@a:1"})
        self.assertEqual(urls, ["http://u:p@a:1"])

    def test_proxy_endpoints_combines_with_user_pass(self):
        urls = costco_au_proxies.load_proxy_urls(env={
            "PROXY_ENDPOINTS": "a:1,b:2",
            "PROXY_USER": "alice",
            "PROXY_PASS": "secret/!",
            "PROXY_SCHEME": "http",
        })
        # secret/! gets URL-encoded so urlparse can read it back later.
        self.assertEqual(urls, [
            "http://alice:secret%2F%21@a:1",
            "http://alice:secret%2F%21@b:2",
        ])

    def test_deduplicates_proxies(self):
        urls = costco_au_proxies.load_proxy_urls(env={
            "COSTCO_AU_PROXY_URLS": "http://u:p@a:1,http://u:p@a:1,http://u:p@b:2",
        })
        self.assertEqual(urls, ["http://u:p@a:1", "http://u:p@b:2"])

    def test_drops_malformed_entries(self):
        urls = costco_au_proxies.load_proxy_urls(env={
            "COSTCO_AU_PROXY_URLS": "not-a-url, http://good:1, just-text, http://u:p@c:3",
        })
        # 'not-a-url' has no colon→port; 'just-text' likewise. Drop them.
        self.assertEqual(urls, ["http://good:1", "http://u:p@c:3"])


class CostcoAuProxyPoolTests(SimpleTestCase):
    """Pool behavior: sticky assignment per thread + cooldown rotation."""

    def setUp(self):
        self.urls = [
            "http://u:p@a:1",
            "http://u:p@b:2",
            "http://u:p@c:3",
        ]
        self.pool = costco_au_proxies.CostcoAuProxyPool(self.urls, min_gap_sec=0.0)

    def test_acquire_returns_first_proxy(self):
        a = self.pool.acquire()
        self.assertIsNotNone(a)
        self.assertEqual(a.url, self.urls[0])
        self.assertEqual(a.label, "a:1")

    def test_acquire_is_sticky_for_same_thread(self):
        a1 = self.pool.acquire()
        a2 = self.pool.acquire()
        self.assertEqual(a1.url, a2.url)
        self.assertEqual(a1.index, a2.index)

    def test_force_rotate_advances_proxy(self):
        a1 = self.pool.acquire()
        a2 = self.pool.acquire(force_rotate=True)
        self.assertNotEqual(a1.url, a2.url)

    def test_mark_blocked_skips_in_cooldown(self):
        a1 = self.pool.acquire()
        self.pool.mark_blocked(a1, cooldown_sec=600.0)
        a2 = self.pool.acquire(force_rotate=True)
        self.assertNotEqual(a2.index, a1.index)

    def test_returns_none_when_pool_empty(self):
        pool = costco_au_proxies.CostcoAuProxyPool([], min_gap_sec=0.0)
        self.assertIsNone(pool.acquire())

    def test_round_robin_when_all_in_cooldown(self):
        # Mark every proxy blocked; acquire should still return *some* proxy.
        for _ in range(len(self.urls)):
            a = self.pool.acquire(force_rotate=True)
            self.pool.mark_blocked(a, cooldown_sec=600.0)
        a = self.pool.acquire(force_rotate=True)
        self.assertIsNotNone(a)

    def test_threads_get_independent_assignments(self):
        results: dict[int, str] = {}

        def grab(tid_key: int):
            results[tid_key] = self.pool.acquire().url

        t1 = threading.Thread(target=grab, args=(1,))
        t2 = threading.Thread(target=grab, args=(2,))
        t1.start(); t2.start(); t1.join(); t2.join()
        # Each thread should pick from the pool; they may pick the same first
        # proxy (cursor-based round robin), but the per-thread sticky dict has
        # two entries.
        self.assertEqual(len(results), 2)


class GetPoolTests(SimpleTestCase):
    """``get_pool`` caches the module singleton."""

    def setUp(self):
        costco_au_proxies.reset_pool_for_tests()

    def tearDown(self):
        costco_au_proxies.reset_pool_for_tests()

    def test_returns_none_when_no_proxies(self):
        with patch.dict("os.environ", {}, clear=False):
            for k in ("COSTCO_AU_PROXY_URLS", "PROXY_URLS",
                      "COSTCO_AU_PROXY_URL", "PROXY_URL", "PROXY_ENDPOINTS"):
                # Clear any value that the host may have set.
                import os
                os.environ.pop(k, None)
            self.assertIsNone(costco_au_proxies.get_pool())

    def test_singleton(self):
        with patch.dict("os.environ", {"COSTCO_AU_PROXY_URLS": "http://u:p@a:1"}, clear=False):
            p1 = costco_au_proxies.get_pool()
            p2 = costco_au_proxies.get_pool()
            self.assertIsNotNone(p1)
            self.assertIs(p1, p2)


# ============================================================================
# HTML parsing (challenge detection + product extraction)
# ============================================================================

_PDP_HTML_NORMAL = """
<html><head><title>Premium Wireless Vacuum | Costco Australia</title></head>
<body>
  <h1>Premium Wireless Vacuum</h1>
  <sip-add-to-cart-form>
    <button data-cy="addtocart-button-173734" class="btn btn-primary">Add to cart</button>
  </sip-add-to-cart-form>
  <div class="price-original"><span class="notranslate">$1,299.99</span></div>
</body></html>
"""

_PDP_HTML_SALE = """
<html><head><title>Premium Wireless Vacuum | Costco Australia</title></head>
<body>
  <h1>Premium Wireless Vacuum</h1>
  <sip-add-to-cart-form>
    <button data-cy="addtocart-button-173734" class="btn btn-primary">Add to cart</button>
  </sip-add-to-cart-form>
  <div class="price-original"><span class="notranslate">$1,299.99</span></div>
  <span class="you-pay-value">$999.00</span>
</body></html>
"""

_PDP_HTML_JSONLD = """
<html><head><title>Some Product | Costco Australia</title></head>
<body>
  <h1>Some Product</h1>
  <sip-add-to-cart-form>
    <button data-cy="addtocart-button-100200" class="btn btn-primary">Add to cart</button>
  </sip-add-to-cart-form>
  <script type="application/ld+json">
    {"@type":"Product","name":"Some Product","offers":{"@type":"Offer","price":"49.95","priceCurrency":"AUD"}}
  </script>
</body></html>
"""

_PDP_HTML_OUT_OF_STOCK = """
<html><head><title>OOS Item | Costco Australia</title></head>
<body>
  <h1>OOS Item</h1>
  <sip-add-to-cart-form>
    <button data-cy="addtocart-button-173999" class="btn btn-block btn-primary disabled outOfStock" disabled>
      Out of Stock
    </button>
  </sip-add-to-cart-form>
  <div class="price-original"><span class="notranslate">$199.00</span></div>
</body></html>
"""

_PDP_HTML_NO_PRICE = """
<html><head><title>Product Without Price | Costco Australia</title></head>
<body>
  <h1>Product Without Price</h1>
  <sip-add-to-cart-form>
    <button data-cy="addtocart-button-444555" class="btn btn-primary">Add to cart</button>
  </sip-add-to-cart-form>
</body></html>
"""

_HOMEPAGE_HTML = """
<html><head>
  <title>Member warehouse for bulk buys at low prices | Costco AUS</title>
  <link rel="canonical" href="https://www.costco.com.au/" />
</head><body>
  <nav>Welcome to Costco AU</nav>
</body></html>
"""

_CLOUDFLARE_BLOCK_HTML = """
<html><head><title>Just a moment...</title></head>
<body>
  <div id="challenge-form">Checking your browser</div>
  <script>window._cf_chl_opt = {};</script>
</body></html>
"""


class HtmlChallengeDetectionTests(SimpleTestCase):
    def test_real_pdp_is_not_challenged(self):
        challenged, _ = costco_au_scraper.html_is_challenge(_PDP_HTML_NORMAL)
        self.assertFalse(challenged)

    def test_cloudflare_block_detected(self):
        challenged, reason = costco_au_scraper.html_is_challenge(_CLOUDFLARE_BLOCK_HTML)
        self.assertTrue(challenged)
        self.assertEqual(reason, "cloudflare")

    def test_empty_response_is_challenge(self):
        challenged, reason = costco_au_scraper.html_is_challenge("")
        self.assertTrue(challenged)
        self.assertEqual(reason, "empty_response")

    def test_truncated_response_is_challenge(self):
        challenged, reason = costco_au_scraper.html_is_challenge("garbage")
        self.assertTrue(challenged)
        self.assertEqual(reason, "truncated")


class ParseCostcoPdpTests(SimpleTestCase):
    """End-to-end parse from HTML payload to ScrapeResult.to_legacy()."""

    URL = "https://www.costco.com.au/p/173734"

    def test_normal_price_no_sale(self):
        result = costco_au_scraper.parse_costco_pdp(self.URL, _PDP_HTML_NORMAL)
        self.assertTrue(result.success)
        self.assertEqual(result.price, 1299.99)
        self.assertEqual(result.stock, 3)
        self.assertIn("Vacuum", result.title or "")

    def test_sale_price_overrides_normal(self):
        result = costco_au_scraper.parse_costco_pdp(self.URL, _PDP_HTML_SALE)
        self.assertTrue(result.success)
        self.assertEqual(result.price, 999.00)
        self.assertEqual(result.stock, 3)

    def test_jsonld_fallback_price(self):
        url = "https://www.costco.com.au/p/100200"
        result = costco_au_scraper.parse_costco_pdp(url, _PDP_HTML_JSONLD)
        self.assertTrue(result.success)
        self.assertEqual(result.price, 49.95)
        self.assertEqual(result.stock, 3)

    def test_out_of_stock_button(self):
        url = "https://www.costco.com.au/p/173999"
        result = costco_au_scraper.parse_costco_pdp(url, _PDP_HTML_OUT_OF_STOCK)
        self.assertTrue(result.success)
        self.assertEqual(result.stock, 0)
        self.assertEqual(result.price, 199.00)

    def test_no_price_returns_fail(self):
        url = "https://www.costco.com.au/p/444555"
        result = costco_au_scraper.parse_costco_pdp(url, _PDP_HTML_NO_PRICE)
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "no_price")

    def test_homepage_redirect_returns_product_not_found(self):
        result = costco_au_scraper.parse_costco_pdp(self.URL, _HOMEPAGE_HTML)
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "product_not_found")

    def test_blocked_html_returns_blocked_error(self):
        result = costco_au_scraper.parse_costco_pdp(self.URL, _CLOUDFLARE_BLOCK_HTML)
        self.assertFalse(result.success)
        self.assertTrue(result.error_code.startswith("blocked_"))

    def test_product_id_extraction(self):
        self.assertEqual(
            costco_au_scraper.product_id_from_url("https://www.costco.com.au/p/173734"),
            "173734",
        )
        self.assertEqual(
            costco_au_scraper.product_id_from_url("https://www.costco.com.au/p/173734/foo"),
            "173734",
        )
        self.assertIsNone(
            costco_au_scraper.product_id_from_url("https://www.costco.com.au/category/x"),
        )


# ============================================================================
# scrape_costco_au orchestration
# ============================================================================

class ScrapeCostcoAuOrchestrationTests(SimpleTestCase):

    def setUp(self):
        costco_au_proxies.reset_pool_for_tests()
        self.pool = costco_au_proxies.CostcoAuProxyPool(
            ["http://u:p@a:1", "http://u:p@b:2"], min_gap_sec=0.0,
        )
        self.url = "https://www.costco.com.au/p/173734"

    def tearDown(self):
        costco_au_proxies.reset_pool_for_tests()

    def test_returns_ingest_only_payload_when_no_pool(self):
        empty_pool = costco_au_proxies.CostcoAuProxyPool([], min_gap_sec=0.0)
        result = costco_au_scraper.scrape_costco_au(
            self.url, "AU", session={}, pool=empty_pool,
        )
        self.assertIsNone(result.get("price"))
        self.assertEqual(result.get("error_code"), "costco_no_proxy")

    def test_successful_http_first_path(self):
        with patch.object(
            costco_au_scraper, "_http_fetch",
            return_value=(_PDP_HTML_NORMAL, self.url, ""),
        ) as mock_fetch:
            result = costco_au_scraper.scrape_costco_au(
                self.url, "AU", session={}, pool=self.pool,
            )
        mock_fetch.assert_called_once()
        self.assertEqual(result["price"], 1299.99)
        self.assertEqual(result["stock"], 3)
        self.assertIn("Vacuum", result["title"])

    def test_rotates_proxy_on_block(self):
        calls = []

        def fake_fetch(url, session, assignment):
            calls.append(assignment.index)
            if assignment.index == 0:
                return _CLOUDFLARE_BLOCK_HTML, url, ""
            return _PDP_HTML_NORMAL, url, ""

        with patch.object(costco_au_scraper, "_http_fetch", side_effect=fake_fetch):
            result = costco_au_scraper.scrape_costco_au(
                self.url, "AU", session={}, pool=self.pool,
            )

        self.assertEqual(result["price"], 1299.99)
        self.assertEqual(calls, [0, 1])

    def test_product_not_found_does_not_rotate(self):
        calls = []

        def fake_fetch(url, session, assignment):
            calls.append(assignment.index)
            return _HOMEPAGE_HTML, url, ""

        with patch.object(costco_au_scraper, "_http_fetch", side_effect=fake_fetch):
            result = costco_au_scraper.scrape_costco_au(
                self.url, "AU", session={}, pool=self.pool,
            )

        self.assertIsNone(result["price"])
        self.assertEqual(result["error_code"], "product_not_found")
        self.assertEqual(len(calls), 1, "product_not_found is terminal — must not rotate")

    def test_http_error_rotates_through_pool(self):
        calls = []

        def fake_fetch(url, session, assignment):
            calls.append(assignment.index)
            return "", "", "request_error: ConnectionError: refused"

        with patch.object(costco_au_scraper, "_http_fetch", side_effect=fake_fetch):
            result = costco_au_scraper.scrape_costco_au(
                self.url, "AU", session={}, pool=self.pool,
            )

        self.assertIsNone(result["price"])
        self.assertTrue(result["error_code"].startswith("request_error"))
        # 2 proxies + HTTP_RETRIES default 2 → expect 2-3 attempts; both proxies tried.
        self.assertGreaterEqual(len(calls), 2)
        self.assertIn(0, calls)
        self.assertIn(1, calls)


# ============================================================================
# Dispatcher integration
# ============================================================================

class DispatcherRoutingTests(SimpleTestCase):
    """``scrapers.get_price_and_stock`` routes costco.com.au correctly."""

    def setUp(self):
        costco_au_proxies.reset_pool_for_tests()

    def tearDown(self):
        costco_au_proxies.reset_pool_for_tests()

    def test_costco_url_returns_ingest_only_when_no_proxies(self):
        import os
        with patch.dict("os.environ", {}, clear=False):
            for k in ("COSTCO_AU_PROXY_URLS", "PROXY_URLS",
                      "COSTCO_AU_PROXY_URL", "PROXY_URL", "PROXY_ENDPOINTS"):
                os.environ.pop(k, None)

            from scrapers import get_price_and_stock
            result = get_price_and_stock("https://www.costco.com.au/p/173734", "AU", {})
            self.assertEqual(result.get("error_code"), "costco_ingest_only")
            self.assertIsNone(result.get("price"))

    def test_costco_url_routes_to_scraper_when_proxies_configured(self):
        env = {"COSTCO_AU_PROXY_URLS": "http://u:p@a:1"}
        with patch.dict("os.environ", env, clear=False):
            with patch.object(
                costco_au_scraper, "_http_fetch",
                return_value=(_PDP_HTML_NORMAL, "https://www.costco.com.au/p/173734", ""),
            ):
                from scrapers import get_price_and_stock
                result = get_price_and_stock(
                    "https://www.costco.com.au/p/173734", "AU", {},
                )
        self.assertEqual(result["price"], 1299.99)
        self.assertEqual(result["inventory"], 3)


# ============================================================================
# Catalog routing — ingest-only flips off when proxies configured
# ============================================================================

class IngestOnlyToggleTests(SimpleTestCase):
    """``catalog.tasks._is_ingest_only_product`` honors COSTCO_AU_PROXY_URLS."""

    def setUp(self):
        costco_au_proxies.reset_pool_for_tests()

    def tearDown(self):
        costco_au_proxies.reset_pool_for_tests()

    def test_costco_is_ingest_only_without_proxies(self):
        import os
        for k in ("COSTCO_AU_PROXY_URLS", "PROXY_URLS",
                  "COSTCO_AU_PROXY_URL", "PROXY_URL", "PROXY_ENDPOINTS"):
            os.environ.pop(k, None)
        from catalog.tasks import _is_ingest_only_product

        class FakeVendor:
            code = "costcoau"

        class FakeProduct:
            vendor = FakeVendor()

        self.assertTrue(_is_ingest_only_product(FakeProduct()))

    def test_costco_is_live_when_proxies_set(self):
        with patch.dict("os.environ", {"COSTCO_AU_PROXY_URLS": "http://u:p@a:1"}, clear=False):
            from catalog.tasks import _is_ingest_only_product

            class FakeVendor:
                code = "costcoau"

            class FakeProduct:
                vendor = FakeVendor()

            self.assertFalse(_is_ingest_only_product(FakeProduct()))

    def test_heb_and_vevor_remain_ingest_only_regardless(self):
        with patch.dict("os.environ", {"COSTCO_AU_PROXY_URLS": "http://u:p@a:1"}, clear=False):
            from catalog.tasks import _is_ingest_only_product

            class FakeVendor:
                def __init__(self, code):
                    self.code = code

            class FakeProduct:
                def __init__(self, code):
                    self.vendor = FakeVendor(code)

            self.assertTrue(_is_ingest_only_product(FakeProduct("heb")))
            self.assertTrue(_is_ingest_only_product(FakeProduct("vevorau")))
