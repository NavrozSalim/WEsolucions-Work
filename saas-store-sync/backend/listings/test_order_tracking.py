"""Tracking Bot: queue purchased orders, the laptop reads Amazon tracking, Replace, and the Chrome profile lock."""
import hashlib
import io
import shutil
import tempfile
from datetime import timedelta

import openpyxl
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from catalog.models import IngestToken
from listings.models import (
    Environment, MarketplaceOrder, OrderPurchaseCheck, OrderStatus, OrderTracking, PurchaseStatus,
)
from listings.order_tracking import due_checks, start_tracking
from marketplace.models import Marketplace
from stores.models import AutoOrderRun, PurchaseAccount, Store

User = get_user_model()
_MEDIA = tempfile.mkdtemp(prefix='trk-test-')
NEXT = '/api/v1/ingest/order-bot/next-job/'
TRACKING = {'kinds': 'tracking'}
ORDER_BOT = {'kinds': 'run,fetch_orders,purchase,lookup_order'}
COLS = ['order_ref', 'vendor_order_number', 'status', 'tracking_number', 'carrier', 'ship_name', 'name_ok', 'error']


def done_xlsx(rows):
    wb = openpyxl.Workbook()
    wb.active.append(COLS)
    for r in rows:
        wb.active.append([str(r.get(c, '')) for c in COLS])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@override_settings(MEDIA_ROOT=_MEDIA, AUTO_ORDER_BOT_RUNNER='laptop', DEBUG=True,
                   ENCRYPTION_KEY=Fernet.generate_key().decode())
class OrderTrackingTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA, ignore_errors=True)

    def setUp(self):
        self.user = User.objects.create_user(username='trk', email='trk@example.com', password='pw12345678')
        kogan = Marketplace.objects.get_or_create(code='kogan', defaults={'name': 'Kogan'})[0]
        self.store = Store.objects.create(
            user=self.user, name='Kogan-TFS', region='AU', api_token='t', marketplace=kogan,
            auto_buy_vendor='amazon_au',
        )
        self.account = PurchaseAccount.objects.create(
            user=self.user, name='Amazon AU', vendor='amazon_au', region='AU', chrome_profile='P1',
        )
        self.c = APIClient()
        self.c.force_authenticate(self.user)
        raw = 'laptop-1-' + 'z' * 30
        IngestToken.objects.create(label='laptop-1', token_hash=hashlib.sha256(raw.encode()).hexdigest(),
                                   token_prefix=raw[:8], scopes=['order-bot'], created_by=self.user)
        self.laptop = APIClient()
        self.laptop.credentials(HTTP_AUTHORIZATION=f'Bearer {raw}')

    def purchased(self, key='K1', number='503-2272946-3274202', days_ago=2, name='Elena Legkova', vendor=None):
        order = MarketplaceOrder.objects.create(
            store=self.store, user=self.user, external_order_key=key, invoice_number=key,
            environment=Environment.PRODUCTION, status=OrderStatus.PAID,
            customer_info_json={'name': name}, line_items_json=[{'sku': 'S1', 'quantity': 1}],
            raw_response_json={'source': 'order_file', 'customer': {'name': name}},
        )
        lines = [{'sku': 'S1', 'quantity': 1}] + ([{'sku': 'S1', 'auto_buy_vendor': vendor}] if vendor else [])
        OrderPurchaseCheck.objects.create(
            order=order, store=self.store, status='buyable', lines_json=lines,
            purchase_status=PurchaseStatus.PURCHASED, vendor_order_number=number,
            purchased_at=timezone.now() - timedelta(days=days_ago),
        )
        return order

    def check(self, order):
        return OrderPurchaseCheck.objects.get(order=order)

    def url(self, path=''):
        return f'/api/v1/stores/{self.store.id}/orders/{path}'

    def complete(self, job, rows, code=0, stdout=''):
        return self.laptop.post(job['complete_url'], {
            'exit_code': code, 'stdout': stdout,
            'done': SimpleUploadedFile('kogan-done.xlsx', done_xlsx(rows)),
        }, format='multipart')

    # ------------------------------------------------------------------ which orders

    def test_due_orders(self):
        due = self.purchased('DUE')
        self.purchased('OLD', days_ago=40)
        self.purchased('UNREAD', number='UNREAD')
        self.purchased('NONUM', number='')
        self.purchased('COSTCO', vendor='costco_au')
        mine = self.purchased('MINE')
        OrderTracking.objects.create(order=mine, tracking_number='X1', source=OrderTracking.Source.USER)
        found = self.purchased('FOUND')
        OrderPurchaseCheck.objects.filter(order=found).update(tracking_status='found')
        self.assertEqual([c.order_id for c in due_checks(self.store)], [due.id])

    def test_get_tracking_queues_one_job_with_the_orders_file(self):
        a = self.purchased('A')
        b = self.purchased('B', number='503-1, 503-2')
        res = self.c.post(self.url('get-tracking/'))
        self.assertEqual(res.status_code, 202, res.data)
        self.assertEqual(res.data['queued'], 2)
        run = AutoOrderRun.objects.get(kind='tracking')
        self.assertEqual(run.account_name, 'Amazon AU')
        self.assertEqual(self.check(a).tracking_status, 'queued')
        job = self.laptop.get(NEXT, TRACKING).data
        self.assertEqual(job['kind'], 'tracking')
        rows = list(openpyxl.load_workbook(io.BytesIO(b''.join(
            self.laptop.get(job['files']['orders']).streaming_content))).worksheets[0].iter_rows(values_only=True))
        by_ref = {r[0]: dict(zip(rows[0], r)) for r in rows[1:]}
        self.assertEqual(by_ref[str(b.id)]['vendor_order_number'], '503-1, 503-2')
        self.assertEqual(by_ref[str(a.id)]['customer_name'], 'Elena Legkova')
        # A second click while it waits queues nothing more.
        res = self.c.post(self.url('get-tracking/'))
        self.assertEqual(res.data['queued'], 0)

    def test_order_bot_agent_does_not_take_tracking_jobs(self):
        self.purchased('A')
        start_tracking(self.store)
        self.assertIsNone(self.laptop.get(NEXT, ORDER_BOT).data['job_id'])

    # ------------------------------------------------------------------ results

    def test_found_not_shipped_partly_and_name_check(self):
        a = self.purchased('A')
        b = self.purchased('B')
        c = self.purchased('C')
        d = self.purchased('D', name='Yasmin Harrison')
        start_tracking(self.store)
        job = self.laptop.get(NEXT, TRACKING).data
        self.complete(job, [
            {'order_ref': a.id, 'status': 'found', 'tracking_number': 'TBAU316654872',
             'carrier': 'Delivery By Amazon', 'ship_name': 'Elena Legkova', 'name_ok': 'yes'},
            {'order_ref': b.id, 'status': 'not_shipped'},
            {'order_ref': c.id, 'status': 'found', 'tracking_number': 'T1', 'carrier': 'Australia Post'},
            {'order_ref': c.id, 'status': 'not_shipped'},
            {'order_ref': d.id, 'status': 'found', 'tracking_number': 'T2', 'ship_name': 'Elena Legkova',
             'name_ok': 'no'},
        ])
        t = OrderTracking.objects.get(order=a)
        self.assertEqual((t.tracking_number, t.carrier, t.source), ('TBAU316654872', 'Delivery By Amazon', 'bot'))
        self.assertEqual(self.check(a).tracking_status, 'found')
        self.assertEqual(self.check(b).tracking_status, 'not_shipped')
        self.assertEqual(self.check(c).tracking_status, 'partly')
        self.assertIn('is not the customer (Yasmin Harrison)', self.check(d).tracking_note)
        # Not shipped and partly shipped orders are checked again; found ones are not.
        self.assertEqual({x.order_id for x in due_checks(self.store)}, {b.id, c.id})
        # The orders list shows the numbers.
        orders = {o['id']: o for o in self.c.get(self.url()).data['orders']}
        self.assertEqual(orders[str(a.id)]['tracking']['current'][0]['tracking_number'], 'TBAU316654872')

    def test_failed_job_marks_orders_failed_and_they_are_retried(self):
        a = self.purchased('A')
        start_tracking(self.store)
        job = self.laptop.get(NEXT, TRACKING).data
        self.laptop.post(job['complete_url'], {'exit_code': 3, 'stdout': 'Chrome did not start'}, format='multipart')
        self.assertEqual(self.check(a).tracking_status, 'failed')
        self.assertIn('Chrome did not start', self.check(a).tracking_note)
        self.assertEqual([x.order_id for x in due_checks(self.store)], [a.id])

    def test_same_number_is_not_saved_twice(self):
        a = self.purchased('A')
        for _ in range(2):
            run = start_tracking(self.store, checks=[self.check(a)])[0]
            job = self.laptop.get(NEXT, TRACKING).data
            self.assertEqual(job['job_id'], str(run.id))
            self.complete(job, [{'order_ref': a.id, 'status': 'found', 'tracking_number': 'TB1'}])
        self.assertEqual(OrderTracking.objects.filter(order=a).count(), 1)

    # ------------------------------------------------------------------ Replace

    def test_replace_keeps_history_and_the_bot_leaves_it(self):
        a = self.purchased('A')
        old = OrderTracking.objects.create(order=a, tracking_number='TB1', carrier='Delivery By Amazon')
        res = self.c.post(self.url(f'{a.id}/tracking/'), {'tracking_id': str(old.id), 'tracking_number': ' AP 99 '})
        self.assertEqual(res.status_code, 200, res.data)
        cur = res.data['tracking']['current']
        self.assertEqual([(t['tracking_number'], t['carrier'], t['source']) for t in cur],
                         [('AP 99', 'Delivery By Amazon', 'user')])
        self.assertEqual(res.data['tracking']['replaced'][0]['tracking_number'], 'TB1')
        # Replacing the old one again is refused.
        res = self.c.post(self.url(f'{a.id}/tracking/'), {'tracking_id': str(old.id), 'tracking_number': 'X'})
        self.assertEqual(res.status_code, 409)
        # The schedule skips it, and a bot result for it is not saved.
        self.assertEqual(due_checks(self.store), [])
        run = start_tracking(self.store, checks=[self.check(a)])[0]
        job = self.laptop.get(NEXT, TRACKING).data
        self.assertEqual(job['job_id'], str(run.id))
        self.complete(job, [{'order_ref': a.id, 'status': 'found', 'tracking_number': 'TB2'}])
        self.assertEqual(list(OrderTracking.objects.filter(order=a, replaced_at__isnull=True)
                              .values_list('tracking_number', flat=True)), ['AP 99'])
        self.assertIn('kept the tracking you entered', self.check(a).tracking_note)

    def test_add_tracking_without_one_and_empty_is_refused(self):
        a = self.purchased('A')
        self.assertEqual(self.c.post(self.url(f'{a.id}/tracking/'), {'tracking_number': ' '}).status_code, 400)
        res = self.c.post(self.url(f'{a.id}/tracking/'), {'tracking_number': 'TB9', 'carrier': 'Toll'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['tracking']['current'][0]['carrier'], 'Toll')
        self.assertEqual(self.c.post(self.url(f'{a.id}/tracking/'),
                                     {'tracking_id': 'not-a-uuid', 'tracking_number': 'X'}).status_code, 404)

    def test_get_tracking_one_order(self):
        a = self.purchased('A')
        unread = self.purchased('U', number='UNREAD')
        self.assertEqual(self.c.post(self.url(f'{unread.id}/get-tracking/')).status_code, 409)
        res = self.c.post(self.url(f'{a.id}/get-tracking/'))
        self.assertEqual(res.status_code, 202, res.data)
        self.assertTrue(res.data['tracking']['pending'])
        self.assertEqual(self.c.post(self.url(f'{a.id}/get-tracking/')).status_code, 409)

    # ------------------------------------------------------------------ schedule and profile lock

    def test_schedule_does_not_pile_up_while_the_laptop_is_off(self):
        from listings.tasks import queue_order_tracking

        self.purchased('A')
        self.assertEqual(queue_order_tracking(), 1)
        self.purchased('B')
        self.assertEqual(queue_order_tracking(), 0)
        self.assertEqual(AutoOrderRun.objects.filter(kind='tracking').count(), 1)

    def test_one_chrome_profile_at_a_time(self):
        # The order bot is buying on Amazon AU: the tracking job waits.
        buying = AutoOrderRun.objects.create(
            store=self.store, user=self.user, kind='purchase', marketplace='kogan', account_name='Amazon AU',
            vendor='amazon_au', status=AutoOrderRun.Status.RUNNING, claimed_at=timezone.now())
        self.purchased('A')
        start_tracking(self.store)
        self.assertIsNone(self.laptop.get(NEXT, TRACKING).data['job_id'])
        buying.status = AutoOrderRun.Status.FINISHED
        buying.save()
        job = self.laptop.get(NEXT, TRACKING).data
        self.assertEqual(job['kind'], 'tracking')
        # Now the tracking job runs: a purchase on the same account waits, another account does not.
        AutoOrderRun.objects.create(
            store=self.store, user=self.user, kind='purchase', marketplace='kogan', account_name='Amazon AU',
            vendor='amazon_au', status=AutoOrderRun.Status.PENDING)
        self.assertIsNone(self.laptop.get(NEXT, ORDER_BOT).data['job_id'])
        other = AutoOrderRun.objects.create(
            store=self.store, user=self.user, kind='purchase', marketplace='kogan', account_name='Costco AU',
            vendor='costco_au', status=AutoOrderRun.Status.PENDING)
        self.assertEqual(self.laptop.get(NEXT, ORDER_BOT).data['job_id'], str(other.id))

    def test_dead_run_does_not_hold_the_profile(self):
        AutoOrderRun.objects.create(
            store=self.store, user=self.user, kind='purchase', marketplace='kogan', account_name='Amazon AU',
            vendor='amazon_au', status=AutoOrderRun.Status.RUNNING, claimed_at=timezone.now() - timedelta(hours=3))
        self.purchased('A')
        start_tracking(self.store)
        self.assertEqual(self.laptop.get(NEXT, TRACKING).data['kind'], 'tracking')
