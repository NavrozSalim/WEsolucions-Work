"""Tests for marketplace push gating when store disconnects mid-update."""

from __future__ import annotations



from decimal import Decimal

from unittest.mock import MagicMock, patch



from django.test import SimpleTestCase



from sync.models import SyncSchedule

from sync.tasks import _store_can_push_to_marketplace, run_store_update





class StoreCanPushTests(SimpleTestCase):

    @patch('sync.tasks.Store.objects')

    def test_returns_true_only_when_connected(self, mock_store_objects):

        mock_store_objects.filter.return_value.values_list.return_value.first.side_effect = [

            'connected',

            'pending',

            None,

        ]

        self.assertTrue(_store_can_push_to_marketplace('store-id'))

        self.assertFalse(_store_can_push_to_marketplace('store-id'))

        self.assertFalse(_store_can_push_to_marketplace('store-id'))





class RunStoreUpdatePushGateTests(SimpleTestCase):

    def test_scrape_completes_but_push_skipped_when_not_connected(self):

        adapter = MagicMock()

        store = MagicMock()

        store.id = 'store-uuid'

        store.name = 'Test Store'

        store.connection_status = 'connected'

        store.is_active = True

        store.region = 'USA'



        product = MagicMock()

        product.vendor_id = 1

        product.vendor_sku = 'SKU-1'

        product.vendor_url = 'https://example.com/item'

        vendor = MagicMock()

        vendor.code = 'amazon'

        product.vendor = vendor



        pm = MagicMock()

        pm.product = product

        pm.marketplace_id = 'MP-1'

        pm.marketplace_child_sku = 'CHILD-1'

        pm.store_price = None

        pm.store_stock = 0



        pm_qs = MagicMock()

        pm_qs.iterator.return_value = iter([pm])



        fn = run_store_update.__wrapped__



        with (
            patch(
                'sync.tasks._reset_active_listings_pending_for_store_update',
                return_value={'rows_updated': 1},
            ),
            patch('sync.tasks._store_can_push_to_marketplace', return_value=False),

            patch('sync.tasks.get_price_and_stock', return_value={'price': 10.0, 'stock': 5}),

            patch('sync.tasks._is_ingest_only_product', return_value=False),

            patch('sync.tasks._build_store_vendor_pricing_inventory_caches', return_value=({}, None, {}, None)),

            patch('sync.tasks._get_pricing_for_vendor_from_cache', return_value=None),

            patch('sync.tasks._get_inventory_for_vendor_from_cache', return_value=None),

            patch('sync.tasks._apply_pricing', return_value=Decimal('12.00')),

            patch('sync.tasks._apply_inventory', return_value=3),

            patch('sync.tasks._has_fixed_tier', return_value=False),

            patch('sync.tasks.VendorPrice.objects') as mock_vp_objects,

            patch('sync.tasks.close_amazon_session'),

            patch('sync.tasks.StoreSyncRun.objects') as mock_sync_run_objects,

            patch('store_adapters.get_adapter', return_value=adapter),

            patch('sync.tasks.Store.objects') as mock_store_objects,

            patch('sync.tasks.ProductMapping.objects') as mock_pm_objects,

            patch('sync.models.SyncSchedule.objects') as mock_sched_objects,

            patch('catalog.activity_log.append_catalog_log') as mock_log,

        ):

            mock_store_objects.select_related.return_value.get.return_value = store

            mock_store_objects.filter.return_value.values_list.return_value.first.return_value = (

                'connected'

            )

            mock_pm_objects.filter.return_value.select_related.return_value = pm_qs

            mock_pm_objects.filter.return_value.count.side_effect = [1, 0]

            mock_vp_objects.filter.return_value.order_by.return_value.first.return_value = None

            mock_sync_run_objects.create.return_value = MagicMock()

            mock_sched_objects.get.side_effect = SyncSchedule.DoesNotExist



            result = fn('store-uuid', 'manual')



        self.assertEqual(result['scraped'], 1)

        self.assertEqual(result['pushed'], 0)

        self.assertEqual(result['push_blocked_not_connected'], 1)

        adapter.update_product.assert_not_called()
        skipped_calls = [
            c
            for c in mock_log.call_args_list
            if c.kwargs.get('action_type') == 'sync_push_skipped_not_connected'
        ]
        self.assertEqual(len(skipped_calls), 1)


class RunStoreUpdatePendingResetTests(SimpleTestCase):
    def test_beat_run_resets_pending_then_scrapes_pending_rows_only(self):
        adapter = MagicMock(spec=['update_product', 'lookup_listing_by_sku'])
        store = MagicMock()
        store.id = 'store-uuid'
        store.name = 'Test Store'
        store.connection_status = 'connected'
        store.is_active = True
        store.region = 'USA'
        store.pk = 'store-uuid'

        product = MagicMock()
        product.vendor_id = 1
        product.vendor_sku = 'SKU-1'
        product.vendor_url = 'https://example.com/item'
        product.vendor = MagicMock(code='amazon')

        pm = MagicMock()
        pm.product = product
        pm.marketplace_id = 'MP-1'
        pm.marketplace_child_sku = 'CHILD-1'

        pm_qs = MagicMock()
        pm_qs.iterator.return_value = iter([pm])

        fn = run_store_update.__wrapped__

        with (
            patch(
                'sync.tasks._reset_active_listings_pending_for_store_update',
                return_value={'rows_updated': 42},
            ) as mock_reset,
            patch('sync.tasks._store_can_push_to_marketplace', return_value=True),
            patch('sync.tasks.get_price_and_stock', return_value={'price': 10.0, 'stock': 5}),
            patch('sync.tasks._is_ingest_only_product', return_value=False),
            patch('sync.tasks._build_store_vendor_pricing_inventory_caches', return_value=({}, None, {}, None)),
            patch('sync.tasks._get_pricing_for_vendor_from_cache', return_value=None),
            patch('sync.tasks._get_inventory_for_vendor_from_cache', return_value=None),
            patch('sync.tasks._apply_pricing', return_value=Decimal('12.00')),
            patch('sync.tasks._apply_inventory', return_value=3),
            patch('sync.tasks._has_fixed_tier', return_value=False),
            patch('sync.tasks.VendorPrice.objects') as mock_vp_objects,
            patch('sync.tasks.close_amazon_session'),
            patch('sync.tasks.StoreSyncRun.objects') as mock_sync_run_objects,
            patch('store_adapters.get_adapter', return_value=adapter),
            patch('sync.tasks.Store.objects') as mock_store_objects,
            patch('sync.tasks.ProductMapping.objects') as mock_pm_objects,
            patch('sync.models.SyncSchedule.objects') as mock_sched_objects,
            patch('catalog.activity_log.append_catalog_log'),
        ):
            mock_store_objects.select_related.return_value.get.return_value = store
            mock_store_objects.filter.return_value.values_list.return_value.first.return_value = 'connected'
            mock_pm_objects.filter.return_value.select_related.return_value = pm_qs
            mock_pm_objects.filter.return_value.count.side_effect = [1, 0]
            mock_vp_objects.filter.return_value.order_by.return_value.first.return_value = None
            mock_sync_run_objects.create.return_value = MagicMock()
            mock_sched_objects.get.side_effect = SyncSchedule.DoesNotExist

            result = fn('store-uuid', 'beat')

        mock_reset.assert_called_once_with(store)
        filter_kwargs = mock_pm_objects.filter.call_args_list[-1].kwargs
        self.assertEqual(filter_kwargs.get('sync_status'), 'pending')
        self.assertEqual(result['pending_reset_rows'], 42)
        self.assertEqual(result['scraped'], 1)
        adapter.update_product.assert_called_once()


class RunStoreUpdateScrapeWhenDisconnectedTests(SimpleTestCase):
    def test_starts_reset_and_scrape_when_not_connected_at_start(self):
        adapter = MagicMock(spec=['update_product', 'lookup_listing_by_sku'])
        store = MagicMock()
        store.id = 'store-uuid'
        store.name = 'Test Store'
        store.connection_status = 'pending'
        store.is_active = True
        store.region = 'USA'

        product = MagicMock()
        product.vendor_id = 1
        product.vendor_sku = 'SKU-1'
        product.vendor_url = 'https://example.com/item'
        product.vendor = MagicMock(code='amazon')

        pm = MagicMock()
        pm.product = product
        pm.marketplace_id = 'MP-1'
        pm.marketplace_child_sku = 'CHILD-1'

        pm_qs = MagicMock()
        pm_qs.iterator.return_value = iter([pm])

        fn = run_store_update.__wrapped__

        with (
            patch(
                'sync.tasks._reset_active_listings_pending_for_store_update',
                return_value={'rows_updated': 5},
            ) as mock_reset,
            patch('sync.tasks._store_can_push_to_marketplace', return_value=False),
            patch('sync.tasks.get_price_and_stock', return_value={'price': 10.0, 'stock': 5}),
            patch('sync.tasks._is_ingest_only_product', return_value=False),
            patch('sync.tasks._build_store_vendor_pricing_inventory_caches', return_value=({}, None, {}, None)),
            patch('sync.tasks._get_pricing_for_vendor_from_cache', return_value=None),
            patch('sync.tasks._get_inventory_for_vendor_from_cache', return_value=None),
            patch('sync.tasks._apply_pricing', return_value=Decimal('12.00')),
            patch('sync.tasks._apply_inventory', return_value=3),
            patch('sync.tasks._has_fixed_tier', return_value=False),
            patch('sync.tasks.VendorPrice.objects') as mock_vp_objects,
            patch('sync.tasks.close_amazon_session'),
            patch('sync.tasks.StoreSyncRun.objects') as mock_sync_run_objects,
            patch('store_adapters.get_adapter', return_value=adapter),
            patch('sync.tasks.Store.objects') as mock_store_objects,
            patch('sync.tasks.ProductMapping.objects') as mock_pm_objects,
            patch('sync.models.SyncSchedule.objects') as mock_sched_objects,
            patch('catalog.activity_log.append_catalog_log'),
        ):
            mock_store_objects.select_related.return_value.get.return_value = store
            mock_pm_objects.filter.return_value.select_related.return_value = pm_qs
            mock_pm_objects.filter.return_value.count.side_effect = [1, 0]
            mock_vp_objects.filter.return_value.order_by.return_value.first.return_value = None
            mock_sync_run_objects.create.return_value = MagicMock()
            mock_sched_objects.get.side_effect = SyncSchedule.DoesNotExist

            result = fn('store-uuid', 'beat')

        self.assertNotIn('skipped', result)
        mock_reset.assert_called_once_with(store)
        self.assertFalse(result['marketplace_push_enabled'])
        self.assertEqual(result['scraped'], 1)
        self.assertEqual(result['pushed'], 0)
        self.assertEqual(result['push_blocked_not_connected'], 1)
        adapter.update_product.assert_not_called()


