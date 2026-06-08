"""Unit tests for Amazon US MAP / buybox price parsing."""
import unittest

from bs4 import BeautifulSoup

from scrapers.amazon_us_scraper import AmazonParser


MAP_FORM_HTML = """
<html><body>
<div id="desktop_buybox">
  <span class="a-size-base">Price: See price in cart</span>
  <span class="a-price"><span class="a-offscreen">$6.90</span></span>
  <form id="addToCart" action="/gp/product/handle-buy-box/ref=dp_start-bbf_1_glance">
    <input type="hidden" name="items[0.base][customerVisiblePrice][amount]" value="105.61">
    <input type="hidden" name="items[0.base][customerVisiblePrice][currencyCode]" value="USD">
    <input type="hidden" name="items[0.base][customerVisiblePrice][displayString]" value="$105.61">
    <input type="hidden" name="items[0.base][asin]" value="B001LTZZSG">
    <input type="hidden" name="items[0.base][offerListingId]" value="offer123">
  </form>
</div>
<div id="similarities">
  <span class="a-price"><span class="a-offscreen">$6.90</span></span>
</div>
</body></html>
"""


NORMAL_BUYBOX_HTML = """
<html><body>
<div id="desktop_buybox">
  <div id="corePrice_feature_div">
    <span class="a-price"><span class="a-offscreen">$65.00</span></span>
  </div>
</div>
<div id="similarities">
  <span class="a-price"><span class="a-offscreen">$6.90</span></span>
</div>
</body></html>
"""


class TestAmazonUSMapPrice(unittest.TestCase):
    def test_map_page_uses_hidden_customer_visible_price_not_decoy(self):
        soup = BeautifulSoup(MAP_FORM_HTML, "html.parser")
        self.assertTrue(AmazonParser.is_map_price_page(soup, MAP_FORM_HTML))
        price = AmazonParser.extract_price(soup, MAP_FORM_HTML)
        self.assertEqual(price, 105.61)

    def test_normal_page_prefers_buybox_price(self):
        soup = BeautifulSoup(NORMAL_BUYBOX_HTML, "html.parser")
        self.assertFalse(AmazonParser.is_map_price_page(soup, NORMAL_BUYBOX_HTML))
        price = AmazonParser.extract_price(soup, NORMAL_BUYBOX_HTML)
        self.assertEqual(price, 65.0)

    def test_extract_buybox_form_price_standalone(self):
        soup = BeautifulSoup(MAP_FORM_HTML, "html.parser")
        self.assertEqual(AmazonParser.extract_buybox_form_price(soup), 105.61)

    def test_map_detection_finds_phrase_late_in_html(self):
        soup = BeautifulSoup("<html><body><div id='desktop_buybox'></div></body></html>", "html.parser")
        late_html = ("x" * 400_000) + "see price in cart"
        self.assertTrue(AmazonParser.is_map_price_page(soup, late_html))


if __name__ == "__main__":
    unittest.main()
