from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from catalog.celery_routing import (
    CatalogScrapeTaskRouter,
    QUEUE_HEAVY_AU,
    QUEUE_HEAVY_US,
    QUEUE_SCRAPE_FINALIZE,
)

from catalog.marketplace_templates import (
    build_field_indices,
    col_index,
    resolve_sample_template_kind,
    sample_template_filename_for_kind,
    sample_template_rows_for_kind,
    store_marketplace_kind,
    template_kind_from_store_adapter,
    upload_row_to_cells,
    validate_marketplace_headers,
)
from catalog.marketplace_catalog import listing_sku_lookup_order, store_is_sears, store_is_walmart
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

    @patch('stores.models.Store.objects')
    def test_managed_listing_scrape_routes_by_store_region(self, mock_objects):
        r = CatalogScrapeTaskRouter()
        mock_objects.values_list.return_value.get.return_value = 'AU'
        self.assertEqual(
            r.route_for_task(
                'listings.scrape_store_listings',
                (1, 'store-au', ['1'], 'gen'),
                {},
                {},
            ),
            {'queue': QUEUE_HEAVY_AU},
        )
        mock_objects.values_list.return_value.get.return_value = 'USA'
        self.assertEqual(
            r.route_for_task(
                'listings.scrape_store_listings',
                (),
                {'user_id': 1, 'store_id': 'store-us'},
                {},
            ),
            {'queue': QUEUE_HEAVY_US},
        )


class CeleryStaticTaskRoutesTests(SimpleTestCase):
    """``CELERY_TASK_ROUTES`` dict keys must match ``Task.name`` (see core/settings.py)."""

    def test_routed_tasks_match_registered_names(self):
        from django.conf import settings

        from catalog import tasks as catalog_tasks
        from listings import tasks as listing_tasks
        from sync import tasks as sync_tasks

        static = next(r for r in settings.CELERY_TASK_ROUTES if isinstance(r, dict))
        bindings = [
            (catalog_tasks.catalog_ingest_upload_file_task, 'ingest'),
            (listing_tasks.ingest_listing_bulk_upload, 'ingest'),
            (catalog_tasks.catalog_sync_task, 'ingest'),
            (catalog_tasks.catalog_update_task, 'ingest'),
            (catalog_tasks.resume_catalog_scrape_after_stop, 'light'),
            (catalog_tasks.vevor_au_ingest_task, 'light'),
            (catalog_tasks.costway_au_ingest_task, 'heavy-au'),
            (sync_tasks.run_store_sync, 'sync'),
            (sync_tasks.run_store_update, 'sync'),
            (sync_tasks.run_store_push_listings_only, 'sync'),
            (sync_tasks.run_store_critical_zero_inventory, 'sync'),
            (sync_tasks.run_store_failed_zero_inventory, 'sync'),
            (sync_tasks.check_scheduled_updates, 'light'),
        ]
        from vendor import tasks as vendor_tasks

        self.assertIsNone(static.get('listings.scrape_store_listings'))
        self.assertEqual(
            static.get(vendor_tasks.prune_old_vendor_prices_task.name),
            {'queue': 'celery'},
        )
        for task, expected_queue in bindings:
            with self.subTest(task=task.name):
                self.assertEqual(
                    static.get(task.name),
                    {'queue': expected_queue},
                    msg=f"Update core/settings.py CELERY_TASK_ROUTES for {task.name}",
                )


class CostwayIngestInvokeTests(SimpleTestCase):
    """Costway CSV must be fetched on heavy-au, never inline on sync/light."""

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    @patch('catalog.tasks.costway_au_ingest_task.apply_async')
    def test_invoke_dispatches_to_heavy_au_and_waits(self, mock_async):
        from catalog.celery_routing import QUEUE_HEAVY_AU
        from catalog.tasks import invoke_costway_au_ingest

        mock_async.return_value.get.return_value = {'status': 'ok', 'updated': 2}
        out = invoke_costway_au_ingest('store-1')
        self.assertEqual(out['updated'], 2)
        self.assertEqual(mock_async.call_args.kwargs.get('queue'), QUEUE_HEAVY_AU)
        mock_async.return_value.get.assert_called_once()

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    @patch('catalog.tasks.costway_au_ingest_task.apply_async')
    @patch('catalog.tasks.run_costway_au_ingest', return_value={'status': 'ok', 'updated': 1})
    def test_invoke_eager_runs_inline_without_queue(self, mock_run, mock_async):
        from catalog.tasks import invoke_costway_au_ingest

        out = invoke_costway_au_ingest('store-1')
        self.assertEqual(out['updated'], 1)
        mock_run.assert_called_once()
        mock_async.assert_not_called()


class ScrapeProgressCacheTests(SimpleTestCase):
    def test_cache_key_and_invalidate(self):
        from catalog.scrape_progress import (
            invalidate_scrape_progress_cache,
            scrape_progress_cache_key,
        )
        from django.core.cache import cache

        store_id = '11111111-1111-1111-1111-111111111111'
        key = scrape_progress_cache_key(store_id)
        cache.set(key, {'total': 1}, 60)
        self.assertEqual(cache.get(key), {'total': 1})
        invalidate_scrape_progress_cache(store_id)
        self.assertIsNone(cache.get(key))


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

    def test_walmart_requires_fulfillment_center_column(self):
        header = [
            'Vendor Name',
            'Store Name',
            'SKU',
            'Vendor URL',
            'Action',
            'Pack QTY',
            'Prep Fees',
            'Shipping Fees',
        ]
        idx = build_field_indices(header, _store('walmart'))
        err = validate_marketplace_headers(idx, _store('walmart'))
        self.assertIn('Fulfillment Center ID', err or '')

    def test_walmart_requires_lag_time_column(self):
        header = [
            'Vendor Name',
            'Store Name',
            'SKU',
            'Vendor URL',
            'Action',
            'Pack QTY',
            'Prep Fees',
            'Shipping Fees',
            'Fulfillment Center ID',
        ]
        idx = build_field_indices(header, _store('walmart'))
        err = validate_marketplace_headers(idx, _store('walmart'))
        self.assertIn('Lag Time', err or '')

    def test_walmart_sample_template_includes_fulfillment_center(self):
        headers, rows = sample_template_rows_for_kind('walmart')
        self.assertIn('Fulfillment Center ID', headers)
        self.assertIn('Lag Time', headers)
        self.assertEqual(len(headers), 12)
        self.assertEqual(rows[0][headers.index('Fulfillment Center ID')], '861260459919982593')
        self.assertEqual(rows[0][headers.index('Lag Time')], '1')

    def test_sears_sample_template_excludes_vendor_sku(self):
        headers, rows = sample_template_rows_for_kind('sears')
        self.assertNotIn('Vendor SKU', headers)
        self.assertEqual(headers.index('Marketplace Child SKU'), 4)
        self.assertEqual(len(headers), 7)
        self.assertEqual(len(rows[0]), 7)

    def test_sears_minimal_headers_valid(self):
        headers = [
            'Vendor Name',
            'Vendor ID',
            'Store Name',
            'Marketplace Parent SKU',
            'Marketplace Child SKU',
            'Vendor URL',
            'Action',
        ]
        idx = build_field_indices(headers, _store('sears'))
        self.assertIsNone(validate_marketplace_headers(idx, _store('sears')))

    def test_sears_legacy_wide_headers_still_valid(self):
        headers = [
            'Vendor Name',
            'Vendor ID',
            'Is Variation',
            'Variation ID',
            'Marketplace Name',
            'Store Name',
            'Marketplace Parent SKU',
            'Marketplace Child SKU',
            'Marketplace ID',
            'Vendor URL',
            'Action',
        ]
        idx = build_field_indices(headers, _store('sears'))
        self.assertIsNone(validate_marketplace_headers(idx, _store('sears')))

    def test_sears_legacy_template_with_vendor_sku_column_still_valid(self):
        headers = [
            'Vendor Name',
            'Vendor ID',
            'Is Variation',
            'Variation ID',
            'Marketplace Name',
            'Store Name',
            'Marketplace Parent SKU',
            'Marketplace Child SKU',
            'Marketplace ID',
            'Vendor SKU',
            'Vendor URL',
            'Action',
        ]
        idx = build_field_indices(headers, _store('sears'))
        self.assertIsNone(validate_marketplace_headers(idx, _store('sears')))
        self.assertIsNotNone(idx.get('vendor sku'))
        self.assertIsNotNone(idx.get('marketplace child sku'))

    def test_sears_headers_require_marketplace_child_sku(self):
        headers = [
            'Vendor Name',
            'Vendor ID',
            'Is Variation',
            'Variation ID',
            'Marketplace Name',
            'Store Name',
            'Marketplace Parent SKU',
            'Marketplace ID',
            'Vendor URL',
            'Action',
        ]
        idx = build_field_indices(headers, _store('sears'))
        err = validate_marketplace_headers(idx, _store('sears'))
        self.assertIn('Marketplace Child SKU', err or '')

    def test_sears_sku_alias_maps_to_child_sku(self):
        header = [
            'Vendor Name',
            'Store Name',
            'SKU',
            'Vendor URL',
            'Action',
            'Vendor ID',
            'Is Variation',
            'Variation ID',
            'Marketplace Name',
            'Marketplace Parent SKU',
            'Marketplace ID',
        ]
        idx = build_field_indices(header, _store('sears'))
        self.assertEqual(idx['marketplace child sku'], 2)

    def test_sears_export_row_omits_vendor_sku(self):
        row = MagicMock()
        row.vendor_name_raw = 'Amazon'
        row.vendor_id_raw = 'B0TEST'
        row.is_variation_raw = 'No'
        row.variation_id_raw = ''
        row.marketplace_name_raw = 'Sears'
        row.store_name_raw = 'My Store'
        row.marketplace_parent_sku_raw = 'PARENT-1'
        row.marketplace_child_sku_raw = 'CHILD-1'
        row.marketplace_id_raw = ''
        row.vendor_sku_raw = 'SHOULD-NOT-EXPORT'
        row.vendor_url_raw = 'https://example.com'
        row.action_raw = 'Add'
        cells = upload_row_to_cells(row, _store('sears'))
        self.assertNotIn('SHOULD-NOT-EXPORT', cells)
        self.assertEqual(cells[7], 'CHILD-1')
        self.assertEqual(len(cells), 11)


class SearsCatalogRulesTests(SimpleTestCase):
    def test_store_is_sears(self):
        self.assertTrue(store_is_sears(_store('sears')))
        self.assertFalse(store_is_sears(_store('reverb')))

    def test_listing_sku_lookup_order_sears_uses_child_sku_only(self):
        pm = MagicMock()
        pm.marketplace_child_sku = 'CHILD-99'
        pm.marketplace_parent_sku = 'PARENT-99'
        pm.product = MagicMock()
        pm.product.vendor_sku = 'VENDOR-99'
        store = _store('sears')
        self.assertEqual(listing_sku_lookup_order(pm, store), ['CHILD-99'])

    def test_resolve_listing_id_sears_prefers_child_over_marketplace_id(self):
        from sync.tasks import _resolve_listing_id_for_pm

        pm = MagicMock()
        pm.marketplace_id = 'B646498318'
        pm.marketplace_child_sku = 'UTXY-123-New'
        store = _store('sears')
        adapter = MagicMock()
        self.assertEqual(_resolve_listing_id_for_pm(adapter, pm, store), 'UTXY-123-New')
        adapter.lookup_listing_by_sku.assert_not_called()

    def test_resolve_listing_id_sears_ignores_stale_marketplace_id_without_child(self):
        from sync.tasks import _resolve_listing_id_for_pm

        pm = MagicMock()
        pm.marketplace_id = 'B646498318'
        pm.marketplace_child_sku = ''
        store = _store('sears')
        adapter = MagicMock()
        self.assertIsNone(_resolve_listing_id_for_pm(adapter, pm, store))
        adapter.lookup_listing_by_sku.assert_not_called()

    def test_mydeal_kind_from_store_not_generic_other(self):
        store = _store('mydeal', 'MyDeal')
        self.assertEqual(store_marketplace_kind(store), 'mydeal')
        self.assertEqual(template_kind_from_store_adapter(store), 'other')
        self.assertEqual(resolve_sample_template_kind(store), 'mydeal')

    def test_kogan_and_mydeal_are_distinct_templates(self):
        kogan_h, kogan_rows = sample_template_rows_for_kind('kogan')
        mydeal_h, mydeal_rows = sample_template_rows_for_kind('mydeal')
        self.assertEqual(kogan_h, mydeal_h)
        self.assertEqual(kogan_rows[0][kogan_h.index('Marketplace Name')], 'Kogan')
        self.assertEqual(mydeal_rows[0][mydeal_h.index('Marketplace Name')], 'MyDeal')
        self.assertNotIn('Reverb', mydeal_rows[0])

    def test_each_marketplace_catalog_template_is_distinct(self):
        walmart, _ = sample_template_rows_for_kind('walmart')
        sears, _ = sample_template_rows_for_kind('sears')
        reverb, _ = sample_template_rows_for_kind('reverb')
        other, _ = sample_template_rows_for_kind('other')
        self.assertIn('Fulfillment Center ID', walmart)
        self.assertIn('Lag Time', walmart)
        self.assertNotIn('Vendor SKU', sears)
        self.assertIn('Marketplace Child SKU', sears)
        self.assertEqual(reverb, ['Vendor Name', 'Vendor ID', 'Marketplace Name', 'Store Name', 'SKU', 'Vendor URL', 'Action'])
        self.assertIn('Pack QTY', other)
        self.assertNotEqual(walmart, sears)
        self.assertNotEqual(walmart, reverb)
        self.assertNotEqual(sears, reverb)

    def test_delete_template_keeps_marketplace_columns_and_sets_action(self):
        for kind in ('walmart', 'sears', 'reverb', 'kogan', 'mydeal', 'other'):
            headers, rows = sample_template_rows_for_kind(kind, action='delete')
            catalog_h, _ = sample_template_rows_for_kind(kind, action='catalog')
            self.assertEqual(headers, catalog_h)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][headers.index('Action')], 'Delete')
            self.assertEqual(
                sample_template_filename_for_kind(kind, action='delete'),
                'catalog_delete_template.csv' if kind == 'other' else f'catalog_delete_template_{kind}.csv',
            )

    def test_mydeal_minimal_headers_valid(self):
        header = ['Vendor Name', 'Store Name', 'SKU', 'Vendor URL', 'Action']
        idx = build_field_indices(header, _store('mydeal'))
        self.assertIsNotNone(idx['marketplace parent sku'])
        self.assertIsNone(validate_marketplace_headers(idx, _store('mydeal')))

    def test_query_param_marketplace_wins_over_store(self):
        store = _store('kogan', 'Kogan')
        self.assertEqual(resolve_sample_template_kind(store, 'mydeal'), 'mydeal')
        self.assertEqual(resolve_sample_template_kind(None, 'walmart'), 'walmart')
        self.assertEqual(resolve_sample_template_kind(None, 'unknown'), 'other')


class WalmartCatalogRulesTests(SimpleTestCase):
    def test_store_is_walmart(self):
        self.assertTrue(store_is_walmart(_store('walmart')))
        self.assertFalse(store_is_walmart(_store('sears')))

    def test_listing_sku_lookup_order_walmart_uses_child_sku_only(self):
        pm = MagicMock()
        pm.marketplace_child_sku = 'WM-99'
        pm.marketplace_parent_sku = 'PARENT-99'
        pm.product = MagicMock()
        pm.product.vendor_sku = 'VENDOR-99'
        store = _store('walmart')
        self.assertEqual(listing_sku_lookup_order(pm, store), ['WM-99'])


class AliExpressVendorAliasTests(SimpleTestCase):
    def test_resolve_canonical_vendor_codes(self):
        from catalog.services import resolve_canonical_vendor_code

        self.assertEqual(resolve_canonical_vendor_code('AliExpress UK'), 'aliexpressuk')
        self.assertEqual(resolve_canonical_vendor_code('aliexpressus'), 'aliexpressus')
        self.assertEqual(resolve_canonical_vendor_code('AliExpress AU'), 'aliexpressau')
        self.assertEqual(resolve_canonical_vendor_code('aliexpress'), 'aliexpressuk')
        self.assertEqual(resolve_canonical_vendor_code('aliexpress_us'), 'aliexpressus')
        self.assertEqual(resolve_canonical_vendor_code('Costway'), 'costwayau')
        self.assertEqual(resolve_canonical_vendor_code('CostwayAU'), 'costwayau')
        self.assertEqual(resolve_canonical_vendor_code('costco'), 'costcoau')
        self.assertEqual(resolve_canonical_vendor_code('Wallkoala'), 'wallkoala')
        self.assertEqual(resolve_canonical_vendor_code('wall koala'), 'wallkoala')


class SearsListingSkuFallbackTests(SimpleTestCase):
    def test_copies_parent_when_child_is_na(self):
        from catalog.services import _sears_listing_skus

        parent, child = _sears_listing_skus('UTXY-1', 'N/A')
        self.assertEqual(parent, 'UTXY-1')
        self.assertEqual(child, 'UTXY-1')

    def test_keeps_variation_parent_and_child(self):
        from catalog.services import _sears_listing_skus

        parent, child = _sears_listing_skus('PARENT-1', 'CHILD-1')
        self.assertEqual(parent, 'PARENT-1')
        self.assertEqual(child, 'CHILD-1')

    def test_leaves_empty_when_both_missing(self):
        from catalog.services import _sears_listing_skus

        parent, child = _sears_listing_skus('N/A', '')
        self.assertEqual(parent, 'N/A')
        self.assertEqual(child, '')
