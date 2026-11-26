"""Scheduled ordering: per Auto Purchasing vendor, fetch on the laptop, check, then send that vendor's orders."""
import shutil
import tempfile
from datetime import timedelta
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from listings import auto_order_schedule as sched
from listings.models import Environment, MarketplaceOrder, OrderPurchaseCheck, OrderStatus
from marketplace.models import Marketplace
from stores.models import AutoOrderRun, PurchaseAccount, Store, StoreAutoBuyVendor

User = get_user_model()
_MEDIA = tempfile.mkdtemp(prefix='aos-test-')
SYDNEY = ZoneInfo('Australia/Sydney')


def _now_hhmm(now):
    return now.astimezone(SYDNEY).strftime('%H:%M')


@override_settings(MEDIA_ROOT=_MEDIA, AUTO_ORDER_BOT_RUNNER='laptop', DEBUG=True,
                   ENCRYPTION_KEY=Fernet.generate_key().decode())
class ScheduledOrderingTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA, ignore_errors=True)

    def setUp(self):
        self.now = timezone.now().replace(second=30, microsecond=0)
        self.user = User.objects.create_user(username='aos', email='aos@example.com', password='pw12345678')
        kogan = Marketplace.objects.get_or_create(code='kogan', defaults={'name': 'Kogan'})[0]
        self.store = Store.objects.create(
            user=self.user, name='Kogan-TFS', region='AU', api_token='t', marketplace=kogan,
            auto_buy_vendor='amazon_au',
        )
        amazon = PurchaseAccount.objects.create(
            user=self.user, name='au-amazon', vendor='amazon_au', region='AU', chrome_profile='P1',
        )
        costco = PurchaseAccount.objects.create(
            user=self.user, name='au-costco', vendor='costco_au', region='AU', chrome_profile='P2',
        )
        self.amazon_row = StoreAutoBuyVendor.objects.create(
            store=self.store, vendor='amazon_au', account=amazon, auto_process=True,
            process_times=[_now_hhmm(self.now)], process_timezone='Australia/Sydney',
        )
        self.costco_row = StoreAutoBuyVendor.objects.create(store=self.store, vendor='costco_au', account=costco)

    def order(self, key, vendor='amazon_au', status='buyable', **dates):
        lines = [{'sku': f'{key}-SKU', 'quantity': 1, 'priceCents': 5000, 'vendor_url': 'https://example.com/p',
                  'vendor_id': 'V1', 'vendor_price_cents': 3000, 'auto_buy_vendor': vendor}]
        order = MarketplaceOrder.objects.create(
            store=self.store, user=self.user, external_order_key=key, invoice_number=key,
            environment=Environment.PRODUCTION, status=OrderStatus.PAID,
            customer_info_json={'name': 'A Buyer'},
            line_items_json=[{'sku': ln['sku'], 'title': ln['sku'], 'quantity': 1, 'priceCents': 5000} for ln in lines],
            raw_response_json={'customer': {'name': 'A Buyer', 'shippingAddress': {'postcode': '2155'}}, **dates},
        )
        OrderPurchaseCheck.objects.create(
            order=order, store=self.store, status=status, lines_json=lines,
            checked_at=timezone.now(), earning_cents=1000, cost_cents=3000,
        )
        return order

    def fetch_runs(self):
        return AutoOrderRun.objects.filter(store=self.store, kind=AutoOrderRun.Kind.FETCH_ORDERS)

    def finish_fetch(self):
        self.fetch_runs().update(status=AutoOrderRun.Status.FINISHED, finished_at=timezone.now())

    def purchase_status(self, order):
        return OrderPurchaseCheck.objects.get(order=order).purchase_status

    # ------------------------------------------------------------------ start

    def test_due_time_asks_the_laptop_to_fetch_and_waits(self):
        amazon = self.order('A1')
        out = sched.tick(self.now)
        self.assertEqual(out['started'], [f'{self.store.id}/amazon_au'])
        self.assertEqual(out['sent'], [])
        self.assertEqual(self.fetch_runs().count(), 1)
        self.amazon_row.refresh_from_db()
        self.assertIsNotNone(self.amazon_row.process_pending_since)
        self.assertEqual(self.purchase_status(amazon), '')

    def test_not_due_and_switched_off_do_nothing(self):
        StoreAutoBuyVendor.objects.filter(pk=self.amazon_row.pk).update(
            process_times=[(self.now - timedelta(hours=2)).astimezone(SYDNEY).strftime('%H:%M')],
        )
        self.assertEqual(sched.tick(self.now)['started'], [])
        StoreAutoBuyVendor.objects.filter(pk=self.amazon_row.pk).update(
            auto_process=False, process_times=[_now_hhmm(self.now)],
        )
        self.assertEqual(sched.tick(self.now)['started'], [])
        self.assertEqual(self.fetch_runs().count(), 0)

    def test_each_time_runs_once(self):
        sched.tick(self.now)
        self.finish_fetch()
        sched.tick(self.now + timedelta(minutes=1))
        self.assertEqual(sched.tick(self.now + timedelta(minutes=2))['started'], [])
        self.assertEqual(self.fetch_runs().count(), 1)

    # ------------------------------------------------------------------ send

    def test_after_the_fetch_only_this_vendors_ready_orders_are_sent(self):
        amazon = self.order('A1')
        costco = self.order('C1', vendor='costco_au')
        loss = self.order('A2', status='blocked')
        sched.tick(self.now)
        self.finish_fetch()
        out = sched.tick(self.now + timedelta(minutes=2))
        self.assertEqual(out['sent'], [f'{self.store.id}/amazon_au'])
        self.assertEqual(self.purchase_status(amazon), 'queued')
        self.assertEqual(self.purchase_status(costco), '')
        self.assertEqual(self.purchase_status(loss), '')
        run = AutoOrderRun.objects.get(kind=AutoOrderRun.Kind.PURCHASE)
        self.assertEqual(run.vendor, 'amazon_au')
        self.amazon_row.refresh_from_db()
        self.assertIsNone(self.amazon_row.process_pending_since)
        self.assertIn('1 order(s) from Amazon AU placed on', self.amazon_row.process_last_message)

    def test_only_orders_placed_on_the_run_day_are_sent(self):
        today = self.now.astimezone(SYDNEY).date()
        yesterday = today - timedelta(days=1)
        fresh = self.order('A1', orderDate=today.isoformat())
        old_file = self.order('A2', orderDate=yesterday.isoformat())
        # API order placed at 23:30 Sydney yesterday (12:30 UTC): yesterday, not today.
        old_api = self.order('A3', orderedAt=yesterday.isoformat() + 'T12:30:00Z')
        sched.tick(self.now)
        self.finish_fetch()
        sched.tick(self.now + timedelta(minutes=2))
        self.assertEqual(self.purchase_status(fresh), 'queued')
        self.assertEqual(self.purchase_status(old_file), '')
        self.assertEqual(self.purchase_status(old_api), '')
        self.amazon_row.refresh_from_db()
        msg = self.amazon_row.process_last_message
        self.assertIn(f'1 order(s) from Amazon AU placed on {today:%d/%m/%Y} sent', msg)
        self.assertIn('2 ready order(s) from other days left for you', msg)

    def test_manual_process_all_still_sends_every_day(self):
        from listings.auto_purchase_jobs import process_all

        yesterday = (self.now.astimezone(SYDNEY).date() - timedelta(days=1)).isoformat()
        old = self.order('A2', orderDate=yesterday)
        self.assertEqual(process_all(self.store, self.user, vendor='amazon_au')['queued'], 1)
        self.assertEqual(self.purchase_status(old), 'queued')

    def test_waits_while_orders_are_being_checked(self):
        amazon = self.order('A1')
        sched.tick(self.now)
        self.finish_fetch()
        self.order('A9', status='checking')
        self.assertEqual(sched.tick(self.now + timedelta(minutes=2))['sent'], [])
        self.assertEqual(self.purchase_status(amazon), '')
        OrderPurchaseCheck.objects.filter(status='checking').update(status='blocked')
        self.assertEqual(len(sched.tick(self.now + timedelta(minutes=3))['sent']), 1)
        self.assertEqual(self.purchase_status(amazon), 'queued')

    def test_laptop_that_never_fetches_still_sends_the_checked_orders(self):
        amazon = self.order('A1')
        sched.tick(self.now)
        self.assertEqual(sched.tick(self.now + timedelta(hours=1))['sent'], [])
        out = sched.tick(self.now + sched.FETCH_WAIT + timedelta(minutes=1))
        self.assertEqual(len(out['sent']), 1)
        self.assertEqual(self.purchase_status(amazon), 'queued')
        self.amazon_row.refresh_from_db()
        self.assertIn('did not fetch the orders in time', self.amazon_row.process_last_message)

    def test_vendors_with_the_same_time_share_one_fetch(self):
        StoreAutoBuyVendor.objects.filter(pk=self.costco_row.pk).update(
            auto_process=True, process_times=[_now_hhmm(self.now)], process_timezone='Australia/Sydney',
        )
        amazon, costco = self.order('A1'), self.order('C1', vendor='costco_au')
        self.assertEqual(len(sched.tick(self.now)['started']), 2)
        self.assertEqual(self.fetch_runs().count(), 1)
        self.finish_fetch()
        self.assertEqual(len(sched.tick(self.now + timedelta(minutes=2))['sent']), 2)
        self.assertEqual((self.purchase_status(amazon), self.purchase_status(costco)), ('queued', 'queued'))

    def test_time_that_comes_while_the_last_run_waits_is_skipped(self):
        sched.tick(self.now)
        later = self.now + timedelta(hours=1)
        StoreAutoBuyVendor.objects.filter(pk=self.amazon_row.pk).update(
            process_times=[_now_hhmm(self.now), _now_hhmm(later)],
        )
        self.assertEqual(sched.tick(later)['started'], [])
        self.amazon_row.refresh_from_db()
        self.assertIn('skipped', self.amazon_row.process_last_message)
        self.assertEqual(self.fetch_runs().count(), 1)

    def test_inactive_store_is_left_alone(self):
        Store.objects.filter(pk=self.store.pk).update(is_active=False)
        self.assertEqual(sched.tick(self.now)['started'], [])

    def test_fetch_not_possible_still_sends_checked_orders(self):
        amazon = self.order('A1')
        with override_settings(AUTO_ORDER_BOT_RUNNER='local'):
            out = sched.tick(self.now)
        self.assertEqual(len(out['started']), 1)
        self.assertEqual(len(out['sent']), 1)
        self.assertEqual(self.fetch_runs().count(), 0)
        self.assertEqual(self.purchase_status(amazon), 'queued')


@override_settings(MEDIA_ROOT=_MEDIA, ENCRYPTION_KEY=Fernet.generate_key().decode())
class OrderScheduleSettingsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='aos2', email='aos2@example.com', password='pw12345678')
        kogan = Marketplace.objects.get_or_create(code='kogan', defaults={'name': 'Kogan'})[0]
        self.store = Store.objects.create(user=self.user, name='K', region='AU', api_token='t', marketplace=kogan)
        self.account = PurchaseAccount.objects.create(
            user=self.user, name='au-amazon', vendor='amazon_au', region='AU', chrome_profile='P1',
        )
        self.c = APIClient()
        self.c.force_authenticate(self.user)

    def patch(self, rows):
        return self.c.patch(f'/api/v1/stores/{self.store.id}/', {'auto_buy_vendors': rows}, format='json')

    def test_times_are_saved_per_vendor_and_returned(self):
        res = self.patch([
            {'vendor': 'amazon_au', 'account': str(self.account.id), 'auto_process': True,
             'process_times': ['15:00', '9:30'], 'process_timezone': 'Australia/Perth'},
            {'vendor': 'ebay_au', 'account': None},
        ])
        self.assertEqual(res.status_code, 200, res.content)
        rows = {r['vendor']: r for r in res.data['auto_buy_vendors']}
        self.assertTrue(rows['amazon_au']['auto_process'])
        self.assertEqual(rows['amazon_au']['process_times'], ['09:30', '15:00'])
        self.assertEqual(rows['amazon_au']['process_timezone'], 'Australia/Perth')
        self.assertFalse(rows['ebay_au']['auto_process'])
        self.assertEqual(rows['ebay_au']['process_timezone'], 'Australia/Sydney')

    def test_switched_on_without_times_is_refused(self):
        res = self.patch([{'vendor': 'amazon_au', 'account': str(self.account.id), 'auto_process': True,
                           'process_times': []}])
        self.assertEqual(res.status_code, 400)
        self.assertIn('Amazon AU order schedule', str(res.data))

    def test_bad_time_is_refused(self):
        res = self.patch([{'vendor': 'amazon_au', 'account': str(self.account.id), 'auto_process': True,
                           'process_times': ['25:00']}])
        self.assertEqual(res.status_code, 400)

    def test_saving_without_schedule_fields_keeps_the_schedule(self):
        self.patch([{'vendor': 'amazon_au', 'account': str(self.account.id), 'auto_process': True,
                     'process_times': ['09:00']}])
        self.patch([{'vendor': 'amazon_au', 'account': str(self.account.id)}])
        row = StoreAutoBuyVendor.objects.get(store=self.store, vendor='amazon_au')
        self.assertTrue(row.auto_process)
        self.assertEqual(row.process_times, ['09:00'])

    def test_switching_off_clears_a_waiting_run(self):
        self.patch([{'vendor': 'amazon_au', 'account': str(self.account.id), 'auto_process': True,
                     'process_times': ['09:00']}])
        StoreAutoBuyVendor.objects.filter(store=self.store).update(process_pending_since=timezone.now())
        self.patch([{'vendor': 'amazon_au', 'account': str(self.account.id), 'auto_process': False,
                     'process_times': ['09:00']}])
        row = StoreAutoBuyVendor.objects.get(store=self.store, vendor='amazon_au')
        self.assertFalse(row.auto_process)
        self.assertIsNone(row.process_pending_since)


class BeatEntryTests(TestCase):
    def test_runs_every_minute_on_the_light_queue(self):
        from django.conf import settings

        from core.celery import app

        entry = app.conf.beat_schedule['run-auto-order-schedules']
        self.assertEqual(entry['task'], 'listings.run_auto_order_schedules')
        routes = {}
        for part in settings.CELERY_TASK_ROUTES:
            if isinstance(part, dict):
                routes.update(part)
        self.assertEqual(routes['listings.run_auto_order_schedules'], {'queue': 'light'})


class OrderDayTests(TestCase):
    def _order(self, raw, created=None):
        from types import SimpleNamespace

        return SimpleNamespace(raw_response_json=raw, created_at=created)

    def day(self, raw, created=None, tz='Australia/Sydney'):
        from listings.auto_purchase_jobs import order_day

        return order_day(self._order(raw, created), tz)

    def test_orders_file_day_is_used_as_is(self):
        self.assertEqual(str(self.day({'orderDate': '2026-11-27'})), '2026-11-27')

    def test_api_time_in_utc_is_turned_into_the_schedule_day(self):
        self.assertEqual(str(self.day({'orderedAt': '2026-11-26T14:30:00Z'})), '2026-11-27')
        self.assertEqual(str(self.day({'createdAt': '2026-11-26T12:30:00+00:00'})), '2026-11-26')

    def test_marketplace_local_time_without_zone(self):
        self.assertEqual(str(self.day({'orderedAt': '2026-11-26T23:50:00'})), '2026-11-26')

    def test_epoch_milliseconds(self):
        self.assertEqual(str(self.day({'orderedAt': 1795700000000})), '2026-11-27')

    def test_no_date_uses_when_it_came_in(self):
        from datetime import datetime, timezone as dt_tz

        came = datetime(2026, 11, 26, 20, 0, tzinfo=dt_tz.utc)  # 07:00 on the 27th in Sydney
        self.assertEqual(str(self.day({}, came)), '2026-11-27')
        self.assertIsNone(self.day({'orderDate': 'not a date'}, None))
