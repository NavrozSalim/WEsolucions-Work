from unittest.mock import MagicMock

from django.test import SimpleTestCase

from catalog.marketplace_templates import (
    build_field_indices,
    col_index,
    validate_marketplace_headers,
)
from scrapers.core import parse_price_text, classify_failure


def _store(code: str, name: str | None = None):
    st = MagicMock()
    st.marketplace = MagicMock()
    st.marketplace.code = code
    st.marketplace.name = name or code.title()
    return st


class ScraperParsingTests(SimpleTestCase):
    def test_parse_price_text_extracts_float(self):
        self.assertEqual(parse_price_text("$1,249.99"), 1249.99)
        self.assertEqual(parse_price_text("AUD 15.00"), 15.00)
        self.assertIsNone(parse_price_text("not-a-price"))

    def test_classify_failure_detects_http_and_captcha(self):
        self.assertEqual(classify_failure(404, "", parse_failed=False), "not_found")
        html = "<html><body>Please verify you are human captcha</body></html>"
        self.assertEqual(classify_failure(200, html, parse_failed=False), "captcha")
        self.assertEqual(classify_failure(200, "<html>ok</html>", parse_failed=True), "parse_error")


class MarketplaceTemplateTests(SimpleTestCase):
    def test_col_index_sku_does_not_match_marketplace_parent_sku_header(self):
        header = ['Vendor Name', 'Marketplace Parent SKU', 'Vendor URL']
        self.assertIsNone(col_index(header, 'sku'))

    def test_col_index_sku_matches_exact_column(self):
        header = ['Vendor Name', 'SKU', 'Vendor URL']
        self.assertEqual(col_index(header, 'sku'), 1)

    def test_reverb_minimal_headers_map_sku_to_parent(self):
        header = ['Vendor Name', 'Store Name', 'SKU', 'Vendor URL', 'Action']
        idx = build_field_indices(header, _store('reverb'))
        self.assertIsNotNone(idx['marketplace parent sku'])
        self.assertEqual(idx['marketplace parent sku'], 2)
        self.assertIsNone(validate_marketplace_headers(idx, _store('reverb')))

    def test_walmart_requires_fee_columns(self):
        header = ['Vendor Name', 'Store Name', 'SKU', 'Vendor URL', 'Action']
        idx = build_field_indices(header, _store('walmart'))
        err = validate_marketplace_headers(idx, _store('walmart'))
        self.assertIn('Pack QTY', err or '')
