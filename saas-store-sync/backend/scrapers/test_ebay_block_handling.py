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
        with patch.object(ebay_common.EbayHTTP, "fetch", return_value=(None, 403, "http_403")) as http, \
                patch.object(ebay_common.EbayBrowserSession, "warm_and_fetch",
                             return_value=(_CHALLENGE, "browser_challenge")) as browser:
            out = scrape_ebay_for_market("https://www.ebay.com/itm/1", "USA", {}, market=EBAY_MARKET_US)
        # One cookie hand-off per Chrome try; Chrome's error is the one reported.
        self.assertEqual(http.call_count, ebay_common.RETRY_LIMIT)
        self.assertEqual(browser.call_count, ebay_common.RETRY_LIMIT)
        # Only the first try keeps its proxy; every retry rotates.
        rotates = [c.kwargs["force_proxy_rotate"] for c in browser.call_args_list]
        self.assertEqual(rotates, [False] + [True] * (ebay_common.RETRY_LIMIT - 1))
        self.assertEqual(out["error_code"], "challenge")
        self.assertIsNone(out["title"])

    def test_us_page_with_no_price_is_tried_once(self):
        with patch.object(ebay_common.EbayBrowserSession, "warm_and_fetch",
                          return_value=("<html>no price</html>", "")) as browser, \
                patch.object(ebay_common.EbayHTTP, "fetch", return_value=(None, None, "")), \
                patch.object(ebay_common, "_parse_html_to_result", return_value=None):
            out = scrape_ebay_for_market("https://www.ebay.com/itm/1", "USA", {}, market=EBAY_MARKET_US)
        browser.assert_called_once()
        self.assertEqual(out["error_code"], "no_price")

    def test_cookie_handoff_gets_the_price_after_chrome_is_blocked(self):
        with patch.object(ebay_common.EbayHTTP, "fetch",
                          return_value=("<html>item</html>", 200, "")) as http, \
                patch.object(ebay_common.EbayBrowserSession, "warm_and_fetch",
                             return_value=(_AKAMAI_403, "browser_blocked")), \
                patch.object(ebay_common, "_parse_html_to_result",
                             side_effect=lambda h, u: {"price": 9.99, "stock": 20, "title": "T"}
                             if h == "<html>item</html>" else None):
            out = scrape_ebay_for_market("https://www.ebay.com/itm/1", "USA", {}, market=EBAY_MARKET_US)
        http.assert_called_once()
        self.assertEqual(out["price"], 9.99)

    def test_no_handoff_when_switched_off_or_proxy_refused(self):
        for env, err in (({"EBAY_US_COOKIE_HANDOFF": "0"}, "browser_blocked"), ({}, "browser_proxy_error")):
            with patch.dict(os.environ, env), \
                    patch.object(ebay_common.EbayHTTP, "fetch") as http, \
                    patch.object(ebay_common.EbayBrowserSession, "warm_and_fetch", return_value=(None, err)):
                out = scrape_ebay_for_market("https://www.ebay.com/itm/1", "USA", {}, market=EBAY_MARKET_US)
            http.assert_not_called()
            self.assertEqual(out["error_code"], "blocked")

    def test_ebay_ca_is_tried_first_on_every_try(self):
        with patch.object(ebay_common.EbayHTTP, "fetch", return_value=(None, 403, "http_403")), \
                patch.object(ebay_common.EbayBrowserSession, "warm_and_fetch",
                             return_value=(_AKAMAI_403, "browser_blocked")) as browser:
            scrape_ebay_for_market("https://www.ebay.com/itm/182391181967", "USA", {}, market=EBAY_MARKET_US)
        hosts = [c.args[0].split("/")[2] for c in browser.call_args_list]
        self.assertEqual(hosts, ["www.ebay.ca", "www.ebay.com"] * ebay_common.RETRY_LIMIT)

    def test_ca_first_can_be_switched_off(self):
        with patch.dict(os.environ, {"EBAY_US_CA_FIRST": "0"}), \
                patch.object(ebay_common.EbayHTTP, "fetch", return_value=(None, 403, "http_403")), \
                patch.object(ebay_common.EbayBrowserSession, "warm_and_fetch",
                             return_value=(_AKAMAI_403, "browser_blocked")) as browser:
            scrape_ebay_for_market("https://www.ebay.com/itm/182391181967", "USA", {}, market=EBAY_MARKET_US)
        self.assertEqual(browser.call_args_list[0].args[0].split("/")[2], "www.ebay.com")

    def test_chrome_uses_a_random_windows_name_unless_native_is_on(self):
        self.assertFalse(ebay_common.ebay_us_chrome_native_ua())
        with patch.dict(os.environ, {"EBAY_US_CHROME_NATIVE_UA": "1"}):
            self.assertTrue(ebay_common.ebay_us_chrome_native_ua())


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


class UsChromeProxyRefusedTests(SimpleTestCase):
    """The server IP is not on the proxy allowlist: Chrome gets an empty stub."""

    _STUB = "<html><head></head><body></body></html>"

    def setUp(self):
        from scrapers import ebay_us_proxies

        ebay_us_proxies.reset_pool_for_tests()
        self.addCleanup(ebay_us_proxies.reset_pool_for_tests)
        self._reset_switch()
        self.addCleanup(self._reset_switch)

    @staticmethod
    def _reset_switch():
        ebay_common._us_browser_proxy_fails = 0
        ebay_common._us_browser_proxy_off_until = 0.0

    def test_stub_and_chrome_error_pages_count_as_not_loaded(self):
        self.assertTrue(ebay_common._browser_page_not_loaded(self._STUB))
        self.assertTrue(ebay_common._browser_page_not_loaded(
            '<html><body class="neterror">ERR_TUNNEL_CONNECTION_FAILED</body></html>' + "x" * 5000))
        self.assertFalse(ebay_common._browser_page_not_loaded(_AKAMAI_403))
        product = "<html>" + "x" * 8000 + 'data-testid="x-price-primary" err_timed_out</html>'
        self.assertFalse(ebay_common._browser_page_not_loaded(product))
        self.assertEqual(ebay_common.ebay_result_error_code("browser_proxy_error"), "blocked")

    def _fetch_stub(self, session):
        driver = type("D", (), {"page_source": self._STUB, "current_url": "about:blank",
                                "get": lambda self, u: None})()
        with patch.object(ebay_common.EbayDriver, "get_or_create", return_value=driver), \
                patch.object(ebay_common.EbayBrowserSession, "_apply_region_browser_profile"), \
                patch.object(ebay_common.EbayBrowserSession, "_wait_until_product_or_stable_challenge",
                             return_value=self._STUB), \
                patch.object(ebay_common.EbayBrowserSession, "_finalize_bin_price_hydration",
                             return_value=self._STUB), \
                patch.object(ebay_common.EbayHTTP, "import_cookies_from_selenium"), \
                patch.object(ebay_common, "_add_us_saved_cookies"), \
                patch.object(ebay_common.EbayDriver, "close") as close:
            out = ebay_common.EbayBrowserSession.warm_and_fetch(
                "https://www.ebay.com/itm/1", "USA", session, EBAY_MARKET_US)
        return out, close

    def test_refused_proxy_is_reported_and_chrome_closed(self):
        session = {ebay_common._ebay_sk(EBAY_MARKET_US, "selenium_proxy_url"): "http://u:p@a:1"}
        (html, err), close = self._fetch_stub(session)
        self.assertIsNone(html)
        self.assertEqual(err, "browser_proxy_error")
        close.assert_called_once()

    def test_two_refusals_switch_chrome_to_server_ip(self):
        env = {"EBAY_US_PROXY_URLS": "http://u:p@a:1,http://u:p@b:2", "EBAY_US_MIN_REQUEST_GAP_SEC": "0"}
        with patch.dict(os.environ, env):
            self.assertIsNotNone(ebay_common.EbayDriver._us_proxy_url({}))
            for _ in range(ebay_common._US_PROXY_FAILS_BEFORE_OFF):
                session = {ebay_common._ebay_sk(EBAY_MARKET_US, "selenium_proxy_url"): "http://u:p@a:1"}
                self._fetch_stub(session)
            self.assertIsNone(ebay_common.EbayDriver._us_proxy_url({}))

    def test_stub_without_proxy_is_empty_not_proxy_error(self):
        (html, err), _ = self._fetch_stub({})
        self.assertIsNone(html)
        self.assertEqual(err, "empty_browser_html")
        self.assertEqual(ebay_common._us_browser_proxy_fails, 0)


class UsSavedCookieFilesTests(SimpleTestCase):
    def test_ebay_ca_file_next_to_the_us_file_is_loaded_too(self):
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            us = os.path.join(d, "ebay_us_cookies.json")
            with open(us, "w", encoding="utf-8") as f:
                json.dump([{"name": "us", "value": "1", "domain": ".ebay.com"}], f)
            with open(os.path.join(d, "ebay_ca_cookies.json"), "w", encoding="utf-8") as f:
                # The extension's other export format wraps the list.
                json.dump({"url": "https://www.ebay.ca",
                           "cookies": [{"name": "ca", "value": "1", "domain": ".ebay.ca"}]}, f)
            with patch.dict(os.environ, {"EBAY_US_COOKIES_FILE": us}):
                os.environ.pop("EBAY_US_COOKIES_JSON", None)
                names = [c["name"] for c in ebay_common._load_us_saved_cookies()]
        self.assertEqual(names, ["us", "ca"])


class AuTitleTests(SimpleTestCase):
    def test_block_page_title_is_never_used(self):
        from scrapers.ebay_au_fast import _AU_LAST_HTML_KEY, _AU_PRODUCT_HTML_KEY
        from scrapers.ebay_au_scraper import _title_from_session_html

        # Even after an earlier product page in the same session.
        for page in (_AKAMAI_403, _CHALLENGE):
            session = {_AU_LAST_HTML_KEY: page, _AU_PRODUCT_HTML_KEY: True}
            self.assertIsNone(_title_from_session_html(session))
        self.assertIsNone(_title_from_session_html({_AU_LAST_HTML_KEY: _AKAMAI_403}))
