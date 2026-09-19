"""Unit tests for Amazon US MAP / buybox price parsing and delivery stock gate."""
import unittest
from datetime import date

from bs4 import BeautifulSoup

from scrapers.amazon_us_scraper import (
    AmazonParser,
    MAX_DELIVERY_DAYS,
    _parse_delivery_days_from_text,
    _price_from_amazon_node,
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


AU_NEW_MARKUP_HTML = """
<html><body>
<div id="buybox">
  <div id="desktop_buybox">
    <div id="corePrice_feature_div">
      <span class="a-price aok-align-center apex-pricetopay-value" data-a-size="xl">
        <span class="a-offscreen">$34.06</span>
        <span aria-hidden="true">
          <span class="a-price-symbol">$</span>
          <span class="a-price-whole">34<span class="a-price-decimal">.</span></span>
          <span class="a-price-fraction">06</span>
        </span>
      </span>
    </div>
    <span class="a-price aok-align-center reinventPricePriceToPayMargin priceToPay apex-pricetopay-value">
      <span class="a-offscreen"> </span>
      <span aria-hidden="true">
        <span class="a-price-symbol">$</span>
        <span class="a-price-whole">34<span class="a-price-decimal">.</span></span>
        <span class="a-price-fraction">06</span>
      </span>
    </span>
    <div id="availability"><span>In stock</span></div>
  </div>
</div>
<div id="similarities">
  <span class="a-price">
    <span class="a-offscreen">$13.99</span>
    <span aria-hidden="true">
      <span class="a-price-symbol">$</span>
      <span class="a-price-whole">13<span class="a-price-decimal">.</span></span>
      <span class="a-price-fraction">99</span>
    </span>
  </span>
</div>
</body></html>
"""

AU_EMPTY_OFFSCREEN_NO_FORM_HTML = """
<html><body>
<div id="buybox">
  <div id="corePrice_feature_div">
    <span class="a-price aok-align-center reinventPricePriceToPayMargin priceToPay apex-pricetopay-value">
      <span class="a-offscreen"> </span>
      <span aria-hidden="true">
        <span class="a-price-symbol">$</span>
        <span class="a-price-whole">34<span class="a-price-decimal">.</span></span>
        <span class="a-price-fraction">06</span>
      </span>
    </span>
  </div>
</div>
<div id="similarities">
  <span class="a-price">
    <span class="a-offscreen"></span>
    <span aria-hidden="true">
      <span class="a-price-symbol">$</span>
      <span class="a-price-whole">13<span class="a-price-fraction">99</span></span>
    </span>
  </span>
</div>
</body></html>
"""

AU_AUD_PREFIX_HTML = """
<html><body>
<div id="desktop_buybox">
  <div id="corePrice_feature_div">
    <span class="a-price apex-pricetopay-value">
      <span class="a-offscreen">AUD$34.06</span>
      <span aria-hidden="true">
        <span class="a-price-whole">34<span class="a-price-decimal">.</span></span>
        <span class="a-price-fraction">06</span>
      </span>
    </span>
  </div>
</div>
</body></html>
"""

RELATED_ONLY_NESTED_FRACTION_HTML = """
<html><body>
<span class="a-price">
  <span class="a-offscreen"></span>
  <span aria-hidden="true">
    <span class="a-price-symbol">$</span>
    <span class="a-price-whole">13<span class="a-price-fraction">99</span></span>
  </span>
</span>
</body></html>
"""


class TestAmazonAUPriceMarkup(unittest.TestCase):
    def test_new_apex_markup_prefers_buybox_not_related_13_99(self):
        soup = BeautifulSoup(AU_NEW_MARKUP_HTML, "html.parser")
        self.assertEqual(AmazonParser.extract_price(soup, AU_NEW_MARKUP_HTML, market="AU"), 34.06)

    def test_empty_pricetopay_offscreen_rebuilds_whole_and_fraction(self):
        soup = BeautifulSoup(AU_EMPTY_OFFSCREEN_NO_FORM_HTML, "html.parser")
        price = AmazonParser.extract_price(soup, AU_EMPTY_OFFSCREEN_NO_FORM_HTML, market="AU")
        self.assertEqual(price, 34.06)
        self.assertNotEqual(price, 1399.0)

    def test_nested_fraction_in_whole_is_13_99_not_1399(self):
        soup = BeautifulSoup(RELATED_ONLY_NESTED_FRACTION_HTML, "html.parser")
        node = soup.select_one("span.a-price")
        self.assertEqual(_price_from_amazon_node(node, allow_aud=True), 13.99)

    def test_related_only_page_is_not_used_as_buybox_price(self):
        soup = BeautifulSoup(RELATED_ONLY_NESTED_FRACTION_HTML, "html.parser")
        self.assertIsNone(
            AmazonParser.extract_price(soup, RELATED_ONLY_NESTED_FRACTION_HTML, market="AU")
        )

    def test_related_100_89_in_rightcol_is_not_10089(self):
        html = """
        <html><body>
          <div id="buybox">
            <div id="corePrice_feature_div">
              <span class="a-price apex-pricetopay-value">
                <span class="a-offscreen">$55.24</span>
                <span aria-hidden="true">
                  <span class="a-price-whole">55<span class="a-price-decimal">.</span></span>
                  <span class="a-price-fraction">24</span>
                </span>
              </span>
            </div>
          </div>
          <div id="rightCol">
            <div id="similarities">
              <span class="a-price">
                <span class="a-offscreen"></span>
                <span aria-hidden="true">
                  <span class="a-price-whole">100<span class="a-price-fraction">89</span></span>
                </span>
              </span>
            </div>
          </div>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        price = AmazonParser.extract_price(soup, html, market="AU")
        self.assertEqual(price, 55.24)
        self.assertNotEqual(price, 10089.0)

    def test_missing_buybox_does_not_take_related_carousel_price(self):
        html = """
        <html><body>
          <div id="rightCol">
            <div id="similarities">
              <span class="a-price">
                <span class="a-offscreen">$100.89</span>
                <span class="a-price-whole">100</span>
                <span class="a-price-fraction">89</span>
              </span>
            </div>
          </div>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        self.assertIsNone(AmazonParser.extract_price(soup, html, market="AU"))

    def test_aud_prefix_accepted_for_au_market(self):
        soup = BeautifulSoup(AU_AUD_PREFIX_HTML, "html.parser")
        self.assertEqual(AmazonParser.extract_price(soup, AU_AUD_PREFIX_HTML, market="AU"), 34.06)

    def test_aud_prefix_rejected_for_us_market(self):
        soup = BeautifulSoup(AU_AUD_PREFIX_HTML, "html.parser")
        self.assertIsNone(AmazonParser.extract_price(soup, AU_AUD_PREFIX_HTML, market="US"))

    def test_form_price_still_wins_on_au_page(self):
        html = AU_NEW_MARKUP_HTML.replace(
            "</div>\n</div>\n<div id=\"similarities\">",
            """<form id="addToCart" action="/gp/product/handle-buy-box/ref=dp_start-bbf_1_glance">
            <input type="hidden" name="items[0.base][customerVisiblePrice][amount]" value="34.06">
            </form></div></div><div id="similarities">""",
        )
        soup = BeautifulSoup(html, "html.parser")
        self.assertEqual(AmazonParser.extract_price(soup, html, market="AU"), 34.06)


NO_FEATURED_OFFER_HTML = """
<html><body>
<span id="productTitle">HAUTMEC Copper Pipe Cutter Bundle with Pipe Cutting Tool</span>
<div id="desktop_buybox">
  <div id="qualifiedBuybox" class="aok-hidden">
    <form id="addToCart" action="/gp/product/handle-buy-box/ref=dp_start-bbf_1_glance">
      <input id="add-to-cart-button" name="submit.add-to-cart" type="submit" value="Add to Cart">
      <input type="hidden" name="items[0.base][customerVisiblePrice][amount]" value="12.99">
    </form>
  </div>
  <div id="unqualifiedBuyBox">
    <span class="a-size-medium a-color-secondary">No featured offers available</span>
    <span class="a-declarative">Learn more</span>
    <span id="buybox-see-all-buying-options" class="a-button a-button-span12">
      <span class="a-button-inner">
        <a id="buybox-see-all-buying-options-announce" class="a-button-text">See All Buying Options</a>
      </span>
    </span>
    <span>Add to List</span>
  </div>
  <div class="a-section aok-hidden twister-plus-buying-options-price-data">
    {"desktop_buybox_group_1":[{"priceAmount":12.99,"displayPrice":"$12.99"}]}
  </div>
</div>
</body></html>
"""


IN_STOCK_WITH_SEE_ALL_HTML = """
<html><body>
<span id="productTitle">In stock widget</span>
<div id="desktop_buybox">
  <div id="corePrice_feature_div">
    <span class="a-price"><span class="a-offscreen">$21.00</span></span>
  </div>
  <form id="addToCart">
    <input id="add-to-cart-button" name="submit.add-to-cart" type="submit" value="Add to Cart">
  </form>
  <span id="buybox-see-all-buying-options" class="a-button">
    <a>See All Buying Options</a>
  </span>
  <div id="availability"><span>In Stock</span></div>
</div>
</body></html>
"""


class TestAmazonUSNoFeaturedOffer(unittest.TestCase):
    def test_no_featured_offer_is_stock_zero_and_ignores_leftover_price(self):
        soup = BeautifulSoup(NO_FEATURED_OFFER_HTML, "html.parser")
        self.assertTrue(AmazonParser.has_no_featured_offer(soup))
        self.assertFalse(AmazonParser.has_visible_add_to_cart(soup))
        self.assertIsNone(AmazonParser.extract_price(soup, NO_FEATURED_OFFER_HTML))
        self.assertEqual(AmazonParser.extract_stock(soup), 0)
        result = AmazonParser.oos_without_offer_result(soup)
        self.assertIsNotNone(result)
        self.assertTrue(result.success)
        self.assertIsNone(result.price)
        self.assertEqual(result.stock, 0)
        self.assertIn("HAUTMEC", result.title or "")

    def test_in_stock_page_with_see_all_keeps_buybox(self):
        soup = BeautifulSoup(IN_STOCK_WITH_SEE_ALL_HTML, "html.parser")
        self.assertFalse(AmazonParser.has_no_featured_offer(soup))
        self.assertTrue(AmazonParser.has_visible_add_to_cart(soup))
        self.assertEqual(AmazonParser.extract_price(soup, IN_STOCK_WITH_SEE_ALL_HTML), 21.0)
        self.assertEqual(AmazonParser.extract_stock(soup), 99)
        self.assertIsNone(AmazonParser.oos_without_offer_result(soup))

    def test_see_all_without_add_to_cart_is_oos(self):
        html = """
        <html><body>
          <span id="productTitle">Pliers bundle</span>
          <div id="desktop_buybox">
            <span id="buybox-see-all-buying-options">See All Buying Options</span>
            <span>Add to List</span>
          </div>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        self.assertTrue(AmazonParser.has_no_featured_offer(soup))
        self.assertEqual(AmazonParser.extract_stock(soup), 0)
        self.assertIsNone(AmazonParser.extract_price(soup, html))


if __name__ == "__main__":
    unittest.main()
