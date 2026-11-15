"""Tests for Auto Scraping (scrape at run times, then retry Failed cycles)."""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from catalog.models import ProductMapping
from marketplace.models import Marketplace
from products.models import Product
from stores.models import Store
from sync import auto_scrape
from sync.models import AutoScrapeSchedule
from vendor.models import Vendor

User = get_user_model()
SYD = ZoneInfo('Australia/Sydney')


class NormalizePayloadTests(TestCase):
    def test_valid_payload_is_cleaned(self):
        clean = auto_scrape.normalize_auto_scrape_payload({
            'run_times': ['18:30', '6:00', '06:00'],
            'timezone': 'Australia/Sydney',
            'retry_failed_cycles': '3',
            'push_after': True,
        })
        self.assertEqual(clean['run_times'], ['06:00', '18:30'])
        self.assertEqual(clean['retry_failed_cycles'], 3)
        self.assertTrue(clean['push_after'])

    def test_rejects_bad_values(self):
        bad = [
            {'run_times': []},
            {'run_times': ['25:00']},
            {'run_times': ['06:00'], 'timezone': 'Mars/Base'},
            {'run_times': ['06:00'], 'retry_failed_cycles': 11},
            {'run_times': ['06:00'], 'retry_failed_cycles': -1},
            {'run_times': ['00:00', '01:00', '02:00', '03:00', '04:00', '05:00', '06:00']},
        ]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(ValueError):
                auto_scrape.normalize_auto_scrape_payload(data)


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class AutoScrapeBase(TestCase):
    def setUp(self):
        self.mp, _ = Marketplace.objects.get_or_create(code='auto_scrape_mt', defaults={'name': 'Auto Scrape MT'})
        self.user = User.objects.create_user(
            username='auto_scrape_u', email='auto_scrape_u@example.com', password='pass12345',
        )
        self.store = Store.objects.create(
            user=self.user, name='Auto Scrape Store', region='AU', api_token='tok',
            marketplace=self.mp, connection_status='connected',
        )
        self.vendor, _ = Vendor.objects.get_or_create(code='auto_scrape_vendor', defaults={'name': 'AS Vendor'})
        self.sched = AutoScrapeSchedule.objects.create(
            store=self.store, is_active=True, run_times=['06:00'], timezone='Australia/Sydney',
            retry_failed_cycles=3,
        )

    def _mapping(self, sku, status):
        product = Product.objects.create(
            owner=self.user, vendor=self.vendor, vendor_sku=sku, vendor_url=f'https://example.com/{sku}',
        )
        return ProductMapping.objects.create(store=self.store, product=product, is_active=True, sync_status=status)


class DueRunTimeTests(AutoScrapeBase):
    def test_due_at_and_shortly_after_run_time_once(self):
        at = datetime(2026, 11, 15, 6, 0, 20, tzinfo=SYD)
        self.assertIsNotNone(auto_scrape.due_run_time(self.sched, at))
        self.assertIsNotNone(auto_scrape.due_run_time(self.sched, at + timedelta(minutes=3)))
        self.assertIsNone(auto_scrape.due_run_time(self.sched, at + timedelta(minutes=6)))
        self.assertIsNone(auto_scrape.due_run_time(self.sched, at - timedelta(minutes=2)))

        self.assertEqual(auto_scrape.check_due_schedules(at), [str(self.store.id)])
        # Same slot is not claimed twice.
        self.assertEqual(auto_scrape.check_due_schedules(at + timedelta(minutes=1)), [])
        # Next day's slot is due again.
        self.assertEqual(auto_scrape.check_due_schedules(at + timedelta(days=1)), [str(self.store.id)])

    def test_inactive_store_or_schedule_is_not_due(self):
        at = datetime(2026, 11, 15, 6, 0, tzinfo=SYD)
        Store.objects.filter(pk=self.store.pk).update(is_active=False)
        self.assertEqual(auto_scrape.check_due_schedules(at), [])
        Store.objects.filter(pk=self.store.pk).update(is_active=True)
        AutoScrapeSchedule.objects.filter(pk=self.sched.pk).update(is_active=False)
        self.assertEqual(auto_scrape.check_due_schedules(at), [])

    def test_slot_just_before_midnight(self):
        self.sched.run_times = ['23:58']
        self.sched.save()
        at = datetime(2026, 11, 16, 0, 1, tzinfo=SYD)
        self.assertEqual(auto_scrape.due_run_time(self.sched, at), datetime(2026, 11, 15, 23, 58, tzinfo=SYD))


class RunAutoScrapeTests(AutoScrapeBase):
    def _patches(self, scrape_side_effect):
        return (
            patch.object(auto_scrape, '_scrape_already_running', return_value=False),
            patch.object(auto_scrape, '_store_update_running', return_value=False),
            patch.object(auto_scrape, '_catalog_scrape_pass', side_effect=scrape_side_effect),
            patch.object(auto_scrape, '_catalog_push', return_value='queued'),
        )

    def _run(self, scrape_side_effect):
        p1, p2, p3, p4 = self._patches(scrape_side_effect)
        with p1, p2, p3 as scrape, p4 as push:
            result = auto_scrape.run_auto_scrape(str(self.store.id))
        return result, scrape, push

    def test_retries_failed_until_none_left(self):
        rows = [self._mapping(f'sku-{i}', 'scraped') for i in range(4)]
        # Pass 0: 3 fail; cycle 1: 1 still fails; cycle 2: all good.
        plan = [{r.pk for r in rows[:3]}, {rows[0].pk}, set()]

        def scrape(_store=None):
            failing = plan.pop(0)
            for r in ProductMapping.objects.filter(store=self.store, sync_status='pending'):
                r.sync_status = 'failed' if r.pk in failing else 'scraped'
                r.save(update_fields=['sync_status'])
            return {}

        result, scrape_mock, push = self._run(scrape)
        self.assertEqual(scrape_mock.call_count, 3)
        self.assertEqual(result['failed_after'], [3, 1, 0])
        self.assertEqual(result['cycles_run'], 2)
        self.assertEqual(result['stop_reason'], 'no_failed')
        self.assertEqual(result['status'], 'success')
        push.assert_not_called()
        self.sched.refresh_from_db()
        self.assertEqual(self.sched.last_status, 'success')
        self.assertIsNotNone(self.sched.last_finished_at)

    def test_stops_after_max_cycles_and_pushes_when_on(self):
        AutoScrapeSchedule.objects.filter(pk=self.sched.pk).update(retry_failed_cycles=2, push_after=True)
        self._mapping('sku-x', 'scraped')

        def scrape(_store=None):
            ProductMapping.objects.filter(store=self.store, sync_status='pending').update(sync_status='failed')
            return {}

        result, scrape_mock, push = self._run(scrape)
        self.assertEqual(scrape_mock.call_count, 3)  # first pass + 2 cycles
        self.assertEqual(result['failed_after'], [1, 1, 1])
        self.assertEqual(result['stop_reason'], 'cycles_done')
        self.assertEqual(result['status'], 'partial')
        push.assert_called_once()

    def test_zero_cycles_scrapes_once(self):
        AutoScrapeSchedule.objects.filter(pk=self.sched.pk).update(retry_failed_cycles=0)
        self._mapping('sku-z', 'scraped')

        def scrape(_store=None):
            ProductMapping.objects.filter(store=self.store, sync_status='pending').update(sync_status='failed')
            return {}

        result, scrape_mock, _ = self._run(scrape)
        self.assertEqual(scrape_mock.call_count, 1)
        self.assertEqual(result['failed_after'], [1])

    def test_user_stop_ends_run_without_push(self):
        AutoScrapeSchedule.objects.filter(pk=self.sched.pk).update(push_after=True)
        self._mapping('sku-s', 'scraped')

        def scrape(_store=None):
            ProductMapping.objects.filter(store=self.store, sync_status='pending').update(sync_status='failed')
            return {'stop': 'user_cancelled'}

        result, scrape_mock, push = self._run(scrape)
        self.assertEqual(scrape_mock.call_count, 1)
        self.assertEqual(result['status'], 'stopped')
        self.assertEqual(result['stop_reason'], 'user_cancelled')
        push.assert_not_called()

    def test_skips_when_scrape_already_running(self):
        with patch.object(auto_scrape, '_scrape_already_running', return_value=True), \
                patch.object(auto_scrape, '_catalog_scrape_pass') as scrape:
            result = auto_scrape.run_auto_scrape(str(self.store.id))
        self.assertTrue(result['skipped'])
        self.assertEqual(result['reason'], 'scrape_running')
        scrape.assert_not_called()


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class StoreSerializerAutoScrapeTests(AutoScrapeBase):
    def test_save_and_read_auto_scrape(self):
        from rest_framework.test import APIRequestFactory

        from stores.serializers import StoreSerializer

        req = APIRequestFactory().get('/')
        req.user = self.user
        ser = StoreSerializer(context={'request': req})
        ser._save_auto_scrape(self.store, {
            'enabled': True, 'run_times': ['07:15', '19:00'], 'timezone': 'Australia/Sydney',
            'retry_failed_cycles': 4, 'push_after': True,
        })
        data = StoreSerializer(Store.objects.get(pk=self.store.pk), context={'request': req}).data['auto_scrape']
        self.assertTrue(data['enabled'])
        self.assertEqual(data['run_times'], ['07:15', '19:00'])
        self.assertEqual(data['retry_failed_cycles'], 4)

        ser._save_auto_scrape(self.store, {'enabled': False})
        data = StoreSerializer(Store.objects.get(pk=self.store.pk), context={'request': req}).data['auto_scrape']
        self.assertFalse(data['enabled'])
        self.assertEqual(data['run_times'], ['07:15', '19:00'])  # kept for when it is turned back on
