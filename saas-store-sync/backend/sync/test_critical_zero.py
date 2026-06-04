"""Tests for emergency critical zero-inventory push."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from sync.models import SyncSchedule
from sync.tasks import run_store_critical_zero_inventory


class CriticalZeroWalmartTests(SimpleTestCase):
    def test_critical_zero_passes_per_listing_ship_node(self):
        mock_push_kwargs = MagicMock(
            return_value={
                'stock': 0,
                'ship_node': '861260459919982593',
                'price': Decimal('10.00'),
            },
        )
        adapter = MagicMock()

        store = MagicMock()
        store.connection_status = 'connected'
        store.is_active = True

        pm = MagicMock()
        pm.store_price = Decimal('10.00')
        pm_qs = MagicMock()
        pm_qs.iterator.return_value = iter([pm])

        fn = run_store_critical_zero_inventory.__wrapped__
        with (
            patch('sync.tasks._adapter_push_kwargs', mock_push_kwargs),
            patch('sync.tasks._resolve_listing_id_for_pm', return_value='WM-1'),
            patch(
                'sync.tasks._build_store_vendor_pricing_inventory_caches',
                return_value=({}, None, {}, None),
            ),
            patch('store_adapters.get_adapter', return_value=adapter),
            patch('sync.tasks.Store.objects') as mock_store_objects,
            patch('sync.tasks.ProductMapping.objects') as mock_pm_objects,
            patch('sync.models.SyncSchedule.objects') as mock_sched_objects,
        ):
            mock_store_objects.select_related.return_value.get.return_value = store
            mock_pm_objects.filter.return_value.select_related.return_value = pm_qs
            mock_sched_objects.get.side_effect = SyncSchedule.DoesNotExist

            result = fn('store-uuid')

        mock_pm_objects.filter.assert_called_once()
        self.assertNotIn('error', result or {}, result)
        self.assertEqual(result['listings_zeroed_local'], 1, result)
        mock_push_kwargs.assert_called_once()
        self.assertEqual(mock_push_kwargs.call_args[0][3], 0)
        adapter.update_product.assert_called_once_with(
            'WM-1',
            stock=0,
            ship_node='861260459919982593',
            price=Decimal('10.00'),
        )
        self.assertEqual(result['marketplace_push_ok'], 1)
        self.assertEqual(result['marketplace_push_failed'], 0)
