"""One running catalog scrape per vendor: start, stop, finish and progress per vendor."""
from datetime import timedelta
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from catalog.celery_scrape_state import (
    clear_celery_scrape_state,
    current_celery_scrape_root_task_id,
    heal_stale_celery_scrape_state,
    request_celery_scrape_cancel,
    scrape_conflict,
    set_celery_scrape_state,
    should_abort_celery_scrape,
)
from catalog.models import ProductMapping, StoreCatalogCeleryScrapeState
from catalog.scrape_progress import build_scrape_progress_payload
from marketplace.models import Marketplace
from products.models import Product
from stores.models import Store, StoreVendorPriceSettings
from vendor.models import Vendor

User = get_user_model()
STORE = StoreCatalogCeleryScrapeState.Scope.STORE


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class ScrapePerVendorTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='per_vendor_u', email='per_vendor@example.com', password='pass12345',
        )
        mp, _ = Marketplace.objects.get_or_create(code='sears_per_vendor', defaults={'name': 'Sears PV'})
        self.store = Store.objects.create(
            user=self.user, name='Per Vendor Store', region='USA', api_token='tok-pv', marketplace=mp,
        )
        self.amazon, _ = Vendor.objects.get_or_create(code='amazonus', defaults={'name': 'AmazonUS'})
        self.ebay, _ = Vendor.objects.get_or_create(code='ebayus', defaults={'name': 'EbayUS'})
        for v in (self.amazon, self.ebay):
            StoreVendorPriceSettings.objects.create(store=self.store, vendor=v)
        self.sid = str(self.store.id)
        self.amz = str(self.amazon.id)
        self.eb = str(self.ebay.id)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _run(self, vendor, root):
        set_celery_scrape_state(self.store, task_id=root, scope=STORE, vendor_id=vendor)

    def _mapping(self, sku, vendor):
        product = Product.objects.create(
            vendor=vendor, vendor_sku=sku, owner=self.user, vendor_url=f'https://example.com/{sku}',
        )
        return ProductMapping.objects.create(
            store=self.store, product=product, marketplace_id=sku, sync_status='pending', is_active=True,
        )

    # --- the state helpers -------------------------------------------------

    def test_two_vendors_run_at_the_same_time(self):
        self._run(self.amz, 'root-amz')
        self._run(self.eb, 'root-eb')
        self.assertEqual(StoreCatalogCeleryScrapeState.objects.filter(store=self.store).count(), 2)
        self.assertEqual(current_celery_scrape_root_task_id(self.sid, self.amz), 'root-amz')
        self.assertEqual(current_celery_scrape_root_task_id(self.sid, self.eb), 'root-eb')

    def test_conflicts(self):
        self._run(self.amz, 'root-amz')
        self.assertIsNone(scrape_conflict(self.sid, self.eb))  # other vendor: free
        self.assertIsNotNone(scrape_conflict(self.sid, self.amz))  # same vendor: busy
        self.assertIsNotNone(scrape_conflict(self.sid, ''))  # all vendors: busy
        clear_celery_scrape_state(self.sid, root_task_id='root-amz')
        self._run('', 'root-all')
        self.assertIsNotNone(scrape_conflict(self.sid, self.eb))  # all-vendors run covers eBay

    def test_stopping_one_vendor_leaves_the_other_running(self):
        self._run(self.amz, 'root-amz')
        self._run(self.eb, 'root-eb')
        self.assertTrue(request_celery_scrape_cancel(self.sid, vendor_id=self.eb))
        self.assertTrue(should_abort_celery_scrape(self.sid, root_task_id='root-eb'))
        self.assertFalse(should_abort_celery_scrape(self.sid, root_task_id='root-amz'))

    def test_stop_without_vendor_stops_every_run(self):
        self._run(self.amz, 'root-amz')
        self._run(self.eb, 'root-eb')
        request_celery_scrape_cancel(self.sid)
        self.assertTrue(should_abort_celery_scrape(self.sid, root_task_id='root-amz'))
        self.assertTrue(should_abort_celery_scrape(self.sid, root_task_id='root-eb'))

    def test_finishing_one_run_clears_only_its_row(self):
        self._run(self.amz, 'root-amz')
        self._run(self.eb, 'root-eb')
        clear_celery_scrape_state(self.sid, root_task_id='root-eb')
        self.assertFalse(should_abort_celery_scrape(self.sid, root_task_id='root-amz'))
        self.assertTrue(should_abort_celery_scrape(self.sid, root_task_id='root-eb'))

    def test_replaced_run_of_the_same_vendor_aborts_old_workers(self):
        self._run(self.amz, 'root-old')
        self._run(self.amz, 'root-new')
        self.assertTrue(should_abort_celery_scrape(self.sid, root_task_id='root-old'))
        self.assertFalse(should_abort_celery_scrape(self.sid, root_task_id='root-new'))

    @override_settings(CATALOG_SCRAPE_STOP_IDLE_SECONDS=45)
    def test_stopped_vendor_clears_while_another_vendor_keeps_writing(self):
        self._run(self.amz, 'root-amz')
        self._run(self.eb, 'root-eb')
        request_celery_scrape_cancel(self.sid, vendor_id=self.eb)
        StoreCatalogCeleryScrapeState.objects.filter(root_task_id='root-eb').update(
            cancel_requested_at=timezone.now() - timedelta(minutes=2),
        )
        busy = self._mapping('A-LIVE', self.amazon)
        busy.last_scrape_time = timezone.now()
        busy.save(update_fields=['last_scrape_time'])
        self.assertTrue(heal_stale_celery_scrape_state(self.sid))
        remaining = list(StoreCatalogCeleryScrapeState.objects.values_list('root_task_id', flat=True))
        self.assertEqual(remaining, ['root-amz'])

    # --- the API ------------------------------------------------------------

    @patch('catalog.tasks.store_has_scrapeable_pending_mappings', return_value=True)
    @patch('catalog.tasks.catalog_scrape_store_task.apply_async')
    def test_api_starts_a_second_vendor_while_the_first_runs(self, _apply, _pending):
        url = f'/api/v1/stores/{self.sid}/catalog/scrape/'
        first = self.client.post(url, {'vendor_id': self.amz}, format='json')
        self.assertEqual(first.status_code, 202, first.content)
        second = self.client.post(url, {'vendor_id': self.eb}, format='json')
        self.assertEqual(second.status_code, 202, second.content)
        again = self.client.post(url, {'vendor_id': self.amz}, format='json')
        self.assertEqual(again.status_code, 409)
        all_vendors = self.client.post(url, {}, format='json')
        self.assertEqual(all_vendors.status_code, 409)
        self.assertEqual(StoreCatalogCeleryScrapeState.objects.filter(store=self.store).count(), 2)

    @patch('core.celery.app.control.revoke')
    def test_api_stop_one_vendor(self, _revoke):
        self._run(self.amz, 'root-amz')
        self._run(self.eb, 'root-eb')
        url = f'/api/v1/stores/{self.sid}/catalog/scrape/cancel/'
        res = self.client.post(url, {'vendor_id': self.eb}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data['stopped_vendor_ids'], [self.eb])
        self.assertFalse(should_abort_celery_scrape(self.sid, root_task_id='root-amz'))
        self.assertTrue(should_abort_celery_scrape(self.sid, root_task_id='root-eb'))

    @patch('core.celery.app.control.revoke')
    def test_api_stop_all(self, _revoke):
        self._run(self.amz, 'root-amz')
        self._run(self.eb, 'root-eb')
        res = self.client.post(f'/api/v1/stores/{self.sid}/catalog/scrape/cancel/', {}, format='json')
        self.assertEqual(sorted(res.data['stopped_vendor_ids']), sorted([self.amz, self.eb]))

    def test_progress_lists_each_running_vendor(self):
        self._run(self.amz, 'root-amz')
        self._run(self.eb, 'root-eb')
        request_celery_scrape_cancel(self.sid, vendor_id=self.eb)
        payload = build_scrape_progress_payload(self.store)
        runs = {r['vendor_name']: r for r in payload['server_celery_scrapes']}
        self.assertEqual(runs['AmazonUS']['phase'], 'queued')
        self.assertEqual(runs['EbayUS']['phase'], 'stopping')
        self.assertTrue(payload['server_celery_scrape']['active'])
        self.assertFalse(payload['server_celery_scrape']['cancel_requested'])


class ScrapeRunMigrationCopyTests(TestCase):
    """The table move keeps running scrapes: run id, vendor and start time."""

    def test_running_scrape_is_copied_with_its_run_id(self):
        import importlib
        from types import SimpleNamespace

        migration = importlib.import_module('catalog.migrations.0034_scrape_run_per_vendor')
        user = User.objects.create_user(username='mig_u', email='mig@example.com', password='pass12345')
        mp, _ = Marketplace.objects.get_or_create(code='sears_mig', defaults={'name': 'Sears Mig'})
        store = Store.objects.create(user=user, name='Mig Store', region='USA', api_token='tok-mig', marketplace=mp)
        started = timezone.now() - timedelta(hours=3)
        old_rows = [
            SimpleNamespace(
                store_id=store.id, scope='store', upload_id=None, root_task_id='root-running',
                cancel_requested=False, cancel_requested_at=None, first_worker_started_at=started,
                scrape_vendor_id=None, last_heartbeat_at=started, enqueued_at=started,
            ),
        ]

        class _Legacy:
            class objects:
                @staticmethod
                def all():
                    return old_rows

        class _Apps:
            @staticmethod
            def get_model(app, name):
                return _Legacy if name == 'LegacyStoreCeleryScrapeState' else StoreCatalogCeleryScrapeState

        migration.copy_running_scrapes(_Apps(), None)
        run = StoreCatalogCeleryScrapeState.objects.get(store=store)
        self.assertEqual(run.root_task_id, 'root-running')
        self.assertEqual(run.scrape_vendor_id, '')  # old "all vendors" (None) becomes ''
        self.assertEqual(run.enqueued_at, started)
        self.assertEqual(run.last_heartbeat_at, started)
        # Its workers keep going after the deploy: the run is found by its id.
        self.assertFalse(should_abort_celery_scrape(str(store.id), root_task_id='root-running'))
