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
