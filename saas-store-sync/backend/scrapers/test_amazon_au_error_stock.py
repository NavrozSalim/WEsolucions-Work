"""Amazon AU OOS error-selector stock rules."""
from django.test import SimpleTestCase
from bs4 import BeautifulSoup

from scrapers.amazon_au_scraper import (
    AU_OOS_ERROR_SELECTOR,
    apply_au_error_stock,
)


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

    def test_no_error_selector_keeps_stock(self):
        html = """
        <div id="availability"><span>In Stock</span></div>
        <span class="a-price"><span class="a-offscreen">$19.99</span></span>
        """
        soup = BeautifulSoup(html, "html.parser")
        self.assertEqual(apply_au_error_stock(soup, 99), 99)
        self.assertIsNone(apply_au_error_stock(soup, None))
