from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from catalog.celery_routing import (
    CatalogScrapeTaskRouter,
    QUEUE_HEAVY_AU,
    QUEUE_HEAVY_US,
    QUEUE_SCRAPE_FINALIZE,
)

from catalog.marketplace_templates import (
    build_field_indices,
    col_index,
    validate_marketplace_headers,
)
from store_adapters import _resolve_adapter_class
from store_adapters.walmart_adapter import WalmartAdapter
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


class AdapterRegistryTests(SimpleTestCase):
    def test_resolve_adapter_class_is_case_insensitive(self):
        self.assertIs(_resolve_adapter_class('walmart'), WalmartAdapter)
        self.assertIs(_resolve_adapter_class('Walmart'), WalmartAdapter)
        self.assertIs(_resolve_adapter_class('WALMART'), WalmartAdapter)


class CatalogScrapeTaskRouterTests(SimpleTestCase):
    """US vs AU Celery queue routing for catalog browser scrapes."""

    def test_finalize_tasks_go_to_light_queue(self):
        r = CatalogScrapeTaskRouter()
        self.assertEqual(
            r.route_for_task('catalog.tasks.catalog_scrape_upload_finalize', (), {}, {}),
            {'queue': QUEUE_SCRAPE_FINALIZE},
        )
        self.assertEqual(
            r.route_for_task('catalog.tasks.catalog_scrape_store_finalize', (), {}, {}),
            {'queue': QUEUE_SCRAPE_FINALIZE},
        )

    def test_unrelated_task_returns_none(self):
        r = CatalogScrapeTaskRouter()
        self.assertIsNone(
            r.route_for_task('analytics.tasks.aggregate_daily_metrics', (), {}, {}),
        )

    def test_route_for_task_accepts_celery_five_signature_three_positionals(self):
        """Celery 5 calls route_for_task(name, args, kwargs) — no options dict."""
        r = CatalogScrapeTaskRouter()
        self.assertEqual(
            r.route_for_task('catalog.tasks.catalog_scrape_upload_finalize', (), {}),
            {'queue': QUEUE_SCRAPE_FINALIZE},
        )

    @patch('catalog.models.CatalogUpload.objects')
    def test_scrape_task_routes_upload_by_store_region(self, mock_objects):
        r = CatalogScrapeTaskRouter()
        mock_objects.values_list.return_value.get.return_value = 'AU'
        self.assertEqual(
            r.route_for_task('catalog.tasks.catalog_scrape_task', ('upload-1',), {}, {}),
            {'queue': QUEUE_HEAVY_AU},
        )
        mock_objects.values_list.return_value.get.return_value = 'USA'
        self.assertEqual(
            r.route_for_task('catalog.tasks.catalog_scrape_upload_chunk_task', ('upload-1', 'run-1', []), {}, {}),
            {'queue': QUEUE_HEAVY_US},
        )

    @patch('stores.models.Store.objects')
    def test_scrape_task_routes_store_by_region(self, mock_objects):
        r = CatalogScrapeTaskRouter()
        mock_objects.values_list.return_value.get.return_value = 'AU'
        self.assertEqual(
            r.route_for_task('catalog.tasks.catalog_scrape_store_task', ('store-1',), {}, {}),
            {'queue': QUEUE_HEAVY_AU},
        )
        mock_objects.values_list.return_value.get.return_value = 'USA'
        self.assertEqual(
            r.route_for_task('catalog.tasks.catalog_scrape_store_chunk_task', ('store-1', []), {}, {}),
            {'queue': QUEUE_HEAVY_US},
        )


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
