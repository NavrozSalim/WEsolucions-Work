"""Auto Purchasing Phases 4-6: laptop Fetch, Process (buy one order), and the retired whole-file runs."""
import hashlib
import io
import shutil
import tempfile
from datetime import timedelta
from unittest import mock

import openpyxl
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from catalog.models import IngestToken
from listings.models import Environment, MarketplaceOrder, OrderPurchaseCheck, OrderStatus
from listings.test_order_file_import import BIGW_CSV, kogan_xlsx
from marketplace.models import Marketplace
from stores.models import AutoOrderRun, PurchaseAccount, Store

User = get_user_model()
_MEDIA = tempfile.mkdtemp(prefix='apj-test-')
NEXT = '/api/v1/ingest/order-bot/next-job/'
ALL_KINDS = {'kinds': 'run,fetch_orders,purchase'}
ASYNC = 'listings.tasks.check_auto_purchase_orders.apply_async'


def xlsx_rows(content: bytes):
    rows = list(openpyxl.load_workbook(io.BytesIO(content)).worksheets[0].iter_rows(values_only=True))
    return [dict(zip(rows[0], r)) for r in rows[1:]]


def result_xlsx(rows):
    cols = ['order_id', 'sku', 'buy_qty', 'vendor', 'vendor_url', 'stock_seen', 'status',
            'vendor_order_number', 'error', 'screenshot']
    wb = openpyxl.Workbook()
    wb.active.append(cols)
    for r in rows:
        wb.active.append([r.get(c, '') for c in cols])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@override_settings(MEDIA_ROOT=_MEDIA, AUTO_ORDER_BOT_RUNNER='laptop', DEBUG=True,
                   ENCRYPTION_KEY=Fernet.generate_key().decode())
class AutoPurchaseJobTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA, ignore_errors=True)

    def setUp(self):
        self.user = User.objects.create_user(username='apj', email='apj@example.com', password='pw12345678')
        self.kogan = Marketplace.objects.get_or_create(code='kogan', defaults={'name': 'Kogan'})[0]
        self.mydeal = Marketplace.objects.get_or_create(code='mydeal', defaults={'name': 'MyDeal'})[0]
        self.store = Store.objects.create(
            user=self.user, name='Kogan-TFS', region='AU', api_token='t', marketplace=self.kogan,
            auto_buy_vendor='amazon_au',
        )
        self.account = PurchaseAccount.objects.create(
            user=self.user, name='au-amazon', vendor='amazon_au', region='AU', chrome_profile='P1',
        )
        self.c = APIClient()
        self.c.force_authenticate(self.user)
        raw = 'laptop-1-' + 'z' * 30
        IngestToken.objects.create(label='laptop-1', token_hash=hashlib.sha256(raw.encode()).hexdigest(),
                                   token_prefix=raw[:8], scopes=['order-bot'], created_by=self.user)
        self.laptop = APIClient()
        self.laptop.credentials(HTTP_AUTHORIZATION=f'Bearer {raw}')

    # ------------------------------------------------------------------ helpers

    def url(self, path=''):
        return f'/api/v1/stores/{self.store.id}/orders/{path}'

    def buyable_order(self, key='TJPQ6RDG', lines=None, checked_ago=timedelta(minutes=5)):
        lines = lines or [
            {'sku': 'TFS-KAZ-1-New', 'quantity': 1, 'priceCents': 5921, 'vendor_url': 'https://www.amazon.com.au/dp/B01',
             'vendor_id': 'B01', 'vendor_price_cents': 3000},
            {'sku': 'TFS-KAZ-2-New', 'quantity': 2, 'priceCents': 1649, 'vendor_url': 'https://www.amazon.com.au/dp/B02',
             'vendor_id': 'B02', 'vendor_price_cents': 800},
        ]
        order = MarketplaceOrder.objects.create(
            store=self.store, user=self.user, external_order_key=key, invoice_number=key,
            environment=Environment.PRODUCTION, status=OrderStatus.PAID,
            customer_info_json={'name': 'Jason Oakley', 'phone': '0411885304'},
            line_items_json=[{'sku': ln['sku'], 'title': ln['sku'], 'quantity': ln['quantity'],
                              'priceCents': ln['priceCents']} for ln in lines],
            raw_response_json={
                'source': 'order_file', 'fileFormat': 'kogan',
                'customer': {'name': 'Jason Oakley', 'phone': '0411885304', 'shippingAddress': {
                    'line1': '24 Marsden Terrace', 'line2': '', 'city': 'Taree', 'state': 'NSW',
                    'postcode': '2430', 'country': 'AU'}},
            },
        )
        OrderPurchaseCheck.objects.create(
            order=order, store=self.store, status='buyable', lines_json=lines,
            checked_at=timezone.now() - checked_ago, earning_cents=1000, cost_cents=4600,
        )
        return order

    def claim(self, kinds=ALL_KINDS):
        return self.laptop.get(NEXT, kinds).data

    def check(self, order):
        return OrderPurchaseCheck.objects.get(order=order)

    def complete(self, job, code=0, done=None, remaining=None, stdout=''):
        data = {'exit_code': code, 'stdout': stdout}
        if done is not None:
            data['done'] = SimpleUploadedFile('kogan-done.xlsx', result_xlsx(done))
        if remaining is not None:
            data['remaining'] = SimpleUploadedFile('kogan-remaining.xlsx', result_xlsx(remaining))
        return self.laptop.post(job['complete_url'], data, format='multipart')

    # ------------------------------------------------------------------ Fetch

    def test_fetch_queues_one_job_per_store(self):
        first = self.c.post(self.url('fetch-file/'))
        self.assertEqual(first.status_code, 202)
        again = self.c.post(self.url('fetch-file/'))
        self.assertEqual(again.status_code, 200)
        self.assertEqual(first.data['fetch']['id'], again.data['fetch']['id'])
        self.assertIn('No laptop is online', first.data['message'])
        run = AutoOrderRun.objects.get(store=self.store)
        self.assertEqual((run.kind, run.status, run.marketplace), ('fetch_orders', 'pending', 'kogan'))

    def test_old_agent_does_not_get_fetch_or_purchase_jobs(self):
        self.c.post(self.url('fetch-file/'))
        self.c.post(self.url(f'{self.buyable_order().id}/process/'))
        self.assertIsNone(self.laptop.get(NEXT).data['job_id'])
        self.assertEqual(AutoOrderRun.objects.filter(status='pending').count(), 2)

    @mock.patch(ASYNC)
    def test_fetch_round_trip_imports_and_checks(self, mock_async):
        self.c.post(self.url('fetch-file/'))
        job = self.claim()
        self.assertEqual(job['kind'], 'fetch_orders')
        self.assertNotIn('files', job)
        self.assertEqual(self.c.get(self.url('fetch-file/')).data['fetch']['status'], 'running')
        with self.captureOnCommitCallbacks(execute=True):
            up = self.laptop.post(job['upload_url'], {'file': SimpleUploadedFile('OpenOrders.xlsx', kogan_xlsx())},
                                  format='multipart')
        self.assertEqual(up.status_code, 200, up.data)
        self.assertEqual(up.data['orders'], 3)
        self.assertNotIn('order_ids', up.data)
        mock_async.assert_called_once()
        self.assertEqual(MarketplaceOrder.objects.filter(store=self.store).count(), 3)
        self.complete(job, 0, stdout=up.data['message'])
        status = self.c.get(self.url('fetch-file/')).data['fetch']
        self.assertEqual(status['status'], 'finished')
        self.assertIn('Fetched OpenOrders.xlsx', status['message'])
        self.assertIn('Checking stock and profit for 3 order(s)', status['message'])

    def test_fetch_upload_of_wrong_file_is_refused(self):
        self.c.post(self.url('fetch-file/'))
        job = self.claim()
        up = self.laptop.post(job['upload_url'], {'file': SimpleUploadedFile('p.csv', BIGW_CSV.encode())},
                              format='multipart')
        self.assertEqual(up.status_code, 400)
        self.assertIn('BigW orders file', up.data['error'])
        self.complete(job, 1, stdout='Upload refused: ' + up.data['error'])
        status = self.c.get(self.url('fetch-file/')).data['fetch']
        self.assertEqual(status['status'], 'failed')
        self.assertIn('could not be imported', status['message'])

    def test_fetch_needs_auto_purchase_store_and_supported_marketplace(self):
        self.store.auto_buy_vendor = ''
        self.store.save()
        self.assertEqual(self.c.post(self.url('fetch-file/')).status_code, 400)
        reverb = Marketplace.objects.get_or_create(code='reverb', defaults={'name': 'Reverb'})[0]
        us = Store.objects.create(user=self.user, name='R', region='USA', api_token='t', marketplace=reverb,
                                  auto_buy_vendor='amazon')
        res = self.c.post(f'/api/v1/stores/{us.id}/orders/fetch-file/')
        self.assertEqual(res.status_code, 400)
        self.assertIn('Kogan and BigW', res.data['detail'])

    # ------------------------------------------------------------------ Process

    def test_process_writes_one_order_files_with_max_price(self):
        order = self.buyable_order()
        res = self.c.post(self.url(f'{order.id}/process/'))
        self.assertEqual(res.status_code, 202, res.data)
        self.assertEqual(res.data['purchase_check']['purchase_status'], 'queued')
        job = self.claim()
        self.assertEqual((job['kind'], job['place'], job['account'], job['order_id']),
                         ('purchase', True, 'au-amazon', str(order.id)))
        self.assertEqual(self.check(order).purchase_status, 'purchasing')

        orders = xlsx_rows(b''.join(self.laptop.get(job['files']['orders']).streaming_content))
        self.assertEqual([(r['OrderID'], r['ProductCode'], r['Quantity']) for r in orders],
                         [('TJPQ6RDG', 'TFS-KAZ-1-New', 1), ('TJPQ6RDG', 'TFS-KAZ-2-New', 2)])
        self.assertEqual((orders[0]['DeliveryName'], orders[0]['DeliverySuburb'], orders[0]['DeliveryPostCode']),
                         ('Jason Oakley', 'Taree', '2430'))
        mapping = xlsx_rows(b''.join(self.laptop.get(job['files']['mapping']).streaming_content))
        self.assertEqual([(r['SKU'], r['Vendor'], r['Max Price']) for r in mapping],
                         [('TFS-KAZ-1-New', 'amazon_au', '30.00'), ('TFS-KAZ-2-New', 'amazon_au', '8.00')])
        self.assertEqual(self.laptop.get(job['files']['accounts']).status_code, 200)

    def test_purchased_result_saves_vendor_order_number(self):
        order = self.buyable_order()
        self.c.post(self.url(f'{order.id}/process/'))
        job = self.claim()
        self.complete(job, 0, done=[
            {'order_id': 'TJPQ6RDG', 'sku': 'TFS-KAZ-1-New', 'status': 'purchased', 'vendor_order_number': '249-1'},
            {'order_id': 'TJPQ6RDG', 'sku': 'TFS-KAZ-2-New', 'status': 'purchased', 'vendor_order_number': '249-1'},
        ], remaining=[])
        c = self.check(order)
        self.assertEqual((c.purchase_status, c.vendor_order_number, c.purchase_error), ('purchased', '249-1', ''))
        self.assertIsNotNone(c.purchased_at)
        listed = {o['invoice_number']: o for o in self.c.get(self.url()).data['orders']}
        self.assertEqual(listed['TJPQ6RDG']['purchase_check']['vendor_order_number'], '249-1')

    def test_price_went_up_is_a_failed_purchase_that_can_be_retried(self):
        order = self.buyable_order()
        self.c.post(self.url(f'{order.id}/process/'))
        job = self.claim()
        self.complete(job, 0, done=[], remaining=[
            {'order_id': 'TJPQ6RDG', 'sku': 'TFS-KAZ-1-New', 'status': 'price_changed',
             'error': 'vendor price 34.00 is above the checked price 30.00'},
            {'order_id': 'TJPQ6RDG', 'sku': 'TFS-KAZ-2-New', 'status': 'skipped_status'},
        ])
        c = self.check(order)
        self.assertEqual(c.purchase_status, 'failed')
        self.assertIn('price_changed', c.purchase_error)
        self.assertIn('34.00', c.purchase_error)
        self.assertEqual(self.c.post(self.url(f'{order.id}/process/')).status_code, 202)

    def test_partly_bought_order_is_locked(self):
        order = self.buyable_order()
        self.c.post(self.url(f'{order.id}/process/'))
        job = self.claim()
        self.complete(job, 0, done=[
            {'order_id': 'TJPQ6RDG', 'sku': 'TFS-KAZ-1-New', 'status': 'purchased', 'vendor_order_number': '249-9'},
        ], remaining=[
            {'order_id': 'TJPQ6RDG', 'sku': 'TFS-KAZ-2-New', 'status': 'out_of_stock', 'error': 'need 2'},
        ])
        c = self.check(order)
        self.assertEqual(c.purchase_status, 'purchased')
        self.assertIn('Only some items were bought', c.purchase_error)
        self.assertEqual(self.c.post(self.url(f'{order.id}/process/')).status_code, 409)

    def test_crashed_job_marks_purchase_failed(self):
        order = self.buyable_order()
        self.c.post(self.url(f'{order.id}/process/'))
        self.complete(self.claim(), 3, stdout='Chrome did not open debug port 9222')
        c = self.check(order)
        self.assertEqual(c.purchase_status, 'failed')
        self.assertIn('Chrome did not open', c.purchase_error)

    def test_line_the_bot_never_finished_says_so_with_its_log(self):
        order = self.buyable_order()
        self.c.post(self.url(f'{order.id}/process/'))
        self.complete(self.claim(), 1, done=[], remaining=[
            {'order_id': 'TJPQ6RDG', 'sku': 'TFS-KAZ-1-New', 'status': ''},
            {'order_id': 'TJPQ6RDG', 'sku': 'TFS-KAZ-2-New', 'status': ''},
        ], stdout='order TJPQ6RDG sku TFS-KAZ-1-New\nTargetClosedError: Target page has been closed')
        c = self.check(order)
        self.assertEqual(c.purchase_status, 'failed')
        self.assertIn('stopped before finishing', c.purchase_error)
        self.assertIn('Target page has been closed', c.purchase_error)

    def test_order_cannot_be_sent_to_buy_twice(self):
        order = self.buyable_order()
        self.assertEqual(self.c.post(self.url(f'{order.id}/process/')).status_code, 202)
        second = self.c.post(self.url(f'{order.id}/process/'))
        self.assertEqual(second.status_code, 409)
        self.assertEqual(AutoOrderRun.objects.filter(kind='purchase').count(), 1)

    @mock.patch(ASYNC)
    def test_recheck_skips_orders_being_bought(self, mock_async):
        order = self.buyable_order()
        self.c.post(self.url(f'{order.id}/process/'))
        with self.captureOnCommitCallbacks(execute=True):
            res = self.c.post(self.url('purchase-check/'))
        self.assertEqual(res.data['queued'], 0)
        mock_async.assert_not_called()
        self.assertEqual(self.check(order).status, 'buyable')

    def test_process_refuses_blocked_stale_or_no_account(self):
        blocked = self.buyable_order(key='B1')
        OrderPurchaseCheck.objects.filter(order=blocked).update(status='blocked')
        self.assertEqual(self.c.post(self.url(f'{blocked.id}/process/')).status_code, 409)

        stale = self.buyable_order(key='S1', checked_ago=timedelta(hours=3))
        res = self.c.post(self.url(f'{stale.id}/process/'))
        self.assertEqual(res.status_code, 409)
        self.assertIn('Recheck', res.data['detail'])

        never = MarketplaceOrder.objects.create(store=self.store, user=self.user, external_order_key='N1',
                                                environment=Environment.PRODUCTION, line_items_json=[])
        self.assertEqual(self.c.post(self.url(f'{never.id}/process/')).status_code, 409)

        self.account.delete()
        res = self.c.post(self.url(f'{self.buyable_order(key="A1").id}/process/'))
        self.assertEqual(res.status_code, 400)
        self.assertIn('purchase account for Amazon AU', res.data['detail'])
        self.assertFalse(AutoOrderRun.objects.filter(kind='purchase').exists())

    def test_stale_purchase_job_warns_before_buying_again(self):
        from stores import auto_order_bot as bot

        order = self.buyable_order()
        self.c.post(self.url(f'{order.id}/process/'))
        AutoOrderRun.objects.filter(kind='purchase').update(started_at=timezone.now() - timedelta(hours=13))
        bot.expire_stale(AutoOrderRun.objects.filter(store=self.store))
        c = self.check(order)
        self.assertEqual(c.purchase_status, 'failed')
        self.assertIn('Check the vendor account', c.purchase_error)

    def test_bigw_order_file_layout(self):
        store = Store.objects.create(user=self.user, name='BW', region='AU', api_token='t', marketplace=self.mydeal,
                                     auto_buy_vendor='amazon_au')
        self.store = store
        order = self.buyable_order(key='1023584140')
        MarketplaceOrder.objects.filter(id=order.id).update(store=store)
        OrderPurchaseCheck.objects.filter(order=order).update(store=store)
        res = self.c.post(f'/api/v1/stores/{store.id}/orders/{order.id}/process/')
        self.assertEqual(res.status_code, 202, res.data)
        job = self.claim()
        self.assertEqual(job['marketplace'], 'bigw')
        rows = xlsx_rows(b''.join(self.laptop.get(job['files']['orders']).streaming_content))
        self.assertEqual((rows[0]['Order No'], rows[0]['Status'], rows[0]['Suburb']), ('1023584140', 'Purchased', 'Taree'))

    # ------------------------------------------------------------------ Phase 6

    def test_whole_file_runs_are_retired(self):
        res = self.c.post(f'/api/v1/stores/{self.store.id}/auto-order-bot/runs/', {'account': 'au-amazon'})
        self.assertEqual(res.status_code, 410)
        self.assertIn('Orders page', res.data['detail'])
        up = self.c.post(f'/api/v1/stores/{self.store.id}/auto-order-bot/files/orders/',
                         {'file': SimpleUploadedFile('o.xlsx', kogan_xlsx())}, format='multipart')
        self.assertEqual(up.status_code, 410)
        info = self.c.get(f'/api/v1/stores/{self.store.id}/auto-order-bot/').data
        self.assertEqual((info['legacy_runs'], info['auto_buy_vendor'], info['auto_buy_label']),
                         (False, 'amazon_au', 'Amazon AU'))

    def test_bot_page_lists_laptop_jobs(self):
        self.c.post(self.url('fetch-file/'))
        runs = self.c.get(f'/api/v1/stores/{self.store.id}/auto-order-bot/runs/').data['runs']
        self.assertEqual(runs[0]['kind'], 'fetch_orders')
