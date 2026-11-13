"""Watchdog restarts store-wide scrapes whose workers died or hung mid-run."""
from datetime import timedelta
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from catalog.celery_scrape_state import (
    request_celery_scrape_cancel,
    set_celery_scrape_state,
    touch_celery_scrape_heartbeat,
)
from catalog.models import CatalogActivityLog, ProductMapping, StoreCatalogCeleryScrapeState
from catalog.tasks import (
    _advance_store_scrape_lane,
    catalog_scrape_store_chunk_task,
    catalog_scrape_store_finalize,
    catalog_scrape_store_task,
    catalog_scrape_watchdog,
)
from marketplace.models import Marketplace
from products.models import Product
from stores.models import Store
from vendor.models import Vendor

User = get_user_model()


@override_settings(
    DEBUG=True,
    ENCRYPTION_KEY=Fernet.generate_key().decode(),
    CATALOG_SCRAPE_WATCHDOG_MINUTES=45,
    CATALOG_SCRAPE_STALL_MINUTES=20,
)
class ScrapeWatchdogTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='watchdog_u', email='watchdog@example.com', password='pass12345',
        )
        mp, _ = Marketplace.objects.get_or_create(code='sears_watchdog', defaults={'name': 'Sears WD'})
        self.store = Store.objects.create(
            user=self.user, name='Watchdog Store', region='US', api_token='tok-wd', marketplace=mp,
        )
        self.ebay, _ = Vendor.objects.get_or_create(code='ebayus', defaults={'name': 'EbayUS'})
        self.amazon, _ = Vendor.objects.get_or_create(code='amazonus', defaults={'name': 'AmazonUS'})

    def _mapping(self, sku, vendor, status='pending'):
        product = Product.objects.create(
            vendor=vendor, vendor_sku=sku, owner=self.user, vendor_url=f'https://example.com/{sku}',
        )
        return ProductMapping.objects.create(
            store=self.store, product=product, marketplace_id=sku, sync_status=status, is_active=True,
        )

    def _running(self, *, root='root-old', quiet_minutes=0, vendor_id=None):
        set_celery_scrape_state(
            self.store,
            task_id=root,
            scope=StoreCatalogCeleryScrapeState.Scope.STORE,
            vendor_id=vendor_id,
        )
        then = timezone.now() - timedelta(minutes=quiet_minutes)
        StoreCatalogCeleryScrapeState.objects.filter(store=self.store).update(
            enqueued_at=then, first_worker_started_at=then, last_heartbeat_at=then,
        )

    def _state(self):
        return StoreCatalogCeleryScrapeState.objects.get(store=self.store)

    @patch('catalog.tasks.catalog_scrape_store_task.apply_async')
    def test_quiet_scrape_is_restarted_with_new_run_id(self, mock_async):
        self._mapping('E1', self.ebay)
        self._running(quiet_minutes=60)

        out = catalog_scrape_watchdog.run()

        self.assertEqual(out[0]['action'], 'restarted')
        st = self._state()
        self.assertNotEqual(st.root_task_id, 'root-old')
        mock_async.assert_called_once_with(args=[str(self.store.id)], task_id=st.root_task_id)
        self.assertTrue(
            CatalogActivityLog.objects.filter(store=self.store, action_type='scrape_auto_resume').exists(),
        )

    @patch('catalog.tasks.catalog_scrape_store_task.apply_async')
    def test_restart_keeps_vendor_filter(self, mock_async):
        self._mapping('A1', self.amazon)
        self._running(quiet_minutes=60, vendor_id=str(self.amazon.id))

        catalog_scrape_watchdog.run()

        st = self._state()
        mock_async.assert_called_once_with(
            args=[str(self.store.id), str(self.amazon.id)], task_id=st.root_task_id,
        )

    @patch('catalog.tasks.catalog_scrape_store_task.apply_async')
    def test_recent_heartbeat_is_left_alone(self, mock_async):
        self._mapping('E1', self.ebay)
        self._running(quiet_minutes=60)
        touch_celery_scrape_heartbeat(str(self.store.id), 'root-old')

        self.assertEqual(catalog_scrape_watchdog.run(), [])
        mock_async.assert_not_called()
        self.assertEqual(self._state().root_task_id, 'root-old')

    @patch('catalog.tasks.catalog_scrape_store_task.apply_async')
    def test_heartbeat_from_replaced_run_is_ignored(self, mock_async):
        self._running(quiet_minutes=60)
        touch_celery_scrape_heartbeat(str(self.store.id), 'some-older-run')
        self.assertLess(self._state().last_heartbeat_at, timezone.now() - timedelta(minutes=30))

    @patch('catalog.tasks.catalog_scrape_store_task.apply_async')
    def test_stopped_scrape_is_not_restarted(self, mock_async):
        self._mapping('E1', self.ebay)
        self._running(quiet_minutes=60)
        request_celery_scrape_cancel(str(self.store.id))

        self.assertEqual(catalog_scrape_watchdog.run(), [])
        mock_async.assert_not_called()

    @patch('catalog.tasks.catalog_scrape_store_task.apply_async')
    def test_nothing_pending_clears_stuck_banner(self, mock_async):
        self._mapping('E1', self.ebay, status='scraped')
        self._running(quiet_minutes=60)

        out = catalog_scrape_watchdog.run()

        self.assertEqual(out[0]['action'], 'cleared')
        self.assertFalse(StoreCatalogCeleryScrapeState.objects.filter(store=self.store).exists())
        mock_async.assert_not_called()

    @patch('catalog.tasks.catalog_scrape_store_finalize.apply_async')
    @patch('catalog.tasks.catalog_scrape_store_chunk_task.apply_async')
    def test_old_lane_does_not_advance_or_finalize_new_run(self, mock_chunk, mock_final):
        self._running(root='root-new')
        _advance_store_scrape_lane(
            str(self.store.id), [['x']], 1, '', {'user_cancelled': True}, root_task_id='root-old',
        )
        _advance_store_scrape_lane(
            str(self.store.id), [], 1, '', {}, root_task_id='root-old',
        )
        mock_chunk.assert_not_called()
        mock_final.assert_not_called()

    @patch('catalog.tasks.catalog_scrape_store_finalize.apply_async')
    def test_current_lane_finalizes_with_run_id(self, mock_final):
        self._running(root='root-new')
        _advance_store_scrape_lane(str(self.store.id), [], 1, '', {}, root_task_id='root-new')
        mock_final.assert_called_once()
        self.assertEqual(mock_final.call_args.kwargs['kwargs'], {'root_task_id': 'root-new'})

    @patch('catalog.tasks.catalog_scrape_store_finalize.apply_async')
    def test_finalize_gets_totals_of_every_batch(self, mock_final):
        """A last batch that timed out with 0/0 must not hide the run's progress."""
        self._running(root='root-new')
        sid = str(self.store.id)
        _advance_store_scrape_lane(
            sid, [], 2, '', {'rows_processed': 20, 'rows_succeeded': 18, 'failed': 2},
            root_task_id='root-new',
        )
        mock_final.assert_not_called()
        _advance_store_scrape_lane(
            sid, [], 2, '',
            {'rows_processed': 0, 'rows_succeeded': 0, 'failed': 0, 'stalled': True,
             'error_summary': 'batch_time_limit'},
            root_task_id='root-new',
        )
        totals = mock_final.call_args.kwargs['args'][0][0]
        self.assertEqual(totals['rows_succeeded'], 18)
        self.assertEqual(totals['failed'], 2)
        self.assertEqual(totals['rows_processed'], 20)
        self.assertTrue(totals['stalled'])
        self.assertEqual(totals['error_summary'], 'batch_time_limit')

    @patch('catalog.tasks.catalog_scrape_store_finalize.apply_async')
    def test_totals_start_fresh_for_next_run(self, mock_final):
        self._running(root='root-new')
        sid = str(self.store.id)
        _advance_store_scrape_lane(sid, [], 1, '', {'rows_succeeded': 5}, root_task_id='root-new')
        _advance_store_scrape_lane(sid, [], 1, '', {'rows_succeeded': 1}, root_task_id='root-new')
        second = mock_final.call_args_list[1].kwargs['args'][0][0]
        self.assertEqual(second['rows_succeeded'], 1)

    @patch('catalog.tasks.catalog_scrape_store_task.apply_async')
    def test_finalize_continues_when_one_batch_crashed(self, mock_start):
        self._mapping('E1', self.ebay)
        self._running(root='root-new')
        out = catalog_scrape_store_finalize.run(
            [{'rows_succeeded': 10, 'failed': 1, 'rows_processed': 11, 'fatal_error': 'db gone'}],
            str(self.store.id), '', root_task_id='root-new',
        )
        self.assertTrue(out['continued'])
        mock_start.assert_called_once()

    @patch('catalog.tasks.catalog_scrape_store_task.apply_async')
    def test_finalize_stops_when_nothing_moved(self, mock_start):
        self._mapping('E1', self.ebay)
        self._running(root='root-new')
        out = catalog_scrape_store_finalize.run(
            [{'rows_succeeded': 0, 'failed': 0, 'fatal_error': 'db gone'}],
            str(self.store.id), '', root_task_id='root-new',
        )
        self.assertFalse(out['continued'])
        mock_start.assert_not_called()

    @patch('catalog.tasks._advance_store_scrape_lane')
    @patch('catalog.tasks._process_store_wide_scrape_mappings')
    def test_timed_out_batch_reports_rows_it_finished(self, mock_process, mock_advance):
        from celery.exceptions import SoftTimeLimitExceeded

        done = self._mapping('E1', self.ebay, status='scraped')
        bad = self._mapping('E2', self.ebay, status='failed')
        left = self._mapping('E3', self.ebay)
        self._running(root='root-new')
        mock_process.side_effect = SoftTimeLimitExceeded()
        out = catalog_scrape_store_chunk_task.run(
            str(self.store.id), [done.id, bad.id, left.id], [], 1, '', root_task_id='root-new',
        )
        self.assertEqual(out['rows_processed'], 2)
        self.assertEqual(out['rows_succeeded'], 1)
        self.assertEqual(out['failed'], 1)
        self.assertTrue(out['stalled'])

    def test_old_finalize_does_not_clear_new_run(self):
        self._running(root='root-new')
        out = catalog_scrape_store_finalize.run([{}], str(self.store.id), '', root_task_id='root-old')
        self.assertEqual(out['reason'], 'run_replaced')
        self.assertTrue(StoreCatalogCeleryScrapeState.objects.filter(store=self.store).exists())

    @patch('catalog.tasks._advance_store_scrape_lane')
    @patch('catalog.tasks._process_store_wide_scrape_mappings')
    def test_redelivered_old_chunk_exits_without_scraping(self, mock_process, mock_advance):
        self._mapping('E1', self.ebay)
        self._running(root='root-new')
        out = catalog_scrape_store_chunk_task.run(
            str(self.store.id), [], [], 1, '', root_task_id='root-old',
        )
        self.assertTrue(out['user_cancelled'])
        mock_process.assert_not_called()

    @patch('catalog.tasks.run_store_wide_catalog_scrape')
    def test_redelivered_old_start_task_leaves_new_run_alone(self, mock_run):
        self._running(root='root-new')
        catalog_scrape_store_task.push_request(id='root-old')
        try:
            out = catalog_scrape_store_task.run(str(self.store.id))
        finally:
            catalog_scrape_store_task.pop_request()
        self.assertEqual(out['reason'], 'run_replaced')
        mock_run.assert_not_called()
        self.assertEqual(self._state().root_task_id, 'root-new')
