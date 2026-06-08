"""Unit tests for Amazon US MAP / buybox price parsing and delivery stock gate."""
import unittest
from datetime import date

from bs4 import BeautifulSoup

from scrapers.amazon_us_scraper import (
    AmazonParser,
    MAX_DELIVERY_DAYS,
    _parse_delivery_days_from_text,
)


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


IN_STOCK_FAST_DELIVERY_HTML = """
<html><body>
<div id="availability"><span>In Stock</span></div>
<div id="mir-layout-DELIVERY_BLOCK-slot-PRIMARY_DELIVERY_MESSAGE_LARGE">
  FREE delivery <span class="a-text-bold">Tomorrow, June 9</span>
</div>
</body></html>
"""

IN_STOCK_SLOW_DELIVERY_HTML = """
<html><body>
<div id="availability"><span>In Stock</span></div>
<div id="mir-layout-DELIVERY_BLOCK-slot-PRIMARY_DELIVERY_MESSAGE_LARGE">
  FREE delivery <span class="a-text-bold">Saturday, June 20</span>
</div>
</body></html>
"""

IN_STOCK_NO_DELIVERY_HTML = """
<html><body>
<div id="availability"><span>In Stock</span></div>
</body></html>
"""


class TestAmazonUSDeliveryStockGate(unittest.TestCase):
    TODAY = date(2026, 6, 8)

    def test_parse_delivery_days_tomorrow(self):
        self.assertEqual(_parse_delivery_days_from_text("Tomorrow, June 9", self.TODAY), 1)

    def test_parse_delivery_days_weekday_date(self):
        self.assertEqual(_parse_delivery_days_from_text("Saturday, June 13", self.TODAY), 5)

    def test_parse_delivery_days_far_date(self):
        self.assertEqual(_parse_delivery_days_from_text("Saturday, June 20", self.TODAY), 12)

    def test_fast_delivery_keeps_in_stock_quantity(self):
        soup = BeautifulSoup(IN_STOCK_FAST_DELIVERY_HTML, "html.parser")
        self.assertEqual(AmazonParser.extract_delivery_days(soup, today=self.TODAY), 1)
        self.assertEqual(AmazonParser.extract_stock(soup, today=self.TODAY), 99)

    def test_slow_delivery_zeroes_stock(self):
        soup = BeautifulSoup(IN_STOCK_SLOW_DELIVERY_HTML, "html.parser")
        self.assertEqual(AmazonParser.extract_delivery_days(soup, today=self.TODAY), 12)
        self.assertGreater(AmazonParser.extract_delivery_days(soup, today=self.TODAY), MAX_DELIVERY_DAYS)
        self.assertEqual(AmazonParser.extract_stock(soup, today=self.TODAY), 0)

    def test_missing_delivery_block_keeps_stock(self):
        soup = BeautifulSoup(IN_STOCK_NO_DELIVERY_HTML, "html.parser")
        self.assertIsNone(AmazonParser.extract_delivery_days(soup, today=self.TODAY))
        self.assertEqual(AmazonParser.extract_stock(soup, today=self.TODAY), 99)


if __name__ == "__main__":
    unittest.main()
