"""Amazon AU OOS error-selector stock rules."""
import os
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from bs4 import BeautifulSoup

from scrapers.amazon_au_scraper import (
    AU_OOS_ERROR_SELECTOR,
    amazon_au_postcode,
    apply_au_error_stock,
    asin_from_amazon_page,
    au_asin_mismatch_result,
)
from scrapers.amazon_us_scraper import AmazonParser


class AmazonAUErrorStockTests(SimpleTestCase):
    def test_spacing_base_error_forces_stock_zero(self):
        html = """
        <div class="a-spacing-base">
          <span class="a-color-error">Temporarily out of stock.</span>
        </div>
        <span class="a-price"><span class="a-offscreen">$19.99</span></span>
        """
        soup = BeautifulSoup(html, "html.parser")
        self.assertIsNotNone(soup.select_one(AU_OOS_ERROR_SELECTOR))
        self.assertEqual(apply_au_error_stock(soup, 99), 0)
        self.assertEqual(apply_au_error_stock(soup, None), 0)
        self.assertEqual(apply_au_error_stock(soup, 2), 0)

    def test_shipping_coverage_error_forces_stock_zero_even_with_only_n_left(self):
        html = """
        <div id="availability">
          <span class="a-size-base a-color-price a-text-bold">Only 5 left in stock.</span>
        </div>
        <span class="a-color-error">
          Sorry, your selected delivery location is beyond seller's shipping coverage
          for this item. Please choose a different delivery location or purchase from
          another seller.
          <a href="/gp/help/customer/display.html?nodeId=GCRPS8WF593X96XT"> Learn more</a>
        </span>
        <span class="a-price"><span class="a-offscreen">$9.74</span></span>
        """
        soup = BeautifulSoup(html, "html.parser")
        self.assertEqual(apply_au_error_stock(soup, 5), 0)

    def test_only_n_left_color_error_keeps_stock(self):
        html = """
        <span class="a-color-error">Only 5 left in stock.</span>
        <span class="a-price"><span class="a-offscreen">$9.74</span></span>
        """
        soup = BeautifulSoup(html, "html.parser")
        self.assertEqual(apply_au_error_stock(soup, 5), 5)

    def test_import_disclaimer_forces_stock_zero_even_when_in_stock(self):
        html = """
        <div id="availability"><span>In stock</span></div>
        <div id="amazonGlobal_feature_div">
          <span class="a-size-base">
            US imports may differ from local products.
            <a href="/gp/help/customer/display.html">Additional terms</a>
          </span>
        </div>
        <span class="a-price"><span class="a-offscreen">$28.27</span></span>
        """
        soup = BeautifulSoup(html, "html.parser")
        self.assertEqual(apply_au_error_stock(soup, 99), 0)
        self.assertEqual(apply_au_error_stock(soup, 3), 0)

    def test_other_country_import_disclaimer_forces_stock_zero(self):
        html = """
        <div id="availability"><span>In stock</span></div>
        <span>UK imports may differ from local products. Additional terms</span>
        """
        soup = BeautifulSoup(html, "html.parser")
        self.assertEqual(apply_au_error_stock(soup, 2), 0)

    def test_no_error_selector_keeps_stock(self):
        html = """
        <div id="availability"><span>In Stock</span></div>
        <span class="a-price"><span class="a-offscreen">$19.99</span></span>
        """
        soup = BeautifulSoup(html, "html.parser")
        self.assertEqual(apply_au_error_stock(soup, 99), 99)
        self.assertIsNone(apply_au_error_stock(soup, None))


class AmazonAUPriceMarkupTests(SimpleTestCase):
    def test_b00000jrrd_style_page_is_34_06_not_1399(self):
        html = """
        <html><body>
          <div id="buybox">
            <div id="corePrice_feature_div">
              <span class="a-price aok-align-center apex-pricetopay-value">
                <span class="a-offscreen">$34.06</span>
                <span aria-hidden="true">
                  <span class="a-price-symbol">$</span>
                  <span class="a-price-whole">34<span class="a-price-decimal">.</span></span>
                  <span class="a-price-fraction">06</span>
                </span>
              </span>
            </div>
            <span class="a-price priceToPay apex-pricetopay-value">
              <span class="a-offscreen"> </span>
            </span>
            <div id="availability"><span>In stock</span></div>
          </div>
          <div id="similarities">
            <span class="a-price">
              <span class="a-offscreen"></span>
              <span class="a-price-whole">13<span class="a-price-fraction">99</span></span>
            </span>
          </div>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        price = AmazonParser.extract_price(soup, html, market="AU")
        self.assertEqual(price, 34.06)
        self.assertNotEqual(price, 1399.0)
        self.assertEqual(AmazonParser.extract_stock(soup), 99)


class AmazonAUPostcodeTests(SimpleTestCase):
    def test_default_postcode_is_2762(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AMAZON_AU_POSTCODE", None)
            self.assertEqual(amazon_au_postcode(), "2762")

    def test_env_override(self):
        with patch.dict(os.environ, {"AMAZON_AU_POSTCODE": "2000"}):
            self.assertEqual(amazon_au_postcode(), "2000")

    def test_invalid_falls_back(self):
        with patch.dict(os.environ, {"AMAZON_AU_POSTCODE": "abc"}):
            self.assertEqual(amazon_au_postcode(), "2762")


class AmazonAUProxyUrlTests(SimpleTestCase):
    def test_amazon_list_wins_over_shared(self):
        from scrapers.amazon_au_proxies import load_amazon_au_proxy_urls

        urls = load_amazon_au_proxy_urls(env={
            "AMAZON_AU_PROXY_URLS": "http://u:p@amz:1",
            "COSTCO_AU_PROXY_URLS": "http://u:p@costco:2",
        })
        self.assertEqual(urls, ["http://u:p@amz:1"])

    def test_falls_back_to_costco_list(self):
        from scrapers.amazon_au_proxies import load_amazon_au_proxy_urls

        urls = load_amazon_au_proxy_urls(env={"COSTCO_AU_PROXY_URLS": "http://u:p@costco:2"})
        self.assertEqual(urls, ["http://u:p@costco:2"])


class AmazonAUHttpRetryTests(SimpleTestCase):
    def setUp(self):
        from scrapers import amazon_au_proxies
        amazon_au_proxies.reset_pool_for_tests()
        self.addCleanup(amazon_au_proxies.reset_pool_for_tests)

    def test_failed_postcode_is_not_retried_for_every_product(self):
        from scrapers.amazon_au_scraper import AmazonAUHTTP

        session = {}
        s = MagicMock()
        s.post.side_effect = Exception("boom")
        AmazonAUHTTP._ensure_zip(s, "https://www.amazon.com.au/dp/B000000001", session, True)
        AmazonAUHTTP._ensure_zip(s, "https://www.amazon.com.au/dp/B000000002", session, True)
        self.assertEqual(s.post.call_count, 1)

    def test_block_rotates_proxy_without_backoff(self):
        from scrapers import amazon_au_scraper
        from scrapers.core import ScrapeResult

        env = {"AMAZON_AU_PROXY_URLS": "http://u:p@a:1,http://u:p@b:2"}
        results = [
            ScrapeResult.fail("blocked_captcha", "x", "", "amazon_au", "u"),
            ScrapeResult.ok(price=10.0, stock=2, title="t"),
        ]
        with patch.dict(os.environ, env), \
                patch.object(amazon_au_scraper.AmazonAUHTTP, "fetch", side_effect=results), \
                patch.object(amazon_au_scraper.AmazonAUHTTP, "_get_session") as get_session, \
                patch.object(amazon_au_scraper, "backoff_delay") as backoff:
            out = amazon_au_scraper.AmazonAUHTTP.scrape_with_retry(
                "https://www.amazon.com.au/dp/B000000001", {}, True,
            )
        self.assertTrue(out.success)
        backoff.assert_not_called()
        get_session.assert_called_once_with({}, True, rotate=True)


class AmazonAUAsinMismatchTests(SimpleTestCase):
    def test_page_asin_is_read_from_item_details(self):
        html = """
        <table>
          <tr><th>ASIN</th><td>B0C9ZTJTMQ</td></tr>
        </table>
        """
        soup = BeautifulSoup(html, "html.parser")
        self.assertEqual(asin_from_amazon_page(soup), "B0C9ZTJTMQ")

    def test_different_page_asin_fails_without_a_price(self):
        html = """
        <span class="a-price"><span class="a-offscreen">$49.99</span></span>
        <tr><th class="a-color-secondary">ASIN</th><td>B0C9ZTJTMQ</td></tr>
        """
        soup = BeautifulSoup(html, "html.parser")
        result = au_asin_mismatch_result(
            "https://www.amazon.com.au/dp/B08QF7YH9G",
            soup,
        )
        self.assertIsNotNone(result)
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "asin_mismatch")
        self.assertIsNone(result.price)
        self.assertIsNone(result.stock)
        self.assertIn("B0C9ZTJTMQ", result.error_message)
        self.assertIn("B08QF7YH9G", result.error_message)

    def test_matching_asin_is_not_a_failure(self):
        html = "<tr><th>ASIN</th><td>B08QF7YH9G</td></tr>"
        soup = BeautifulSoup(html, "html.parser")
        self.assertIsNone(au_asin_mismatch_result(
            "https://www.amazon.com.au/dp/B08QF7YH9G",
            soup,
        ))

    def test_redirected_url_fails_when_the_page_has_no_asin_row(self):
        soup = BeautifulSoup(
            "<span class='a-price'><span class='a-offscreen'>$4.70</span></span>",
            "html.parser",
        )
        result = au_asin_mismatch_result(
            "https://www.amazon.com.au/dp/B0C9ZQC2FT",
            soup,
            "https://www.amazon.com.au/dp/B0C9ZTJTMQ",
        )
        self.assertEqual(result.error_code, "asin_mismatch")
        self.assertIsNone(result.price)

    def test_same_landed_url_without_an_asin_row_is_not_a_failure(self):
        soup = BeautifulSoup("<span>In stock</span>", "html.parser")
        self.assertIsNone(au_asin_mismatch_result(
            "https://www.amazon.com.au/dp/B0C9ZQC2FT",
            soup,
            "https://www.amazon.com.au/dp/B0C9ZQC2FT",
        ))
