"""Stop Scraping takes effect inside a retrying listing, and stopped scrapes clear without the page open."""
from datetime import timedelta
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from catalog.celery_scrape_state import request_celery_scrape_cancel, set_celery_scrape_state
from catalog.models import ProductMapping, StoreCatalogCeleryScrapeState
from catalog.tasks import _process_store_wide_scrape_mappings, catalog_scrape_watchdog
from marketplace.models import Marketplace
from products.models import Product
from scrapers.stop_signal import ScrapeStopped, raise_if_scrape_stopped, scrape_stop_check
from stores.models import Store
from vendor.models import Vendor

User = get_user_model()


class StopSignalTests(SimpleTestCase):
    def test_no_check_outside_a_store_scrape(self):
        raise_if_scrape_stopped()  # nothing set: must not raise

    def test_raises_when_stop_was_clicked(self):
        with scrape_stop_check(lambda: True):
            with self.assertRaises(ScrapeStopped):
                raise_if_scrape_stopped()

    def test_no_raise_while_scrape_runs(self):
        with scrape_stop_check(lambda: False):
            raise_if_scrape_stopped()

    def test_check_runs_at_most_every_two_seconds(self):
        calls = []

        def check():
            calls.append(1)
            return False

        with scrape_stop_check(check):
            raise_if_scrape_stopped()
            raise_if_scrape_stopped()
        self.assertEqual(len(calls), 1)

    def test_failed_check_does_not_end_the_listing(self):
        def check():
            raise RuntimeError('database blip')

        with scrape_stop_check(check):
            raise_if_scrape_stopped()

    def test_scraper_retry_handler_cannot_swallow_stop(self):
        def scraper_attempt():
            try:
                raise_if_scrape_stopped()
            except Exception:  # what scrapers do around each attempt
                return 'swallowed'
            return 'ran'

        with scrape_stop_check(lambda: True):
            with self.assertRaises(ScrapeStopped):
                scraper_attempt()

    def test_check_is_cleared_after_the_block(self):
        with scrape_stop_check(lambda: True):
            pass
        raise_if_scrape_stopped()


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class StopInsideListingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='stop_fast_u', email='stop_fast@example.com', password='pass12345',
        )
        mp, _ = Marketplace.objects.get_or_create(code='kogan_stop_fast', defaults={'name': 'Kogan SF'})
        self.store = Store.objects.create(
            user=self.user, name='Stop Fast Store', region='AU', api_token='tok-sf', marketplace=mp,
        )
        self.vendor, _ = Vendor.objects.get_or_create(code='amazonau', defaults={'name': 'Amazon AU'})

    def _mapping(self, sku):
        product = Product.objects.create(
            vendor=self.vendor, vendor_sku=sku, owner=self.user,
            vendor_url=f'https://www.amazon.com.au/dp/{sku}',
        )
        return ProductMapping.objects.create(
            store=self.store, product=product, marketplace_id=sku,
            sync_status='pending', is_active=True,
        )

    def _state(self):
        set_celery_scrape_state(
            self.store, task_id='root-task-1', scope=StoreCatalogCeleryScrapeState.Scope.STORE,
        )

    @patch('scrapers.get_price_and_stock')
    @patch('stores.wallkoala.product_uses_wallkoala', return_value=False)
    @patch('stores.nora.product_uses_nora_inventory', return_value=False)
    @patch('stores.nora.load_store_nora_stock_map', return_value=None)
    def test_stop_during_a_retrying_listing_leaves_it_pending(
        self, _nora_map, _nora, _wk, mock_price,
    ):
        self._state()
        first = self._mapping('B0FIRST')
        second = self._mapping('B0SECOND')

        def retrying_scraper(*_args, **_kwargs):
            # Stop is clicked while the first listing is between retries.
            request_celery_scrape_cancel(str(self.store.id))
            raise_if_scrape_stopped()
            return {'price': 9.99, 'stock': 3, 'title': 'never reached'}

        mock_price.side_effect = retrying_scraper
        mappings = ProductMapping.objects.filter(
            store=self.store,
        ).select_related('product', 'product__vendor').order_by('id')
        stats = _process_store_wide_scrape_mappings(
            mappings, store=self.store, store_id=str(self.store.id),
            session={}, emit_stall_log=False,
        )

        self.assertTrue(stats['user_cancelled'])
        self.assertEqual(stats['failed'], 0)
        self.assertEqual(mock_price.call_count, 1)
        for row in (first, second):
            row.refresh_from_db()
            self.assertEqual(row.sync_status, 'pending')
            self.assertIsNone(row.last_scrape_time)

    @patch('scrapers.get_price_and_stock')
    @patch('stores.wallkoala.product_uses_wallkoala', return_value=False)
    @patch('stores.nora.product_uses_nora_inventory', return_value=False)
    @patch('stores.nora.load_store_nora_stock_map', return_value=None)
    def test_scrape_without_stop_is_unchanged(self, _nora_map, _nora, _wk, mock_price):
        self._state()
        self._mapping('B0OK')

        def scraper(*_args, **_kwargs):
            raise_if_scrape_stopped()
            return {'price': 9.99, 'stock': 3, 'title': 'ok'}

        mock_price.side_effect = scraper
        mappings = ProductMapping.objects.filter(
            store=self.store,
        ).select_related('product', 'product__vendor').order_by('id')
        stats = _process_store_wide_scrape_mappings(
            mappings, store=self.store, store_id=str(self.store.id),
            session={}, emit_stall_log=False,
        )
        self.assertFalse(stats['user_cancelled'])
        self.assertEqual(mock_price.call_count, 1)

    @override_settings(CATALOG_SCRAPE_STOP_IDLE_SECONDS=45)
    @patch('catalog.tasks.catalog_scrape_store_task.apply_async')
    def test_watchdog_ends_a_quiet_stopped_scrape(self, _apply):
        self._state()
        request_celery_scrape_cancel(str(self.store.id))
        StoreCatalogCeleryScrapeState.objects.filter(store=self.store).update(
            cancel_requested_at=timezone.now() - timedelta(minutes=2),
        )
        self._mapping('B0LEFT')

        catalog_scrape_watchdog()

        self.assertFalse(StoreCatalogCeleryScrapeState.objects.filter(store=self.store).exists())
        _apply.assert_not_called()

    @override_settings(CATALOG_SCRAPE_STOP_IDLE_SECONDS=45)
    @patch('catalog.tasks.catalog_scrape_store_task.apply_async')
    def test_watchdog_keeps_a_stop_that_is_still_finishing(self, _apply):
        self._state()
        request_celery_scrape_cancel(str(self.store.id))
        row = self._mapping('B0LIVE')
        row.last_scrape_time = timezone.now()
        row.save(update_fields=['last_scrape_time'])
        StoreCatalogCeleryScrapeState.objects.filter(store=self.store).update(
            cancel_requested_at=timezone.now() - timedelta(minutes=2),
        )

        catalog_scrape_watchdog()

        self.assertTrue(StoreCatalogCeleryScrapeState.objects.filter(store=self.store).exists())
