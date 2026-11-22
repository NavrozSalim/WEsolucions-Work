"""Orders page manual fallbacks: Mark as purchased, Uploaded on marketplace tickbox, API stores' check."""
from datetime import timedelta
from unittest import mock

from cryptography.fernet import Fernet
from django.test import TestCase, override_settings
from django.utils import timezone

from listings import test_auto_purchase_jobs as _apj
from listings.models import (
    MarketplaceOrder, OrderPurchaseCheck, OrderStatus, OrderTracking, PurchaseStatus,
)
from listings.order_tracking import can_upload
from marketplace.models import Marketplace
from stores.models import Store



SETTINGS = override_settings(MEDIA_ROOT=_apj._MEDIA, AUTO_ORDER_BOT_RUNNER='laptop', DEBUG=True,
                             ENCRYPTION_KEY=Fernet.generate_key().decode())


@SETTINGS
class MarkPurchasedTests(TestCase):
    setUp = _apj.AutoPurchaseJobTests.setUp
    url = _apj.AutoPurchaseJobTests.url
    buyable_order = _apj.AutoPurchaseJobTests.buyable_order

    def test_mark_purchased_saves_number_and_blocks_the_bot(self):
        order = self.buyable_order()
        r = self.c.post(self.url(f'{order.id}/mark-purchased/'), {'vendor_order_number': ' 503-1 '}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        check = OrderPurchaseCheck.objects.get(order=order)
        self.assertEqual((check.purchase_status, check.vendor_order_number, check.purchased_manually),
                         (PurchaseStatus.PURCHASED, '503-1', True))
        self.assertTrue(r.data['purchase_check']['purchased_manually'])
        # The order bot will not buy it again.
        r = self.c.post(self.url(f'{order.id}/process/'), {}, format='json')
        self.assertEqual(r.status_code, 409)
        # A second Mark as purchased is refused (edit the number instead).
        r = self.c.post(self.url(f'{order.id}/mark-purchased/'), {'vendor_order_number': 'X'}, format='json')
        self.assertEqual(r.status_code, 409)

    def test_mark_purchased_works_on_an_order_never_checked(self):
        order = self.buyable_order()
        OrderPurchaseCheck.objects.filter(order=order).delete()
        r = self.c.post(self.url(f'{order.id}/mark-purchased/'), {'vendor_order_number': '503-2'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(OrderPurchaseCheck.objects.get(order=order).purchase_status, PurchaseStatus.PURCHASED)

    def test_mark_purchased_needs_a_number_and_an_open_order(self):
        order = self.buyable_order()
        r = self.c.post(self.url(f'{order.id}/mark-purchased/'), {'vendor_order_number': ' '}, format='json')
        self.assertEqual(r.status_code, 400)
        MarketplaceOrder.objects.filter(pk=order.pk).update(status=OrderStatus.CANCELLED)
        r = self.c.post(self.url(f'{order.id}/mark-purchased/'), {'vendor_order_number': '1'}, format='json')
        self.assertEqual(r.status_code, 409)

    def test_mark_purchased_refused_while_the_laptop_buys(self):
        order = self.buyable_order()
        OrderPurchaseCheck.objects.filter(order=order).update(purchase_status=PurchaseStatus.QUEUED)
        r = self.c.post(self.url(f'{order.id}/mark-purchased/'), {'vendor_order_number': '1'}, format='json')
        self.assertEqual(r.status_code, 409)


@SETTINGS
class MarkUploadedTests(TestCase):
    setUp = _apj.AutoPurchaseJobTests.setUp
    url = _apj.AutoPurchaseJobTests.url
    buyable_order = _apj.AutoPurchaseJobTests.buyable_order

    def _tracked(self):
        order = self.buyable_order()
        OrderPurchaseCheck.objects.filter(order=order).update(
            purchase_status=PurchaseStatus.PURCHASED, vendor_order_number='503-1',
        )
        t = OrderTracking.objects.create(order=order, tracking_number='TRK1', carrier='AusPost')
        return order, t

    def test_tick_and_untick(self):
        order, t = self._tracked()
        self.assertTrue(can_upload(order, t))
        r = self.c.post(self.url(f'{order.id}/mark-uploaded/'), {'tracking_id': str(t.id)}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        t.refresh_from_db()
        self.assertEqual((t.upload_status, t.upload_marked_by), ('manual', self.user))
        row = r.data['tracking']['current'][0]
        self.assertEqual((row['upload_status'], row['can_upload']), ('manual', False))
        # Upload button refused: already uploaded by hand.
        r = self.c.post(self.url(f'{order.id}/upload-tracking/'), {'tracking_id': str(t.id)}, format='json')
        self.assertEqual(r.status_code, 409)

        r = self.c.delete(self.url(f'{order.id}/mark-uploaded/') + f'?tracking_id={t.id}')
        self.assertEqual(r.status_code, 200, r.data)
        t.refresh_from_db()
        self.assertEqual((t.upload_status, t.upload_marked_by), ('', None))
        self.assertTrue(can_upload(order, t))

    def test_tick_on_unknown_number(self):
        order, _ = self._tracked()
        r = self.c.post(self.url(f'{order.id}/mark-uploaded/'), {'tracking_id': ''}, format='json')
        self.assertEqual(r.status_code, 409)


@SETTINGS
class ApiStoreAutoCheckTests(TestCase):
    """API-connected auto-buy store: new open orders go to the stock and profit check after Fetch."""

    def setUp(self):
        _apj.AutoPurchaseJobTests.setUp(self)
        self.reverb = Marketplace.objects.get_or_create(code='reverb', defaults={'name': 'Reverb'})[0]
        self.api_store = Store.objects.create(
            user=self.user, name='Reverb-API', region='AU', api_token='t', marketplace=self.reverb,
            auto_buy_vendor='amazon_au', management_mode='full_store',
        )

    def _order(self, key, status=OrderStatus.PAID, **kw):
        return MarketplaceOrder.objects.create(
            store=self.api_store, user=self.user, external_order_key=key, invoice_number=key, status=status, **kw,
        )

    @mock.patch(_apj.ASYNC)
    def test_new_open_orders_are_checked_after_fetch(self, mock_async):
        from listings import order_service

        new = self._order('N1')
        self._order('C1', status=OrderStatus.CANCELLED)
        self._order('H1', is_history=True)
        old = self._order('O1')
        MarketplaceOrder.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=10))
        with mock.patch.object(order_service, '_fetch_from_marketplace', return_value={'ok': True, 'fetched': 4}), \
                self.captureOnCommitCallbacks(execute=True):
            result = order_service.fetch(self.user, self.api_store)
        self.assertEqual(result['checking'], 1)
        mock_async.assert_called_once()
        self.assertEqual(mock_async.call_args.kwargs['args'][1], [str(new.id)])

        # Already checked orders are not sent again.
        mock_async.reset_mock()
        with mock.patch.object(order_service, '_fetch_from_marketplace', return_value={'ok': True, 'fetched': 0}), \
                self.captureOnCommitCallbacks(execute=True):
            result = order_service.fetch(self.user, self.api_store)
        self.assertNotIn('checking', result)
        mock_async.assert_not_called()

    @mock.patch(_apj.ASYNC)
    def test_store_without_auto_buy_vendor_is_not_checked(self, mock_async):
        from listings import order_service

        Store.objects.filter(pk=self.api_store.pk).update(auto_buy_vendor='')
        self.api_store.refresh_from_db()
        self._order('N1')
        with mock.patch.object(order_service, '_fetch_from_marketplace', return_value={'ok': True}), \
                self.captureOnCommitCallbacks(execute=True):
            order_service.fetch(self.user, self.api_store)
        mock_async.assert_not_called()


@SETTINGS
class SelectedOrdersTests(TestCase):
    """Checkbox selection: Process, Recheck and Get tracking on the selected orders only."""
    setUp = _apj.AutoPurchaseJobTests.setUp
    url = _apj.AutoPurchaseJobTests.url
    buyable_order = _apj.AutoPurchaseJobTests.buyable_order

    def test_process_selected_queues_only_the_selected_ready_orders(self):
        a, b, c = self.buyable_order('A1'), self.buyable_order('B1'), self.buyable_order('C1')
        OrderPurchaseCheck.objects.filter(order=c).update(status='blocked')
        r = self.c.post(self.url('process-all/'), {'order_ids': [str(a.id), str(c.id)]}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['queued'], 1)
        self.assertIn('1 selected order(s) skipped', r.data['message'])
        statuses = dict(OrderPurchaseCheck.objects.values_list('order__invoice_number', 'purchase_status'))
        self.assertEqual(statuses, {'A1': PurchaseStatus.QUEUED, 'B1': '', 'C1': ''})

    @mock.patch(_apj.ASYNC)
    def test_recheck_selected_checks_only_the_selected_orders(self, mock_async):
        a, b = self.buyable_order('A1'), self.buyable_order('B1')
        with self.captureOnCommitCallbacks(execute=True):
            r = self.c.post(self.url('purchase-check/'), {'order_ids': [str(b.id)]}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['queued'], 1)
        self.assertEqual(mock_async.call_args.kwargs['args'][1], [str(b.id)])

    def test_get_tracking_selected_only_sends_trackable_orders(self):
        a, b = self.buyable_order('A1'), self.buyable_order('B1')
        OrderPurchaseCheck.objects.filter(order=a).update(
            purchase_status=PurchaseStatus.PURCHASED, vendor_order_number='503-1', purchased_at=timezone.now(),
        )
        r = self.c.post(self.url('get-tracking/'), {'order_ids': [str(a.id), str(b.id)]}, format='json')
        self.assertEqual(r.status_code, 202, r.data)
        self.assertEqual(r.data['queued'], 1)
        self.assertEqual(OrderPurchaseCheck.objects.get(order=a).tracking_status, 'queued')
        r = self.c.post(self.url('get-tracking/'), {'order_ids': [str(b.id)]}, format='json')
        self.assertEqual((r.status_code, r.data['queued']), (200, 0))
