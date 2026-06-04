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

        pm.store_price = Decimal('12.00')

        pm.store_stock = 3



        pm_push_qs = MagicMock()

        pm_push_qs.iterator.return_value = iter([pm])



        fn = run_store_update.__wrapped__



        def _pm_filter(**kwargs):
            qs = MagicMock()
            if kwargs.get('sync_status') == 'scraped':
                qs.select_related.return_value = pm_push_qs
            elif kwargs.get('last_scrape_time__gte') is not None:
                qs.exclude.return_value = qs
                qs.count.return_value = 1
            elif kwargs.get('is_active') is True and 'sync_status' not in kwargs:
                qs.count.return_value = 1
            else:
                qs.exists.return_value = False
                qs.count.return_value = 0
            return qs



        with (
            patch(
                'sync.tasks._reset_active_listings_pending_for_store_update',
                return_value={'rows_updated': 1},
            ),
            patch('sync.tasks._scheduled_ingest_refresh', return_value={}),
            patch(
                'sync.tasks._run_browser_scrape_for_scheduled_update',
                return_value={'completed': True},
            ),
            patch('catalog.tasks._ingest_only_vendor_ids', return_value=[]),
            patch('sync.tasks._store_can_push_to_marketplace', return_value=False),
            patch('sync.tasks._build_store_vendor_pricing_inventory_caches', return_value=({}, None, {}, None)),
            patch('catalog.scrape_progress.invalidate_scrape_progress_cache'),
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

            mock_pm_objects.filter.side_effect = _pm_filter

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

        pm.store_price = Decimal('12.00')
        pm.store_stock = 3

        pm_push_qs = MagicMock()
        pm_push_qs.iterator.return_value = iter([pm])

        fn = run_store_update.__wrapped__

        def _pm_filter(**kwargs):
            qs = MagicMock()
            if kwargs.get('sync_status') == 'scraped':
                qs.select_related.return_value = pm_push_qs
            elif kwargs.get('last_scrape_time__gte') is not None:
                qs.exclude.return_value = qs
                qs.count.return_value = 1
            elif kwargs.get('is_active') is True and 'sync_status' not in kwargs:
                qs.count.return_value = 1
            else:
                qs.exists.return_value = False
                qs.count.return_value = 0
            return qs

        with (
            patch(
                'sync.tasks._reset_active_listings_pending_for_store_update',
                return_value={'rows_updated': 42},
            ) as mock_reset,
            patch('sync.tasks._scheduled_ingest_refresh', return_value={}),
            patch(
                'sync.tasks._run_browser_scrape_for_scheduled_update',
                return_value={'completed': True},
            ) as mock_browser,
            patch('catalog.tasks._ingest_only_vendor_ids', return_value=[]),
            patch('sync.tasks._store_can_push_to_marketplace', return_value=True),
            patch('sync.tasks._build_store_vendor_pricing_inventory_caches', return_value=({}, None, {}, None)),
            patch('catalog.scrape_progress.invalidate_scrape_progress_cache'),
            patch('sync.tasks.StoreSyncRun.objects') as mock_sync_run_objects,
            patch('store_adapters.get_adapter', return_value=adapter),
            patch('sync.tasks.Store.objects') as mock_store_objects,
            patch('sync.tasks.ProductMapping.objects') as mock_pm_objects,
            patch('sync.models.SyncSchedule.objects') as mock_sched_objects,
            patch('catalog.activity_log.append_catalog_log'),
        ):
            mock_store_objects.select_related.return_value.get.return_value = store
            mock_store_objects.filter.return_value.values_list.return_value.first.return_value = 'connected'
            mock_pm_objects.filter.side_effect = _pm_filter
            mock_sync_run_objects.create.return_value = MagicMock()
            mock_sched_objects.get.side_effect = SyncSchedule.DoesNotExist

            result = fn('store-uuid', 'beat')

        mock_reset.assert_called_once_with(store)
        mock_browser.assert_called_once_with(store, 'beat')
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
        pm.store_price = Decimal('12.00')
        pm.store_stock = 3

        pm_push_qs = MagicMock()
        pm_push_qs.iterator.return_value = iter([pm])

        fn = run_store_update.__wrapped__

        def _pm_filter(**kwargs):
            qs = MagicMock()
            if kwargs.get('sync_status') == 'scraped':
                qs.select_related.return_value = pm_push_qs
            elif kwargs.get('last_scrape_time__gte') is not None:
                qs.exclude.return_value = qs
                qs.count.return_value = 1
            elif kwargs.get('is_active') is True and 'sync_status' not in kwargs:
                qs.count.return_value = 1
            else:
                qs.exists.return_value = False
                qs.count.return_value = 0
            return qs

        with (
            patch(
                'sync.tasks._reset_active_listings_pending_for_store_update',
                return_value={'rows_updated': 5},
            ) as mock_reset,
            patch(
                'sync.tasks._scheduled_ingest_refresh',
                return_value={'vevor': {'updated': 5, 'listing_count': 5}},
            ),
            patch(
                'sync.tasks._run_browser_scrape_for_scheduled_update',
                return_value={'completed': True},
            ),
            patch('catalog.tasks._ingest_only_vendor_ids', return_value=[]),
            patch('sync.tasks._store_can_push_to_marketplace', return_value=False),
            patch('sync.tasks._build_store_vendor_pricing_inventory_caches', return_value=({}, None, {}, None)),
            patch('catalog.scrape_progress.invalidate_scrape_progress_cache'),
            patch('sync.tasks.StoreSyncRun.objects') as mock_sync_run_objects,
            patch('store_adapters.get_adapter', return_value=adapter),
            patch('sync.tasks.Store.objects') as mock_store_objects,
            patch('sync.tasks.ProductMapping.objects') as mock_pm_objects,
            patch('sync.models.SyncSchedule.objects') as mock_sched_objects,
            patch('catalog.activity_log.append_catalog_log'),
        ):
            mock_store_objects.select_related.return_value.get.return_value = store
            mock_pm_objects.filter.side_effect = _pm_filter
            mock_sync_run_objects.create.return_value = MagicMock()
            mock_sched_objects.get.side_effect = SyncSchedule.DoesNotExist

            result = fn('store-uuid', 'beat')

        self.assertNotIn('skipped', result)
        mock_reset.assert_called_once_with(store)
        self.assertFalse(result['marketplace_push_enabled'])
        self.assertEqual(result['scraped'], 6)
        self.assertEqual(result['pushed'], 0)
        self.assertEqual(result['push_blocked_not_connected'], 1)
        adapter.update_product.assert_not_called()


class ScheduledIngestRefreshTests(SimpleTestCase):
    @patch('sync.tasks._store_has_pending_vevor_listings', return_value=True)
    @patch('catalog.tasks.run_vevor_au_ingest', return_value={'updated': 100, 'status': 'ok'})
    @patch('catalog.views._store_has_pending_vendor_products', return_value=False)
    def test_scheduled_ingest_refresh_runs_vevor_feed(self, *_mocks):
        from sync.tasks import _scheduled_ingest_refresh

        store = MagicMock()
        store.id = 'store-uuid'
        store.name = 'TFS Vevor'

        from catalog.models import HebScrapeJob

        with patch.object(HebScrapeJob.objects, 'filter') as mock_filter:
            mock_filter.return_value.order_by.return_value.first.return_value = None
            with patch.object(HebScrapeJob.objects, 'create'):
                result = _scheduled_ingest_refresh(store)

        self.assertEqual(result['vevor']['updated'], 100)


