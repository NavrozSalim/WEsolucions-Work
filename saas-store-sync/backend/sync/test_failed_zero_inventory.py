"""Tests for failed-listing zero-inventory push."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from sync.tasks import run_store_failed_zero_inventory


class FailedZeroInventoryTests(SimpleTestCase):
    def test_failed_zero_only_pushes_failed_listings(self):
        adapter = MagicMock()
        store = MagicMock()
        store.connection_status = 'connected'
        store.is_active = True

        pm = MagicMock()
        pm.store_price = Decimal('10.00')
        pm_qs = MagicMock()
        pm_qs.iterator.return_value = iter([pm])

        fn = run_store_failed_zero_inventory.__wrapped__
        with (
            patch('sync.tasks._resolve_listing_id_for_pm', return_value='WM-1'),
            patch(
                'sync.tasks._build_store_vendor_pricing_inventory_caches',
                return_value=({}, None, {}, None),
            ),
            patch('catalog.marketplace_push.push_product_mapping_to_marketplace', return_value=(True, None)) as mock_push,
            patch('store_adapters.get_adapter', return_value=adapter),
            patch('sync.tasks.Store.objects') as mock_store_objects,
            patch('sync.tasks.ProductMapping.objects') as mock_pm_objects,
            patch('catalog.activity_log.append_catalog_log'),
        ):
            mock_store_objects.select_related.return_value.get.return_value = store
            mock_pm_objects.filter.return_value.select_related.return_value = pm_qs

            result = fn('store-uuid')

        mock_pm_objects.filter.assert_called_once()
        filter_kwargs = mock_pm_objects.filter.call_args[1]
        self.assertEqual(filter_kwargs['sync_status__in'], ['failed', 'needs_attention'])
        self.assertNotIn('error', result or {}, result)
        self.assertEqual(result['local_zeroed'], 1, result)
        self.assertEqual(result['marketplace_push_ok'], 1)
        self.assertEqual(result['marketplace_push_failed'], 0)
        mock_push.assert_called_once()
        self.assertEqual(pm.store_stock, 0)
        store.save.assert_not_called()

    def test_not_connected_returns_error(self):
        store = MagicMock()
        store.connection_status = 'disconnected'

        fn = run_store_failed_zero_inventory.__wrapped__
        with patch('sync.tasks.Store.objects') as mock_store_objects:
            mock_store_objects.select_related.return_value.get.return_value = store
            result = fn('store-uuid')

        self.assertEqual(result['error'], 'not_connected')
