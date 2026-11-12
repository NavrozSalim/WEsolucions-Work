"""Orders round 2: vendor shipping in the earning, Force process, Process all, old-order upload,
and keeping a deleted store's orders for 7 days."""
from datetime import timedelta
from unittest import mock

from cryptography.fernet import Fernet
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from listings.auto_purchase_check import check_order
from listings.models import MarketplaceOrder, OrderPurchaseCheck
from listings.order_retention import detach_store_orders, purge_expired_detached_orders
from listings import test_auto_purchase_check as _apc
from listings import test_auto_purchase_jobs as _apj
from listings.test_order_file_import import BIGW_CSV
from stores.models import AutoOrderRun, Store


class ShippingTests(TestCase):
    setUp = _apc.PurchaseCheckTests.setUp
    _map = _apc.PurchaseCheckTests._map
    line = staticmethod(_apc.PurchaseCheckTests.line)

    def _order(self, lines, postcode='2603'):
        return MarketplaceOrder.objects.create(
            store=self.store, user=self.user, external_order_key='S1', invoice_number='S1',
            environment='production', status='paid', line_items_json=lines,
            customer_info_json={'name': 'A', 'shippingAddress': {'postcode': postcode}},
            raw_response_json={'source': 'order_file'},
        )

    @mock.patch(_apc.LIVE, return_value=_apc.live('20.99', 5, shipping='6.50'))
    def test_vendor_shipping_is_taken_off_the_earning(self, _live):
        self._map('SKU-A')
        check = check_order(self._order([self.line('SKU-A', 3848)]))
        # 3848 - 577 fee - 2099 cost - 650 shipping
        self.assertEqual((check.shipping_cents, check.earning_cents), (650, 522))
        ln = check.lines_json[0]
        self.assertEqual((ln['postcode'], ln['shipping_cents'], ln['shipping_source']), ('2603', 650, 'vendor'))

    @mock.patch(_apc.LIVE, return_value=_apc.live('20.99', 5))
    def test_no_vendor_shipping_counts_as_zero(self, _live):
        self._map('SKU-A')
        check = check_order(self._order([self.line('SKU-A', 3848)]))
        self.assertEqual((check.shipping_cents, check.earning_cents), (0, 1172))
        self.assertEqual(check.lines_json[0]['shipping_source'], 'none')

    @mock.patch(_apc.LIVE, return_value=_apc.live('20.99', 5, shipping='15.00'))
    def test_shipping_can_turn_the_order_into_a_loss(self, _live):
        self._map('SKU-A')
        check = check_order(self._order([self.line('SKU-A', 3848)]))
        self.assertEqual(check.status, 'blocked')
        self.assertEqual(check.earning_cents, -328)
        self.assertTrue(check.reasons[0].startswith('Loss:'))


class ForceAndProcessAllTests(TestCase):
    setUp = _apj.AutoPurchaseJobTests.setUp
    url = _apj.AutoPurchaseJobTests.url
    buyable_order = _apj.AutoPurchaseJobTests.buyable_order

    def loss_order(self, key='LOSS1', reasons=None):
        order = self.buyable_order(key=key)
        OrderPurchaseCheck.objects.filter(order=order).update(
            status='blocked', earning_cents=-250,
            reasons=reasons or ['Loss: earning -2.50'],
            lines_json=[{**ln, 'result': 'ok'} for ln in order.purchase_check.lines_json],
        )
        order.refresh_from_db()
        return order

    def test_process_refuses_a_loss_order_without_force(self):
        order = self.loss_order()
        r = self.c.post(self.url(f'{order.id}/process/'), {}, format='json')
        self.assertEqual(r.status_code, 409)
        self.assertTrue(order.purchase_check.lines_json)
        self.assertTrue(r.data['detail'])

    def test_force_buys_a_loss_only_order_and_records_it(self):
        order = self.loss_order()
        self.assertTrue(self.c.get(self.url()).data['orders'][0]['purchase_check']['can_force'])
        r = self.c.post(self.url(f'{order.id}/process/'), {'force': True}, format='json')
        self.assertEqual(r.status_code, 202, r.data)
        check = OrderPurchaseCheck.objects.get(order=order)
        self.assertEqual(check.purchase_status, 'queued')
        self.assertIsNotNone(check.forced_at)
        self.assertEqual(check.forced_loss_cents, -250)
        self.assertIn('forced at a loss', r.data['message'])

    def test_force_does_not_bypass_other_errors(self):
        order = self.loss_order(reasons=['Loss: earning -2.50', 'Out of stock: X (Vendor stock 0, order needs 1.)'])
        r = self.c.post(self.url(f'{order.id}/process/'), {'force': True}, format='json')
        self.assertEqual(r.status_code, 409)
        self.assertIn('only problem is a loss', r.data['detail'])

    def test_process_all_queues_only_buyable_orders(self):
        a = self.buyable_order(key='A1')
        b = self.buyable_order(key='B1')
        self.loss_order(key='L1')
        old = self.buyable_order(key='OLD1', checked_ago=timedelta(days=1))
        r = self.c.post(self.url('process-all/'), {}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['queued'], 2)
        self.assertEqual(len(r.data['not_sent']), 1)  # the stale check
        self.assertEqual(
            AutoOrderRun.objects.filter(kind='purchase').count(), 2,
        )
        for o in (a, b):
            self.assertEqual(OrderPurchaseCheck.objects.get(order=o).purchase_status, 'queued')
        self.assertEqual(OrderPurchaseCheck.objects.get(order=old).purchase_status, '')
        # A second click does not buy anything twice.
        self.assertEqual(self.c.post(self.url('process-all/'), {}, format='json').data['queued'], 0)


class HistoryUploadTests(TestCase):
    setUp = _apj.AutoPurchaseJobTests.setUp
    url = _apj.AutoPurchaseJobTests.url

    def bigw_store(self):
        return Store.objects.create(
            user=self.user, name='BW-P&P', region='AU', api_token='t', marketplace=self.mydeal,
            auto_buy_vendor='amazon_au',
        )

    @mock.patch(_apj.ASYNC)
    def test_old_orders_are_kept_but_never_checked_or_bought(self, mock_async):
        store = self.bigw_store()
        with self.captureOnCommitCallbacks(execute=True):
            r = self.c.post(f'/api/v1/stores/{store.id}/orders/import-file/', {
                'file': SimpleUploadedFile('old.csv', BIGW_CSV.encode()), 'history': '1',
            }, format='multipart')
        self.assertEqual(r.status_code, 200, r.data)
        mock_async.assert_not_called()
        orders = MarketplaceOrder.objects.filter(store=store)
        self.assertTrue(orders.exists())
        self.assertTrue(all(o.is_history for o in orders))
        self.assertFalse(OrderPurchaseCheck.objects.filter(order__store=store).exists())
        # Recheck all skips them.
        with self.captureOnCommitCallbacks(execute=True):
            self.c.post(f'/api/v1/stores/{store.id}/orders/purchase-check/', {}, format='json')
        mock_async.assert_not_called()

    def test_old_bigw_file_keeps_rows_in_every_status(self):
        store = self.bigw_store()
        normal = self.c.post(f'/api/v1/stores/{store.id}/orders/import-file/', {
            'file': SimpleUploadedFile('o.csv', BIGW_CSV.encode())}, format='multipart').data
        MarketplaceOrder.objects.filter(store=store).update(store=None, detached_at=timezone.now())
        history = self.c.post(f'/api/v1/stores/{store.id}/orders/import-file/', {
            'file': SimpleUploadedFile('o.csv', BIGW_CSV.encode()), 'history': '1'}, format='multipart').data
        self.assertGreaterEqual(history['lines'], normal['lines'])
        self.assertEqual(history['skipped'], [])


class DeletedStoreOrdersTests(TestCase):
    setUp = _apj.AutoPurchaseJobTests.setUp
    buyable_order = _apj.AutoPurchaseJobTests.buyable_order

    def test_deleting_a_store_keeps_its_orders_for_seven_days(self):
        order = self.buyable_order(key='K1')
        r = self.c.delete(f'/api/v1/stores/{self.store.id}/')
        self.assertEqual(r.status_code, 204)
        order.refresh_from_db()
        self.assertIsNone(order.store_id)
        self.assertEqual(order.detached_store_name, 'Kogan-TFS')
        self.assertTrue(OrderPurchaseCheck.objects.filter(order=order).exists())

        new_store = Store.objects.create(user=self.user, name='Kogan-New', region='AU', api_token='t',
                                         marketplace=self.kogan, auto_buy_vendor='amazon_au')
        url = f'/api/v1/stores/{new_store.id}/orders/detached/'
        groups = self.c.get(url).data['groups']
        self.assertEqual([(g['store_name'], g['count']) for g in groups], [('Kogan-TFS', 1)])
        r = self.c.post(url, {'store_name': 'Kogan-TFS'}, format='json')
        self.assertEqual(r.data['linked'], 1)
        order.refresh_from_db()
        self.assertEqual(order.store_id, new_store.id)
        self.assertIsNone(order.detached_at)
        self.assertEqual(OrderPurchaseCheck.objects.get(order=order).store_id, new_store.id)
        self.assertEqual(self.c.get(url).data['groups'], [])

    def test_orders_are_purged_only_after_the_window(self):
        order = self.buyable_order(key='K2')
        detach_store_orders(self.store)
        self.store.delete()
        self.assertEqual(purge_expired_detached_orders(), 0)
        MarketplaceOrder.objects.filter(id=order.id).update(detached_at=timezone.now() - timedelta(days=8))
        self.assertGreater(purge_expired_detached_orders(), 0)
        self.assertFalse(MarketplaceOrder.objects.filter(id=order.id).exists())

    def test_other_users_cannot_see_or_link_the_orders(self):
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIClient

        self.buyable_order(key='K3')
        detach_store_orders(self.store)
        self.store.delete()
        other = get_user_model().objects.create_user(username='o', email='o@example.com', password='pw12345678')
        their = Store.objects.create(user=other, name='X', region='AU', api_token='t', marketplace=self.kogan)
        c = APIClient()
        c.force_authenticate(other)
        url = f'/api/v1/stores/{their.id}/orders/detached/'
        self.assertEqual(c.get(url).data['groups'], [])
        self.assertEqual(c.post(url, {'store_name': 'Kogan-TFS'}, format='json').data['linked'], 0)


_JOB_SETTINGS = override_settings(
    MEDIA_ROOT=_apj._MEDIA, AUTO_ORDER_BOT_RUNNER='laptop', DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode(),
)
ShippingTests = _JOB_SETTINGS(ShippingTests)
ForceAndProcessAllTests = _JOB_SETTINGS(ForceAndProcessAllTests)
HistoryUploadTests = _JOB_SETTINGS(HistoryUploadTests)
DeletedStoreOrdersTests = _JOB_SETTINGS(DeletedStoreOrdersTests)
