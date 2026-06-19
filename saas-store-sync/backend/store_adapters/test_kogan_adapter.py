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
