"""Tests for failed-listing zero-inventory push."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from sync.tasks import run_store_failed_zero_inventory


class ManualSyncIncludesFailedTests(SimpleTestCase):
    def test_failed_mapping_stays_failed_after_a_zero_push(self):
        from catalog.marketplace_push import mark_mapping_pushed

        failed = MagicMock()
        failed.sync_status = 'failed'
        failed.scrape_error = 'costway_feed_sku_missing'
        mark_mapping_pushed(failed, clear_error=True, scrape_error='warning')
        self.assertEqual(failed.sync_status, 'failed')
        self.assertEqual(failed.scrape_error, 'costway_feed_sku_missing')
        self.assertNotIn('sync_status', failed.save.call_args[1]['update_fields'])

        scraped = MagicMock()
        scraped.sync_status = 'scraped'
        mark_mapping_pushed(scraped)
        self.assertEqual(scraped.sync_status, 'synced')

    def test_manual_sync_queryset_includes_failed_stock(self):
        from django.db.models import Q

        from catalog.marketplace_push import manual_sync_mappings

        store = MagicMock()
        store.marketplace = None
        store.marketplace_id = None
        listed = MagicMock()
        with patch('catalog.models.ProductMapping.objects') as objects:
            objects.filter.return_value.exclude.return_value = listed
            manual_sync_mappings(store, vendor_id='vendor-1')
        statuses = objects.filter.call_args[1]['sync_status__in']
        self.assertIn('failed', statuses)
        self.assertIn('needs_attention', statuses)
        self.assertIn('scraped', statuses)
        self.assertNotIn('pending', statuses)
        objects.filter.return_value.exclude.assert_called_once_with(
            Q(marketplace_id__isnull=True) | Q(marketplace_id=''),
        )
        listed.filter.assert_called_once_with(product__vendor_id='vendor-1')

    def test_sears_manual_sync_counts_marketplace_id_without_a_child_sku(self):
        from django.db.models import Q

        from catalog.marketplace_push import manual_sync_mappings

        store = MagicMock()
        store.marketplace.code = 'sears'
        with patch('catalog.models.ProductMapping.objects') as objects:
            manual_sync_mappings(store)
        missing_child = Q(marketplace_child_sku__isnull=True) | Q(marketplace_child_sku='')
        missing_id = Q(marketplace_id__isnull=True) | Q(marketplace_id='')
        objects.filter.return_value.exclude.assert_called_once_with(missing_child & missing_id)

    def test_kogan_manual_sync_counts_catalog_sku_without_marketplace_id(self):
        from django.db.models import Q

        from catalog.marketplace_push import manual_sync_mappings

        store = MagicMock()
        store.marketplace.code = 'kogan'
        store.marketplace.name = 'Kogan'
        with patch('catalog.models.ProductMapping.objects') as objects:
            manual_sync_mappings(store)
        missing_parent = Q(marketplace_parent_sku__isnull=True) | Q(marketplace_parent_sku='')
        missing_child = Q(marketplace_child_sku__isnull=True) | Q(marketplace_child_sku='')
        missing_id = Q(marketplace_id__isnull=True) | Q(marketplace_id='')
        objects.filter.return_value.exclude.assert_called_once_with(
            missing_parent & missing_child & missing_id,
        )
        self.assertNotIn('store_price__isnull', objects.filter.call_args.kwargs)


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
