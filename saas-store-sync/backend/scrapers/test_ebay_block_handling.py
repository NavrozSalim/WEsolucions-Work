"""eBay bot-wall handling: reason codes, US Chrome-first, no pointless retries."""
from __future__ import annotations

import os
from unittest.mock import patch

from django.test import SimpleTestCase

from scrapers import ebay_common
from scrapers.ebay_common import EBAY_MARKET_US, ebay_result_error_code, scrape_ebay_for_market

_AKAMAI_403 = (
    "<html><head><title>Error Page | eBay</title></head><body>SORRY Something went wrong on our end"
    "</body></html>"
)
_CHALLENGE = "<html><head><title>Pardon Our Interruption...</title></head><body>x</body></html>"


class ErrorCodeTests(SimpleTestCase):
    def test_codes(self):
        self.assertEqual(ebay_result_error_code("not_found"), "not_found")
        self.assertEqual(ebay_result_error_code("http_404"), "not_found")
        self.assertEqual(ebay_result_error_code("http_403"), "blocked")
        self.assertEqual(ebay_result_error_code("browser_challenge"), "challenge")
        self.assertEqual(ebay_result_error_code("browser_blocked"), "blocked")
        self.assertEqual(ebay_result_error_code("selenium_error: boom"), "blocked")
        self.assertEqual(ebay_result_error_code("http_error: timed out"), "blocked")
        self.assertEqual(ebay_result_error_code(""), "no_price")
        self.assertEqual(ebay_result_error_code("unknown_error"), "no_price")

    def test_akamai_error_page_counts_as_blocked(self):
        blocked, _ = ebay_common._is_challenge_or_blocked(_AKAMAI_403)
        self.assertTrue(blocked)


@patch.object(ebay_common, "random_delay", lambda *a, **k: None)
@patch.object(ebay_common, "backoff_delay", lambda *a, **k: None)
class UsBrowserFirstTests(SimpleTestCase):
    def setUp(self):
        saved = {k: os.environ.pop(k) for k in ("EBAY_US_BROWSER_FIRST",) if k in os.environ}
        self.addCleanup(os.environ.update, saved)

    def test_us_skips_plain_http_and_uses_chrome(self):
        with patch.object(ebay_common.EbayHTTP, "fetch") as http, \
                patch.object(ebay_common.EbayBrowserSession, "warm_and_fetch",
                             return_value=("<html>item</html>", "")) as browser, \
                patch.object(ebay_common, "_parse_html_to_result",
                             return_value={"price": 9.99, "stock": 20, "title": "Tuner"}):
            out = scrape_ebay_for_market("https://www.ebay.com/itm/1", "USA", {}, market=EBAY_MARKET_US)
        http.assert_not_called()
        browser.assert_called_once()
        self.assertEqual(out["price"], 9.99)

    def test_us_challenge_retries_with_new_proxy_then_reports_challenge(self):
        with patch.object(ebay_common.EbayHTTP, "fetch") as http, \
                patch.object(ebay_common.EbayBrowserSession, "warm_and_fetch",
                             return_value=(_CHALLENGE, "browser_challenge")) as browser:
            out = scrape_ebay_for_market("https://www.ebay.com/itm/1", "USA", {}, market=EBAY_MARKET_US)
        http.assert_not_called()
        self.assertEqual(browser.call_count, ebay_common.RETRY_LIMIT)
        # Only the first try keeps its proxy; every retry rotates.
        rotates = [c.kwargs["force_proxy_rotate"] for c in browser.call_args_list]
        self.assertEqual(rotates, [False] + [True] * (ebay_common.RETRY_LIMIT - 1))
        self.assertEqual(out["error_code"], "challenge")
        self.assertIsNone(out["title"])

    def test_us_page_with_no_price_is_tried_once(self):
        with patch.object(ebay_common.EbayBrowserSession, "warm_and_fetch",
                          return_value=("<html>no price</html>", "")) as browser, \
                patch.object(ebay_common, "_parse_html_to_result", return_value=None):
            out = scrape_ebay_for_market("https://www.ebay.com/itm/1", "USA", {}, market=EBAY_MARKET_US)
        browser.assert_called_once()
        self.assertEqual(out["error_code"], "no_price")


@patch.object(ebay_common, "random_delay", lambda *a, **k: None)
@patch.object(ebay_common, "backoff_delay", lambda *a, **k: None)
class HttpModeRetryTests(SimpleTestCase):
    """The old HTTP-only path (eBay US with the browser switched off)."""

    def setUp(self):
        patcher = patch.dict(os.environ, {"EBAY_US_BROWSER_FIRST": "0"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_gone_listing_takes_one_request(self):
        with patch.object(ebay_common, "_ebay_http_only_with_proxies", return_value=True), \
                patch.object(ebay_common.EbayHTTP, "fetch",
                             return_value=("<html>gone</html>", 404, "http_404")) as http:
            out = scrape_ebay_for_market("https://www.ebay.com/itm/1", "USA", {}, market=EBAY_MARKET_US)
        self.assertEqual(http.call_count, 1)
        self.assertEqual(out["error_code"], "not_found")

    def test_blocked_listing_still_retries_every_round(self):
        with patch.object(ebay_common, "_ebay_http_only_with_proxies", return_value=True), \
                patch.object(ebay_common.EbayHTTP, "fetch",
                             return_value=(_AKAMAI_403, 403, "http_403")) as http:
            out = scrape_ebay_for_market("https://www.ebay.com/itm/1", "USA", {}, market=EBAY_MARKET_US)
        self.assertGreater(http.call_count, 2)
        self.assertEqual(out["error_code"], "blocked")


class UsChromeProxyTests(SimpleTestCase):
    def setUp(self):
        from scrapers import ebay_us_proxies

        ebay_us_proxies.reset_pool_for_tests()
        self.addCleanup(ebay_us_proxies.reset_pool_for_tests)

    def test_chrome_uses_an_ebay_us_proxy(self):
        with patch.dict(os.environ, {"EBAY_US_PROXY_URLS": "http://u:p@a:1,http://u:p@b:2",
                                     "EBAY_US_MIN_REQUEST_GAP_SEC": "0"}):
            session: dict = {}
            url = ebay_common.EbayDriver._us_proxy_url(session)
        self.assertIn(url, ("http://u:p@a:1", "http://u:p@b:2"))
        self.assertIsNotNone(session.get(ebay_common._ebay_sk(EBAY_MARKET_US, "browser_proxy")))

    def test_proxies_off_uses_server_ip(self):
        with patch.dict(os.environ, {"EBAY_US_PROXY_URLS": "http://u:p@a:1", "EBAY_US_BROWSER_PROXIES": "0"}):
            self.assertIsNone(ebay_common.EbayDriver._us_proxy_url({}))

    def test_saved_us_cookies_load_from_json(self):
        raw = '[{"name": "x", "value": "1", "domain": ".ebay.com", "sameSite": "no_restriction"}, "bad"]'
        with patch.dict(os.environ, {"EBAY_US_COOKIES_JSON": raw}):
            cookies = ebay_common._load_us_saved_cookies()
        self.assertEqual([c["name"] for c in cookies], ["x"])

        class FakeDriver:
            added = []

            def add_cookie(self, c):
                self.added.append(c)

        driver = FakeDriver()
        with patch.dict(os.environ, {"EBAY_US_COOKIES_JSON": raw}):
            ebay_common._add_us_saved_cookies(driver)
        self.assertEqual(driver.added[0]["name"], "x")
        self.assertNotIn("sameSite", driver.added[0])


class AuTitleTests(SimpleTestCase):
    def test_block_page_title_is_never_used(self):
        from scrapers.ebay_au_fast import _AU_LAST_HTML_KEY, _AU_PRODUCT_HTML_KEY
        from scrapers.ebay_au_scraper import _title_from_session_html

        # Even after an earlier product page in the same session.
        for page in (_AKAMAI_403, _CHALLENGE):
            session = {_AU_LAST_HTML_KEY: page, _AU_PRODUCT_HTML_KEY: True}
            self.assertIsNone(_title_from_session_html(session))
        self.assertIsNone(_title_from_session_html({_AU_LAST_HTML_KEY: _AKAMAI_403}))
