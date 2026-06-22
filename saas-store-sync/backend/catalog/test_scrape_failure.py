"""Tests for scrape-failure handling (zero stock + marketplace push)."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from catalog.scrape_failure import (
    FALLBACK_LISTING_PRICE,
    apply_no_vendor_price_fallback,
    fail_product_mapping,
)


class FailProductMappingTests(SimpleTestCase):
    def test_sets_zero_stock_and_marks_failed(self):
        pm = MagicMock()
        pm.failed_sync_count = 0
        pm.store_id = 'store-1'
        store = MagicMock()
        store.connection_status = 'connected'

        with patch('catalog.scrape_failure._push_zero_stock_for_failed') as mock_push:
            fail_product_mapping(pm, 'scrape_failed', 'timeout', store=store)

        self.assertEqual(pm.store_stock, 0)
        self.assertEqual(pm.failed_sync_count, 1)
        self.assertEqual(pm.sync_status, 'failed')
        self.assertIn('scrape_failed', pm.scrape_error)
        pm.save.assert_called_once()
        mock_push.assert_called_once_with(pm, store)

    def test_needs_attention_after_three_failures(self):
        pm = MagicMock()
        pm.failed_sync_count = 2
        store = MagicMock()
        store.connection_status = 'disconnected'

        with patch('catalog.scrape_failure._push_zero_stock_for_failed'):
            fail_product_mapping(pm, 'blocked', store=store)

        self.assertEqual(pm.sync_status, 'needs_attention')

    def test_push_zero_uses_marketplace_push(self):
        pm = MagicMock()
        pm.store_price = Decimal('12.50')
        store = MagicMock()
        store.connection_status = 'connected'

        with (
            patch('catalog.marketplace_push.push_product_mapping_to_marketplace', return_value=(True, None)) as mock_push,
            patch(
                'sync.tasks._build_store_vendor_pricing_inventory_caches',
                return_value=({}, None, {}, None),
            ),
        ):
            from catalog.scrape_failure import _push_zero_stock_for_failed

            _push_zero_stock_for_failed(pm, store)

        mock_push.assert_called_once()
        self.assertEqual(mock_push.call_args[0][0], pm)
        self.assertEqual(mock_push.call_args[0][1], store)


class ApplyNoVendorPriceFallbackTests(SimpleTestCase):
    def test_sets_fallback_price_and_zero_stock(self):
        pm = MagicMock()
        pm.failed_sync_count = 0
        store = MagicMock()
        store.connection_status = 'connected'

        with patch('catalog.scrape_failure._push_fallback_listing') as mock_push:
            apply_no_vendor_price_fallback(pm, 'no_price', 'missing', store=store)

        self.assertEqual(pm.store_price, FALLBACK_LISTING_PRICE)
        self.assertEqual(pm.store_stock, 0)
        self.assertEqual(pm.sync_status, 'failed')
        self.assertEqual(pm.failed_sync_count, 1)
        self.assertIn('no_price', pm.scrape_error)
        self.assertIn('489.99', pm.scrape_error)
        pm.save.assert_called_once()
        mock_push.assert_called_once_with(pm, store, None, None)
