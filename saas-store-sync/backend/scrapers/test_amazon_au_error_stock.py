"""Amazon AU OOS error-selector stock rules."""
from django.test import SimpleTestCase
from bs4 import BeautifulSoup

from scrapers.amazon_au_scraper import AU_OOS_ERROR_SELECTOR, apply_au_error_stock


class AmazonAUErrorStockTests(SimpleTestCase):
    def test_error_selector_forces_stock_zero(self):
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

    def test_no_error_selector_keeps_stock(self):
        html = """
        <div id="availability"><span>In Stock</span></div>
        <span class="a-price"><span class="a-offscreen">$19.99</span></span>
        """
        soup = BeautifulSoup(html, "html.parser")
        self.assertIsNone(soup.select_one(AU_OOS_ERROR_SELECTOR))
        self.assertEqual(apply_au_error_stock(soup, 99), 99)
        self.assertIsNone(apply_au_error_stock(soup, None))
