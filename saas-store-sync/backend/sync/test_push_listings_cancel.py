"""Tests for Manual sync (push listings) cooperative cancel."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from catalog.models import CatalogActivityLog, ProductMapping
from marketplace.models import Marketplace
from products.models import Product
from stores.models import Store
from vendor.models import Vendor
from sync.push_listings_cancel import (
    PushListingsCancelled,
    clear_push_listings_cancel,
    request_push_listings_cancel,
    should_abort_push_listings,
)
from sync.push_listings_lock import (
    force_release_push_listings_lock,
    try_acquire_push_listings_lock,
)
from sync.tasks import _execute_store_push_listings_only, _raise_if_push_aborted

User = get_user_model()


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class PushListingsCancelFlagTests(TestCase):
    def setUp(self):
        self.store_id = '660956e1-cadc-44c4-b807-a4b30467d542'

    def tearDown(self):
        clear_push_listings_cancel(self.store_id)

    def test_request_and_clear_cancel_flag(self):
        self.assertFalse(should_abort_push_listings(self.store_id))
        request_push_listings_cancel(self.store_id)
        self.assertTrue(should_abort_push_listings(self.store_id))
        clear_push_listings_cancel(self.store_id)
        self.assertFalse(should_abort_push_listings(self.store_id))

    def test_raise_if_push_aborted_raises(self):
        request_push_listings_cancel(self.store_id)
        with self.assertRaises(PushListingsCancelled):
            _raise_if_push_aborted(self.store_id)


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class CatalogPushListingsCancelViewTests(TestCase):
    def setUp(self):
        from rest_framework.test import APIClient

        self.client = APIClient()
        self.mp, _ = Marketplace.objects.get_or_create(
            code='push_cancel_mt',
            defaults={'name': 'Push Cancel MT'},
        )
        self.user = User.objects.create_user(
            username='push_cancel_u',
            email='push_cancel_u@example.com',
            password='pass12345',
        )
        self.store = Store.objects.create(
            user=self.user,
            name='Push Cancel Store',
            region='USA',
            api_token='tok-cancel',
            marketplace=self.mp,
            connection_status='connected',
        )
        self.client.force_authenticate(user=self.user)
        self.url = f'/api/v1/stores/{self.store.id}/catalog/push-listings/cancel/'

    def tearDown(self):
        clear_push_listings_cancel(str(self.store.id))
        force_release_push_listings_lock(str(self.store.id))

    def test_cancel_returns_nothing_running_when_idle(self):
        resp = self.client.post(self.url, {}, format='json')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body['push_listings_stopped'])
        self.assertIn('nothing to stop', body['detail'].lower())

    @patch('core.celery.app.control.revoke')
    def test_cancel_stops_running_push(self, revoke_mock):
        try_acquire_push_listings_lock(str(self.store.id), 'task-cancel-xyz')
        resp = self.client.post(self.url, {}, format='json')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body['push_listings_stopped'])
        self.assertEqual(body['job_id'], 'task-cancel-xyz')
        revoke_mock.assert_called_once()
        self.assertTrue(should_abort_push_listings(str(self.store.id)))
        self.assertIsNone(force_release_push_listings_lock(str(self.store.id)))
        self.assertTrue(
            CatalogActivityLog.objects.filter(
                store=self.store,
                action_type='sync_cancelled',
            ).exists(),
        )


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class ExecutePushListingsCancelTests(TestCase):
    def setUp(self):
        self.mp, _ = Marketplace.objects.get_or_create(
            code='reverb_cancel_mt',
            defaults={'name': 'Reverb Cancel MT'},
        )
        self.user = User.objects.create_user(
            username='exec_cancel_u',
            email='exec_cancel_u@example.com',
            password='pass12345',
        )
        self.store = Store.objects.create(
            user=self.user,
            name='Exec Cancel Store',
            region='USA',
            api_token='tok-exec',
            marketplace=self.mp,
            connection_status='connected',
        )
        self.vendor, _ = Vendor.objects.get_or_create(
            code='cancel_vendor',
            defaults={'name': 'Cancel Vendor'},
        )
        product = Product.objects.create(
            owner=self.user,
            vendor=self.vendor,
            vendor_sku='sku-cancel-1',
            vendor_url='https://example.com/p',
        )
        ProductMapping.objects.create(
            store=self.store,
            product=product,
            is_active=True,
            sync_status='synced',
            store_price=12,
            store_stock=3,
            marketplace_child_sku='listing-1',
        )
        product2 = Product.objects.create(
            owner=self.user,
            vendor=self.vendor,
            vendor_sku='sku-cancel-2',
            vendor_url='https://example.com/p2',
        )
        ProductMapping.objects.create(
            store=self.store,
            product=product2,
            is_active=True,
            sync_status='synced',
            store_price=15,
            store_stock=2,
            marketplace_child_sku='listing-2',
        )

    def tearDown(self):
        clear_push_listings_cancel(str(self.store.id))

    @patch('catalog.marketplace_push.push_product_mapping_to_marketplace')
    def test_execute_returns_cancelled_when_abort_flag_set(self, push_mock):
        request_push_listings_cancel(str(self.store.id))
        result = _execute_store_push_listings_only(str(self.store.id), disable_schedule=False)
        self.assertTrue(result.get('cancelled'))
        self.assertEqual(result.get('pushed'), 0)
        push_mock.assert_not_called()
        self.assertTrue(
            CatalogActivityLog.objects.filter(
                store=self.store,
                action_type='sync_end',
                metadata__cancelled=True,
            ).exists(),
        )


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class SearsReportWaitAbortTests(TestCase):
    @patch('store_adapters.sears_adapter.SearsAdapter._request')
    @patch('store_adapters.sears_adapter.time.sleep', return_value=None)
    def test_wait_for_processing_report_aborts_during_poll(self, _sleep_mock, request_mock):
        from store_adapters.sears_adapter import SearsAdapter

        request_mock.return_value = (
            '<?xml version="1.0"?><processing-report>'
            '<document-id>999</document-id><status>Submitted</status>'
            '<report><summary>'
            '<records-accepted>0</records-accepted>'
            '<records-with-errors>0</records-with-errors>'
            '</summary></report></processing-report>'
        )
        adapter = SearsAdapter({
            'seller_id': '10673110',
            'email': 'a@b.com',
            'secret_key': 'secret',
            'location_id': 'loc1',
        })
        cancelled = {'value': False}

        def should_abort():
            cancelled['value'] = True
            return True

        with self.assertRaises(PushListingsCancelled):
            adapter._wait_for_processing_report(
                '999',
                should_abort=should_abort,
            )
        self.assertTrue(cancelled['value'])


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class KoganManualBulkPushTests(TestCase):
    def setUp(self):
        self.mp, _ = Marketplace.objects.get_or_create(
            code='kogan',
            defaults={'name': 'Kogan'},
        )
        self.user = User.objects.create_user(
            username='kogan_bulk_u',
            email='kogan_bulk_u@example.com',
            password='pass12345',
        )
        self.store = Store.objects.create(
            user=self.user,
            name='Kogan Bulk Store',
            region='AU',
            marketplace=self.mp,
            connection_status='connected',
            kogan_sheet_id='sheet-123',
            kogan_tab_name='Sheet1',
        )
        self.vendor, _ = Vendor.objects.get_or_create(
            code='kogan_bulk_vendor',
            defaults={'name': 'Kogan Bulk Vendor'},
        )
        for idx, sku in enumerate(('KOG-SKU-1', 'KOG-SKU-2', 'KOG-SKU-3'), start=1):
            product = Product.objects.create(
                owner=self.user,
                vendor=self.vendor,
                vendor_sku=f'vendor-{idx}',
                vendor_url=f'https://example.com/p{idx}',
            )
            ProductMapping.objects.create(
                store=self.store,
                product=product,
                is_active=True,
                sync_status='scraped',
                store_price=10 + idx,
                store_stock=idx,
                marketplace_child_sku=sku,
                marketplace_id=sku,
            )

    @patch('store_adapters.get_adapter')
    def test_manual_sync_calls_bulk_once_not_per_row(self, get_adapter_mock):
        adapter = MagicMock()
        adapter.update_products_bulk.return_value = {
            'ok': {'KOG-SKU-1', 'KOG-SKU-2', 'KOG-SKU-3'},
            'failed': [],
        }
        get_adapter_mock.return_value = adapter

        result = _execute_store_push_listings_only(str(self.store.id), disable_schedule=False)

        self.assertEqual(result.get('pushed'), 3)
        self.assertEqual(result.get('failed'), 0)
        adapter.update_products_bulk.assert_called_once()
        payload = adapter.update_products_bulk.call_args[0][0]
        self.assertEqual(len(payload), 3)
        skus = {row[0] for row in payload}
        self.assertEqual(skus, {'KOG-SKU-1', 'KOG-SKU-2', 'KOG-SKU-3'})
        self.assertIn('on_batch_progress', adapter.update_products_bulk.call_args.kwargs)
        self.assertIn('should_abort', adapter.update_products_bulk.call_args.kwargs)

    @patch('store_adapters.get_adapter')
    def test_manual_sync_pushes_kogan_parent_sku_when_marketplace_id_is_blank(self, get_adapter_mock):
        product = Product.objects.create(
            owner=self.user,
            vendor=self.vendor,
            vendor_sku='vendor-sheet-only',
            vendor_url='https://example.com/sheet-only',
        )
        ProductMapping.objects.create(
            store=self.store,
            product=product,
            is_active=True,
            sync_status='scraped',
            store_price=19,
            store_stock=4,
            marketplace_parent_sku='SHEET-ONLY',
            marketplace_child_sku='',
            marketplace_id='',
        )
        adapter = MagicMock()
        adapter.update_products_bulk.return_value = {
            'ok': {'KOG-SKU-1', 'KOG-SKU-2', 'KOG-SKU-3', 'SHEET-ONLY'},
            'failed': [],
        }
        get_adapter_mock.return_value = adapter

        result = _execute_store_push_listings_only(str(self.store.id), disable_schedule=False)

        self.assertEqual(result.get('pushed'), 4)
        payload = adapter.update_products_bulk.call_args[0][0]
        self.assertIn('SHEET-ONLY', {row[0] for row in payload})
        saved = ProductMapping.objects.get(product=product)
        self.assertEqual(saved.marketplace_id, 'SHEET-ONLY')

    @patch('store_adapters.get_adapter')
    def test_manual_sync_sends_stock_zero_when_kogan_price_is_missing(self, get_adapter_mock):
        product = Product.objects.create(
            owner=self.user,
            vendor=self.vendor,
            vendor_sku='vendor-no-price',
            vendor_url='https://example.com/no-price',
        )
        ProductMapping.objects.create(
            store=self.store,
            product=product,
            is_active=True,
            sync_status='failed',
            store_price=None,
            store_stock=4,
            marketplace_parent_sku='NO-PRICE',
            marketplace_child_sku='',
            marketplace_id='',
        )
        adapter = MagicMock()
        adapter.update_products_bulk.return_value = {
            'ok': {'KOG-SKU-1', 'KOG-SKU-2', 'KOG-SKU-3', 'NO-PRICE'},
            'failed': [],
        }
        get_adapter_mock.return_value = adapter

        result = _execute_store_push_listings_only(str(self.store.id), disable_schedule=False)

        self.assertEqual(result.get('pushed'), 4)
        payload = adapter.update_products_bulk.call_args[0][0]
        no_price = next(row for row in payload if row[0] == 'NO-PRICE')
        self.assertIsNone(no_price[1])
        self.assertEqual(no_price[2], 0)
        saved = ProductMapping.objects.get(product=product)
        self.assertEqual(saved.store_stock, 0)
        self.assertEqual(saved.sync_status, 'failed')

    @patch('store_adapters.get_adapter')
    def test_sheet_batch_marks_scraped_rows_synced_before_a_later_batch_fails(self, get_adapter_mock):
        adapter = MagicMock()

        def _write(_payload, **kwargs):
            kwargs['on_batch_progress'](1, 2, 1, 2, ['KOG-SKU-1'])
            raise RuntimeError('Google Sheet batch 2 failed')

        adapter.update_products_bulk.side_effect = _write
        get_adapter_mock.return_value = adapter

        result = _execute_store_push_listings_only(str(self.store.id), disable_schedule=False)

        self.assertEqual(result.get('pushed'), 1)
        self.assertEqual(result.get('failed'), 2)
        statuses = {
            row.marketplace_child_sku: row.sync_status
            for row in ProductMapping.objects.filter(store=self.store)
        }
        self.assertEqual(statuses['KOG-SKU-1'], 'synced')
        self.assertEqual(statuses['KOG-SKU-2'], 'scraped')
        self.assertEqual(statuses['KOG-SKU-3'], 'scraped')
