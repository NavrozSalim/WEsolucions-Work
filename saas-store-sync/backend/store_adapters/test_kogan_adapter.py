"""Tests for Kogan Google Sheets adapter (price, stock, RRP columns)."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from catalog.marketplace_rrp import adapter_push_kwargs
from store_adapters.kogan_adapter import KoganAdapter


def _kogan_store(**overrides):
    store = MagicMock()
    store.kogan_service_account_json = '{"type":"service_account","client_email":"a@b.iam.gserviceaccount.com","private_key":"-----BEGIN PRIVATE KEY-----\\nMIIB\\n-----END PRIVATE KEY-----\\n"}'
    store.kogan_sheet_id = 'sheet-123'
    store.kogan_tab_name = 'Sheet1'
    store.kogan_sku_column = 'PRODUCT_SKU'
    store.kogan_stock_column = 'STOCK'
    store.kogan_price_column = 'PRICE'
    store.kogan_first_price_column = 'kogan_first_price'
    store.kogan_rrp_column = 'rrp'
    store.api_token = ''
    store.marketplace = MagicMock()
    store.marketplace.code = 'kogan'
    store.marketplace.name = 'Kogan'
    for key, val in overrides.items():
        setattr(store, key, val)
    return store


class KoganSheetSkuTests(SimpleTestCase):
    def test_sheet_sku_prefers_catalog_parent_over_a_stale_marketplace_id(self):
        from catalog.marketplace_catalog import kogan_sheet_sku, listing_sku_lookup_order

        pm = MagicMock()
        pm.marketplace_parent_sku = 'SHEET-ONLY'
        pm.marketplace_child_sku = ''
        pm.marketplace_id = 'NOT-THE-SHEET-KEY'
        pm.product.vendor_sku = 'B0AMAZON'
        store = _kogan_store()
        self.assertEqual(kogan_sheet_sku(pm), 'SHEET-ONLY')
        self.assertEqual(listing_sku_lookup_order(pm, store), ['SHEET-ONLY'])


class KoganAdapterRrpTests(SimpleTestCase):
    def test_adapter_push_kwargs_adds_rrp_and_list_price_for_kogan(self):
        store = _kogan_store()
        pm = MagicMock()
        pm.product_id = 1
        pm.product.vendor_id = 'vid-1'
        ps = MagicMock()
        ps.mydeal_rrp_margin_percentage = Decimal('26')
        ps.kogan_price_margin_percentage = Decimal('20')
        kwargs = adapter_push_kwargs(
            store,
            pm,
            74.0,
            5,
            price_by_vendor_id={'vid-1': ps},
        )
        self.assertEqual(kwargs['price'], Decimal('74.00'))
        self.assertEqual(kwargs['stock'], 5)
        self.assertEqual(kwargs['rrp'], Decimal('100.00'))
        self.assertEqual(kwargs['list_price'], Decimal('92.50'))

    @patch.object(KoganAdapter, '_get_service')
    def test_bulk_update_writes_price_first_price_and_rrp_columns(self, mock_get_service):
        store = _kogan_store()
        adapter = KoganAdapter(store)
        adapter._header_cache = [
            'PRODUCT_SKU',
            'STOCK',
            'PRICE',
            'kogan_first_price',
            'rrp',
        ]

        batch_updates = []

        def _batch_update(**kwargs):
            batch_updates.extend(kwargs['body']['data'])
            return MagicMock(execute=MagicMock(return_value={}))

        values_api = MagicMock()
        values_api.get.return_value.execute.side_effect = [
            {'values': [['SKU-1'], ['SKU-2']]},
        ]
        values_api.batchUpdate.side_effect = _batch_update

        spreadsheets_api = MagicMock()
        spreadsheets_api.values.return_value = values_api
        mock_get_service.return_value.spreadsheets.return_value = spreadsheets_api

        result = adapter.update_products_bulk([
            ('SKU-1', 74.0, 3, 100.0, 92.5),
            ('SKU-2', 50.0, 0, None, None),
        ])

        self.assertEqual(result['ok'], {'SKU-1', 'SKU-2'})
        self.assertEqual(result['failed'], [])
        ranges = {u['range']: u['values'][0][0] for u in batch_updates}
        self.assertEqual(ranges['Sheet1!B2'], 3)
        self.assertEqual(ranges['Sheet1!C2'], 92.5)
        self.assertEqual(ranges['Sheet1!D2'], 74.0)
        self.assertEqual(ranges['Sheet1!E2'], 100.0)
        self.assertNotIn('Sheet1!E3', ranges)

    @patch.object(KoganAdapter, '_get_service')
    def test_bulk_update_writes_stock_only_when_price_is_missing(self, mock_get_service):
        store = _kogan_store()
        adapter = KoganAdapter(store)
        adapter._header_cache = [
            'PRODUCT_SKU',
            'STOCK',
            'PRICE',
            'kogan_first_price',
            'rrp',
        ]
        batch_updates = []

        def _batch_update(**kwargs):
            batch_updates.extend(kwargs['body']['data'])
            return MagicMock(execute=MagicMock(return_value={}))

        values_api = MagicMock()
        values_api.get.return_value.execute.return_value = {'values': [['NO-PRICE']]}
        values_api.batchUpdate.side_effect = _batch_update
        spreadsheets_api = MagicMock()
        spreadsheets_api.values.return_value = values_api
        mock_get_service.return_value.spreadsheets.return_value = spreadsheets_api

        result = adapter.update_products_bulk([
            ('NO-PRICE', None, 0, None, None),
        ])

        self.assertEqual(result['ok'], {'NO-PRICE'})
        ranges = {u['range']: u['values'][0][0] for u in batch_updates}
        self.assertEqual(ranges, {'Sheet1!B2': 0})

    @patch.object(KoganAdapter, '_get_service')
    def test_bulk_update_matches_price_rrp_headers_case_insensitive(self, mock_get_service):
        store = _kogan_store(kogan_price_column='PRICE', kogan_rrp_column='rrp')
        adapter = KoganAdapter(store)
        adapter._header_cache = [
            'PRODUCT_SKU',
            'STOCK',
            'Price',
            'kogan_first_price',
            'RRP',
        ]

        batch_updates = []

        def _batch_update(**kwargs):
            batch_updates.extend(kwargs['body']['data'])
            return MagicMock(execute=MagicMock(return_value={}))

        values_api = MagicMock()
        values_api.get.return_value.execute.side_effect = [
            {'values': [['SKU-1']]},
        ]
        values_api.batchUpdate.side_effect = _batch_update

        spreadsheets_api = MagicMock()
        spreadsheets_api.values.return_value = values_api
        mock_get_service.return_value.spreadsheets.return_value = spreadsheets_api

        result = adapter.update_products_bulk([
            ('SKU-1', 74.0, 2, 100.0, 92.5),
        ])

        self.assertEqual(result['ok'], {'SKU-1'})
        ranges = {u['range']: u['values'][0][0] for u in batch_updates}
        self.assertEqual(ranges['Sheet1!C2'], 92.5)
        self.assertEqual(ranges['Sheet1!D2'], 74.0)
        self.assertEqual(ranges['Sheet1!E2'], 100.0)

    @patch.object(KoganAdapter, '_get_service')
    def test_bulk_update_reports_each_sheet_batch_and_stops_between_batches(self, mock_get_service):
        store = _kogan_store()
        adapter = KoganAdapter(store)
        adapter._header_cache = ['PRODUCT_SKU', 'STOCK', 'kogan_first_price']
        values_api = MagicMock()
        values_api.get.return_value.execute.return_value = {'values': [['SKU-1']]}
        spreadsheets_api = MagicMock()
        spreadsheets_api.values.return_value = values_api
        mock_get_service.return_value.spreadsheets.return_value = spreadsheets_api

        progress = []
        result = adapter.update_products_bulk(
            [('SKU-1', 10.0, 4, None, None)],
            on_batch_progress=lambda *args: progress.append(args),
            should_abort=lambda: False,
        )
        self.assertEqual(result['ok'], {'SKU-1'})
        self.assertFalse(result['cancelled'])
        self.assertEqual(progress, [(1, 1, 1, 1)])
        values_api.batchUpdate.assert_called_once()

        values_api.batchUpdate.reset_mock()
        stopped = adapter.update_products_bulk(
            [('SKU-1', 10.0, 4, None, None)],
            should_abort=lambda: True,
        )
        self.assertTrue(stopped['cancelled'])
        self.assertEqual(stopped['ok'], set())
        values_api.batchUpdate.assert_not_called()


class KoganPostScrapePushTests(SimpleTestCase):
    def test_apply_post_scrape_does_not_push_kogan(self):
        """Scrapes must not auto-push; Manual sync / schedule own marketplace push."""
        from catalog.marketplace_push import apply_post_scrape_marketplace_push

        store = _kogan_store()
        pm = MagicMock()
        pm.store_price = Decimal('74.00')
        pm.store_stock = 4
        pm.sync_status = 'scraped'
        pm.scrape_error = 'scrape_failed: old'
        ps = MagicMock()
        ps.mydeal_rrp_margin_percentage = Decimal('26')
        ps.kogan_price_margin_percentage = Decimal('20')

        with patch(
            'catalog.marketplace_push.push_product_mapping_to_marketplace',
            return_value=(True, None),
        ) as mock_push:
            apply_post_scrape_marketplace_push(
                pm,
                store,
                price_by_vendor_id={'vid-1': ps},
                price_fallback=ps,
            )

        mock_push.assert_not_called()
        self.assertEqual(pm.sync_status, 'scraped')
        pm.save.assert_not_called()

    def test_apply_post_scrape_skips_non_kogan_non_walmart(self):
        from catalog.marketplace_push import apply_post_scrape_marketplace_push

        store = MagicMock()
        store.marketplace = MagicMock()
        store.marketplace.code = 'reverb'
        store.marketplace.name = 'Reverb'
        pm = MagicMock()
        pm.sync_status = 'scraped'

        with patch(
            'catalog.marketplace_push.push_product_mapping_to_marketplace',
        ) as mock_push:
            apply_post_scrape_marketplace_push(pm, store)

        mock_push.assert_not_called()
        pm.save.assert_not_called()
