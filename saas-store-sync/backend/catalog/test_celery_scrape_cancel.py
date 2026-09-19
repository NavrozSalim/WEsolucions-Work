"""Cooperative cancel and leftover-Pending handling for catalog Celery scrapes."""
from datetime import timedelta
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from catalog.celery_scrape_state import (
    heal_stale_celery_scrape_state,
    request_celery_scrape_cancel,
    set_celery_scrape_state,
    should_abort_celery_scrape,
)
from catalog.models import ProductMapping, StoreCatalogCeleryScrapeState
from catalog.scrape_progress import build_scrape_progress_payload
from catalog.tasks import (
    _pending_left_scrape_note,
    _process_store_wide_scrape_mappings,
    resume_catalog_scrape_after_stop,
)
from marketplace.models import Marketplace
from products.models import Product
from stores.models import Store
from vendor.models import Vendor

User = get_user_model()


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class CeleryScrapeCancelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='celery_cancel_u',
            email='celery_cancel@example.com',
            password='pass12345',
        )
        self.mp, _ = Marketplace.objects.get_or_create(
            code='kogan_celery_cancel',
            defaults={'name': 'Kogan Cancel'},
        )
        self.store = Store.objects.create(
            user=self.user,
            name='Celery Cancel Store',
            region='AU',
            api_token='tok-cc',
            marketplace=self.mp,
        )
        self.vendor, _ = Vendor.objects.get_or_create(
            code='amazonau',
            defaults={'name': 'Amazon AU'},
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.cancel_url = f'/api/v1/stores/{self.store.id}/catalog/scrape/cancel/'

    def _mapping(self, sku, *, status='pending'):
        product = Product.objects.create(
            vendor=self.vendor,
            vendor_sku=sku,
            owner=self.user,
            vendor_url=f'https://www.amazon.com.au/dp/{sku}',
        )
        return ProductMapping.objects.create(
            store=self.store,
            product=product,
            marketplace_id=sku,
            sync_status=status,
            is_active=True,
        )

    def _state(self, *, cancel=False):
        set_celery_scrape_state(
            self.store,
            task_id='root-task-1',
            scope=StoreCatalogCeleryScrapeState.Scope.STORE,
        )
        if cancel:
            request_celery_scrape_cancel(str(self.store.id))
        return StoreCatalogCeleryScrapeState.objects.get(store=self.store)

    def test_should_abort_when_state_missing(self):
        self.assertTrue(should_abort_celery_scrape(str(self.store.id)))

    def test_should_abort_only_after_cancel_flag(self):
        self._state()
        self.assertFalse(should_abort_celery_scrape(str(self.store.id)))
        self.assertTrue(request_celery_scrape_cancel(str(self.store.id)))
        self.assertTrue(should_abort_celery_scrape(str(self.store.id)))
        self.assertTrue(
            StoreCatalogCeleryScrapeState.objects.filter(store=self.store).exists(),
        )

    def test_progress_payload_phase_stopping(self):
        self._state(cancel=True)
        payload = build_scrape_progress_payload(self.store)
        server = payload['server_celery_scrape']
        self.assertTrue(server['active'])
        self.assertEqual(server['phase'], 'stopping')
        self.assertTrue(server['cancel_requested'])

    @patch('core.celery.app.control.revoke')
    @patch('catalog.tasks.resume_catalog_scrape_after_stop.apply_async')
    def test_cancel_keeps_state_and_does_not_auto_resume(self, mock_resume, mock_revoke):
        self._state()
        res = self.client.post(self.cancel_url)
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body['server_scrape_stopped'])
        self.assertTrue(body.get('server_scrape_stopping'))
        self.assertFalse(body.get('server_resume_scheduled'))
        st = StoreCatalogCeleryScrapeState.objects.get(store=self.store)
        self.assertTrue(st.cancel_requested)
        self.assertIsNotNone(st.cancel_requested_at)
        mock_revoke.assert_called_once_with('root-task-1', terminate=False)
        mock_resume.assert_not_called()

    @override_settings(CATALOG_SCRAPE_RESUME_AFTER_STOP_SECONDS=0)
    def test_resume_task_is_disabled_when_setting_is_zero(self):
        out = resume_catalog_scrape_after_stop.run(str(self.store.id))
        self.assertEqual(out, {'skipped': True, 'reason': 'disabled'})

    @patch('scrapers.get_price_and_stock')
    @patch('stores.wallkoala.product_uses_wallkoala', return_value=False)
    @patch('stores.nora.product_uses_nora_inventory', return_value=False)
    @patch('stores.nora.load_store_nora_stock_map', return_value=None)
    def test_store_wide_loop_skips_already_scraped_rows(
        self, _nora_map, _nora, _wk, mock_price,
    ):
        self._state()
        scraped = self._mapping('B0SCRAPED', status='scraped')
        pending = self._mapping('B0PENDING', status='pending')
        mock_price.return_value = {'price': 9.99, 'stock': 3, 'title': 'ok'}
        mappings = ProductMapping.objects.filter(
            id__in=[scraped.id, pending.id],
        ).select_related('product', 'product__vendor').order_by('id')
        _process_store_wide_scrape_mappings(
            mappings,
            store=self.store,
            store_id=str(self.store.id),
            session={},
            emit_stall_log=False,
        )
        self.assertEqual(mock_price.call_count, 1)
        called_url = mock_price.call_args.args[0]
        self.assertIn('B0PENDING', called_url)

    @patch('scrapers.get_price_and_stock')
    @patch('stores.wallkoala.product_uses_wallkoala', return_value=False)
    @patch('stores.nora.product_uses_nora_inventory', return_value=False)
    @patch('stores.nora.load_store_nora_stock_map', return_value=None)
    def test_store_wide_loop_stops_when_cancel_requested(
        self, _nora_map, _nora, _wk, mock_price,
    ):
        self._state(cancel=True)
        self._mapping('B0ONE')
        self._mapping('B0TWO')
        mappings = ProductMapping.objects.filter(
            store=self.store,
        ).select_related('product', 'product__vendor').order_by('id')
        stats = _process_store_wide_scrape_mappings(
            mappings,
            store=self.store,
            store_id=str(self.store.id),
            session={},
            emit_stall_log=False,
        )
        self.assertTrue(stats['user_cancelled'])
        mock_price.assert_not_called()

    def test_pending_left_note_omitted_after_user_cancel(self):
        self._mapping('B0LEFT')
        note, n = _pending_left_scrape_note(self.store, user_cancelled=True)
        self.assertEqual(note, '')
        self.assertEqual(n, 0)
        note, n = _pending_left_scrape_note(self.store, user_cancelled=False)
        self.assertIn('still Pending', note)
        self.assertEqual(n, 1)

    def test_fresh_cancel_is_not_healed(self):
        self._state(cancel=True)
        self.assertFalse(heal_stale_celery_scrape_state(str(self.store.id)))
        self.assertTrue(
            StoreCatalogCeleryScrapeState.objects.filter(store=self.store).exists(),
        )

    def test_heal_clears_legacy_stuck_cancel(self):
        self._state(cancel=True)
        StoreCatalogCeleryScrapeState.objects.filter(store=self.store).update(
            cancel_requested_at=None,
            enqueued_at=timezone.now() - timedelta(days=3),
        )
        self.assertTrue(heal_stale_celery_scrape_state(str(self.store.id)))
        self.assertFalse(
            StoreCatalogCeleryScrapeState.objects.filter(store=self.store).exists(),
        )

    @override_settings(CATALOG_SCRAPE_STOP_STALE_MINUTES=20)
    def test_heal_clears_cancel_after_stale_window(self):
        self._state(cancel=True)
        StoreCatalogCeleryScrapeState.objects.filter(store=self.store).update(
            cancel_requested_at=timezone.now() - timedelta(minutes=21),
        )
        self.assertTrue(heal_stale_celery_scrape_state(str(self.store.id)))
        self.assertFalse(
            StoreCatalogCeleryScrapeState.objects.filter(store=self.store).exists(),
        )

    def test_progress_hides_legacy_stuck_stop(self):
        self._state(cancel=True)
        StoreCatalogCeleryScrapeState.objects.filter(store=self.store).update(
            cancel_requested_at=None,
            enqueued_at=timezone.now() - timedelta(days=3),
        )
        payload = build_scrape_progress_payload(self.store)
        self.assertFalse(payload['server_celery_scrape']['active'])

    @patch('catalog.tasks.catalog_scrape_store_task.apply_async')
    def test_start_scrape_allowed_after_stale_stop(self, mock_apply):
        mock_apply.return_value = None
        self._state(cancel=True)
        StoreCatalogCeleryScrapeState.objects.filter(store=self.store).update(
            cancel_requested_at=None,
            enqueued_at=timezone.now() - timedelta(days=3),
        )
        self._mapping('B0START')
        res = self.client.post(f'/api/v1/stores/{self.store.id}/catalog/scrape/')
        self.assertNotEqual(res.status_code, 409)
        self.assertIn(res.status_code, (200, 202))

