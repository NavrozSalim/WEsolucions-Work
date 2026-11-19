"""Tests for Auto Scheduling: Auto Scraping + Scheduled Updates, store-wide and per vendor."""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from catalog.models import ProductMapping
from marketplace.models import Marketplace
from products.models import Product
from stores.models import Store
from sync import run_schedules as rs
from sync.models import AutoScrapeSchedule, ScheduledRun, SyncSchedule, VendorSchedule
from vendor.models import Vendor

User = get_user_model()
SYD = ZoneInfo('Australia/Sydney')


class NormalizeTests(TestCase):
    def test_card_payload_is_cleaned(self):
        clean = rs.normalize_card_payload({
            'run_times': ['18:30', '6:00', '06:00'],
            'timezone': 'Australia/Sydney',
            'retry_failed_cycles': '3',
        }, rs.KIND_SCRAPE)
        self.assertEqual(clean['run_times'], ['06:00', '18:30'])
        self.assertEqual(clean['retry_failed_cycles'], 3)
        self.assertIsNone(clean['vendors'])  # not sent → vendor rows left alone

    def test_limits_per_kind(self):
        seven = [f'{h:02d}:00' for h in range(7)]
        with self.assertRaises(ValueError):
            rs.normalize_card_payload({'run_times': seven}, rs.KIND_SCRAPE)
        self.assertEqual(len(rs.normalize_card_payload({'run_times': seven}, rs.KIND_UPDATE)['run_times']), 7)
        thirteen = [f'{h:02d}:00' for h in range(13)]
        with self.assertRaises(ValueError):
            rs.normalize_card_payload({'run_times': thirteen}, rs.KIND_UPDATE)

    def test_rejects_bad_values(self):
        bad = [
            {'run_times': []},
            {'run_times': ['25:00']},
            {'run_times': ['06:00'], 'timezone': 'Mars/Base'},
            {'run_times': ['06:00'], 'retry_failed_cycles': 11},
            {'run_times': ['06:00'], 'retry_failed_cycles': -1},
            {'run_times': ['06:00'], 'vendors': [{'vendor_id': 'a', 'run_times': ['07:00']},
                                                 {'vendor_id': 'a', 'run_times': ['08:00']}]},
            {'run_times': ['06:00'], 'vendors': [{'vendor_id': '', 'run_times': ['07:00']}]},
            {'run_times': ['06:00'], 'vendors': [{'vendor_id': 'a', 'run_times': []}]},
        ]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(ValueError):
                rs.normalize_card_payload(data, rs.KIND_SCRAPE)

    def test_update_defaults_to_zero_retries_scrape_to_two(self):
        self.assertEqual(rs.normalize_card_payload({'run_times': ['06:00']}, rs.KIND_UPDATE)['retry_failed_cycles'], 0)
        self.assertEqual(rs.normalize_card_payload({'run_times': ['06:00']}, rs.KIND_SCRAPE)['retry_failed_cycles'], 2)

    def test_old_frequency_converts_to_run_times(self):
        cases = [
            (('crontab', '10', '0'), ['10:00']),
            (('crontab', '*/12', '30'), ['00:30', '12:30']),
            (('crontab', '*/6', '15'), ['00:15', '06:15', '12:15', '18:15']),
            (('crontab', '*/2', '0'), [f'{h:02d}:00' for h in range(0, 24, 2)]),
            (('interval', '10', '0', 7200), [f'{h:02d}:00' for h in range(0, 24, 2)]),
            (('interval', '10', '0', 3600), [f'{h:02d}:00' for h in range(0, 24, 2)]),  # capped at 12
            (('crontab', '3,15', '5'), ['03:05', '15:05']),
        ]
        for args, expected in cases:
            with self.subTest(args=args):
                self.assertEqual(rs.run_times_from_crontab(*args), expected)

    def test_old_update_payload_without_run_times(self):
        clean = rs.normalize_card_payload(
            {'enabled': True, 'crontab_hour': '*/6', 'crontab_minute': '0', 'timezone': 'UTC'}, rs.KIND_UPDATE,
        )
        self.assertEqual(clean['run_times'], ['00:00', '06:00', '12:00', '18:00'])

    def test_migration_uses_same_conversion(self):
        import importlib

        mig = importlib.import_module('sync.migrations.0009_sync_schedule_run_times_data')
        for args in [('crontab', '*/2', '0', None), ('crontab', '10', '45', None), ('interval', '0', '0', 21600)]:
            self.assertEqual(mig.run_times_from_old_fields(*args), rs.run_times_from_crontab(*args))


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class Base(TestCase):
    def setUp(self):
        self.mp, _ = Marketplace.objects.get_or_create(code='sched_mt', defaults={'name': 'Sched MT'})
        self.user = User.objects.create_user(username='sched_u', email='sched_u@example.com', password='pass12345')
        self.store = Store.objects.create(
            user=self.user, name='Sched Store', region='AU', api_token='tok',
            marketplace=self.mp, connection_status='connected',
        )
        self.costco, _ = Vendor.objects.get_or_create(code='sched_costco', defaults={'name': 'Costco T'})
        self.amazon, _ = Vendor.objects.get_or_create(code='sched_amazon', defaults={'name': 'Amazon T'})
        self.auto = AutoScrapeSchedule.objects.create(
            store=self.store, is_active=True, run_times=['06:00'], timezone='Australia/Sydney', retry_failed_cycles=3,
        )
        self.update = SyncSchedule.objects.create(
            store=self.store, is_active=True, run_times=['10:00'], timezone='Australia/Sydney', retry_failed_cycles=2,
        )

    def _mapping(self, sku, status, vendor=None):
        product = Product.objects.create(
            owner=self.user, vendor=vendor or self.costco, vendor_sku=sku, vendor_url=f'https://example.com/{sku}',
        )
        return ProductMapping.objects.create(store=self.store, product=product, is_active=True, sync_status=status)

    def _vendor_row(self, vendor, kind, times=('07:00',), cycles=1):
        return VendorSchedule.objects.create(
            store=self.store, vendor=vendor, kind=kind, run_times=list(times),
            timezone='Australia/Sydney', retry_failed_cycles=cycles,
        )

    def _running(self, kind=rs.KIND_SCRAPE, vendor=None, heartbeat=None):
        now = timezone.now()
        return ScheduledRun.objects.create(
            store=self.store, vendor=vendor, kind=kind, status='running',
            started_at=now, heartbeat_at=heartbeat or now,
        )


class DueTests(Base):
    def test_due_within_grace_once_and_next_day(self):
        at = datetime(2026, 11, 15, 6, 0, 20, tzinfo=SYD)
        self.assertIsNotNone(rs.due_slot(['06:00'], 'Australia/Sydney', None, at))
        self.assertIsNotNone(rs.due_slot(['06:00'], 'Australia/Sydney', None, at + timedelta(minutes=3)))
        self.assertIsNone(rs.due_slot(['06:00'], 'Australia/Sydney', None, at + timedelta(minutes=6)))
        self.assertIsNone(rs.due_slot(['06:00'], 'Australia/Sydney', None, at - timedelta(minutes=2)))

        self.assertEqual(len(rs.check_due(rs.KIND_SCRAPE, at)), 1)
        self.assertEqual(rs.check_due(rs.KIND_SCRAPE, at + timedelta(minutes=1)), [])  # slot taken
        ScheduledRun.objects.all().delete()
        self.assertEqual(len(rs.check_due(rs.KIND_SCRAPE, at + timedelta(days=1))), 1)

    def test_slot_just_before_midnight(self):
        at = datetime(2026, 11, 16, 0, 1, tzinfo=SYD)
        self.assertEqual(
            rs.due_slot(['23:58'], 'Australia/Sydney', None, at), datetime(2026, 11, 15, 23, 58, tzinfo=SYD),
        )

    def test_inactive_store_or_card_is_not_due(self):
        at = datetime(2026, 11, 15, 6, 0, tzinfo=SYD)
        Store.objects.filter(pk=self.store.pk).update(is_active=False)
        self.assertEqual(rs.check_due(rs.KIND_SCRAPE, at), [])
        Store.objects.filter(pk=self.store.pk).update(is_active=True)
        AutoScrapeSchedule.objects.filter(pk=self.auto.pk).update(is_active=False)
        self.assertEqual(rs.check_due(rs.KIND_SCRAPE, at), [])

    def test_vendor_row_runs_only_while_its_card_is_on(self):
        self._vendor_row(self.costco, rs.KIND_UPDATE, times=('07:00',))
        at = datetime(2026, 11, 15, 7, 0, tzinfo=SYD)
        SyncSchedule.objects.filter(pk=self.update.pk).update(is_active=False)  # e.g. paused by Manual sync
        self.assertEqual(rs.check_due(rs.KIND_UPDATE, at), [])
        SyncSchedule.objects.filter(pk=self.update.pk).update(is_active=True)
        run_ids = rs.check_due(rs.KIND_UPDATE, at)
        self.assertEqual(len(run_ids), 1)
        run = ScheduledRun.objects.get(pk=run_ids[0])
        self.assertEqual((run.kind, run.vendor_id, run.status), (rs.KIND_UPDATE, self.costco.id, 'waiting'))

    def test_vendor_rows_ignored_on_managed_store(self):
        Store.objects.filter(pk=self.store.pk).update(management_mode='full_store')
        self._vendor_row(self.costco, rs.KIND_SCRAPE, times=('07:00',))
        self.assertEqual(rs.check_due(rs.KIND_SCRAPE, datetime(2026, 11, 15, 7, 0, tzinfo=SYD)), [])

    def test_old_sync_schedule_without_run_times_uses_its_frequency(self):
        SyncSchedule.objects.filter(pk=self.update.pk).update(run_times=[], crontab_hour='*/12', crontab_minute='0')
        self.assertEqual(len(rs.check_due(rs.KIND_UPDATE, datetime(2026, 11, 15, 12, 0, tzinfo=SYD))), 1)

    def test_same_schedule_still_open_is_not_queued_twice(self):
        self._running(rs.KIND_SCRAPE)
        self.assertEqual(rs.check_due(rs.KIND_SCRAPE, datetime(2026, 11, 15, 6, 0, tzinfo=SYD)), [])


class QueueTests(Base):
    def setUp(self):
        super().setUp()
        p1 = patch.object(rs, '_scrape_running', return_value=False)
        p2 = patch.object(rs, '_push_running', return_value=False)
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    def _waiting(self, kind=rs.KIND_SCRAPE, vendor=None, age=timedelta(0)):
        run = ScheduledRun.objects.create(store=self.store, vendor=vendor, kind=kind, status='waiting')
        if age:
            ScheduledRun.objects.filter(pk=run.pk).update(created_at=timezone.now() - age)
        return run

    def test_oldest_waiting_run_starts_first(self):
        first = self._waiting(rs.KIND_UPDATE, age=timedelta(minutes=2))
        self._waiting(rs.KIND_SCRAPE, vendor=self.costco)
        started = []
        rs.start_waiting_runs(enqueue=started.append)
        self.assertEqual(started, [str(first.id)])
        first.refresh_from_db()
        self.assertEqual(first.status, 'running')
        # The store is busy now: nothing else starts.
        self.assertEqual(rs.start_waiting_runs(enqueue=started.append), [])

    def test_waits_while_scrape_running_then_starts(self):
        run = self._waiting()
        with patch.object(rs, '_scrape_running', return_value=True):
            self.assertEqual(rs.start_waiting_runs(enqueue=lambda _id: None), [])
        run.refresh_from_db()
        self.assertEqual(run.status, 'waiting')
        self.assertTrue(run.summary.get('wait_logged'))
        self.assertEqual(rs.start_waiting_runs(enqueue=lambda _id: None), [str(run.id)])

    def test_waiting_too_long_is_skipped(self):
        run = self._waiting(age=rs.MAX_QUEUE_WAIT + timedelta(minutes=1))
        rs.start_waiting_runs(enqueue=lambda _id: None)
        run.refresh_from_db()
        self.assertEqual(run.status, 'skipped')
        self.assertEqual(run.summary['stop_reason'], 'waited_too_long')

    def test_dead_run_is_failed_and_frees_the_store(self):
        dead = self._running(heartbeat=timezone.now() - rs.RUN_STALE - timedelta(minutes=1))
        waiting = self._waiting()
        self.assertEqual(rs.start_waiting_runs(enqueue=lambda _id: None), [str(waiting.id)])
        dead.refresh_from_db()
        self.assertEqual((dead.status, dead.summary['stop_reason']), ('failed', 'worker_lost'))

    def test_run_in_push_phase_stays_alive_longer(self):
        pushing = self._running(heartbeat=timezone.now() - timedelta(hours=2))
        ScheduledRun.objects.filter(pk=pushing.pk).update(summary={'phase': 'push'})
        self._waiting()
        self.assertEqual(rs.start_waiting_runs(enqueue=lambda _id: None), [])
        pushing.refresh_from_db()
        self.assertEqual(pushing.status, 'running')
        self.assertTrue(rs.run_hold_active(self.store.id))


class ExecuteRunTests(Base):
    """``execute_run`` on catalog stores with the scrape and push mocked."""

    def _run(self, kind, vendor=None, plan=None, stop=None):
        """``plan``: one set of SKUs that fail per scrape pass."""
        run = self._running(kind, vendor)
        passes = list(plan or [set()])

        def scrape(_store, _source, _hb):
            failing = passes.pop(0) if passes else set()
            for pm in ProductMapping.objects.filter(store=self.store, sync_status='pending').select_related('product'):
                pm.sync_status = 'failed' if pm.product.vendor_sku in failing else 'scraped'
                pm.save(update_fields=['sync_status'])
            return stop or {}

        with patch.object(rs, '_catalog_scrape_pass', side_effect=scrape) as scrape_mock, \
                patch('sync.tasks._execute_store_push_listings_only',
                      return_value={'pushed': 3, 'failed': 0, 'skipped_no_listing': 0}) as push_mock:
            result = rs.execute_run(str(run.id))
        run.refresh_from_db()
        return run, result, scrape_mock, push_mock

    def test_scrape_retries_until_none_failed_and_never_pushes(self):
        for i in range(4):
            self._mapping(f'c{i}', 'scraped')
        run, result, scrape_mock, push_mock = self._run(rs.KIND_SCRAPE, plan=[{'c0', 'c1', 'c2'}, {'c0'}, set()])
        self.assertEqual(scrape_mock.call_count, 3)
        self.assertEqual(result['failed_after'], [3, 1, 0])
        self.assertEqual(result['cycles_run'], 2)
        self.assertEqual(run.status, 'success')
        push_mock.assert_not_called()

    def test_update_pushes_once_at_the_end(self):
        self._mapping('c1', 'synced')
        run, result, scrape_mock, push_mock = self._run(rs.KIND_UPDATE, plan=[{'c1'}, {'c1'}, {'c1'}])
        self.assertEqual(scrape_mock.call_count, 3)  # first pass + 2 cycles (SyncSchedule has 2)
        self.assertEqual(result['failed_after'], [1, 1, 1])
        push_mock.assert_called_once()
        self.assertEqual(push_mock.call_args.kwargs['vendor_id'], None)
        self.assertEqual(result['push'], 'done')
        self.assertEqual(run.status, 'partial')
        self.update.refresh_from_db()
        self.assertIsNotNone(self.update.last_run)

    def test_store_wide_run_leaves_out_vendors_with_own_times(self):
        self._mapping('c1', 'scraped', self.costco)
        a1 = self._mapping('a1', 'scraped', self.amazon)
        self._vendor_row(self.amazon, rs.KIND_UPDATE)
        run, result, _, push_mock = self._run(rs.KIND_UPDATE)
        a1.refresh_from_db()
        self.assertEqual(a1.sync_status, 'scraped')  # not reset, not scraped by this run
        self.assertEqual(result['reset_all'], 1)
        self.assertEqual(push_mock.call_args.kwargs['exclude_vendor_ids'], [self.amazon.id])

    def test_scrape_vendor_rows_do_not_change_the_update_scope(self):
        self._mapping('c1', 'scraped', self.costco)
        self._mapping('a1', 'scraped', self.amazon)
        self._vendor_row(self.amazon, rs.KIND_SCRAPE)  # own Auto Scraping times only
        _, result, _, push_mock = self._run(rs.KIND_UPDATE)
        self.assertEqual(result['reset_all'], 2)
        self.assertIsNone(push_mock.call_args.kwargs['exclude_vendor_ids'])

    def test_vendor_run_touches_only_that_vendor(self):
        c1 = self._mapping('c1', 'scraped', self.costco)
        self._mapping('a1', 'scraped', self.amazon)
        self._vendor_row(self.amazon, rs.KIND_UPDATE, cycles=1)
        run, result, scrape_mock, push_mock = self._run(rs.KIND_UPDATE, vendor=self.amazon, plan=[{'a1'}, set()])
        c1.refresh_from_db()
        self.assertEqual(c1.sync_status, 'scraped')
        self.assertEqual(result['reset_all'], 1)
        self.assertEqual(result['failed_after'], [1, 0])
        self.assertEqual(push_mock.call_args.kwargs['vendor_id'], self.amazon.id)
        self.assertEqual(run.status, 'success')

    def test_user_stop_still_pushes_for_update_but_not_when_scrape_still_running(self):
        self._mapping('c1', 'scraped')
        run, result, _, push_mock = self._run(rs.KIND_UPDATE, stop={'stop': 'user_cancelled'})
        self.assertEqual(run.status, 'stopped')
        push_mock.assert_called_once()
        ScheduledRun.objects.all().delete()
        run, result, _, push_mock = self._run(rs.KIND_UPDATE, stop={'stop': 'scrape_still_running'})
        push_mock.assert_not_called()
        self.assertEqual(result['push'], 'skipped_scrape_still_running')

    def test_not_connected_store_skips_push(self):
        Store.objects.filter(pk=self.store.pk).update(connection_status='pending')
        self._mapping('c1', 'scraped')
        _, result, _, push_mock = self._run(rs.KIND_UPDATE)
        push_mock.assert_not_called()
        self.assertEqual(result['push'], 'skipped_not_connected')

    def test_turned_off_before_start_is_skipped(self):
        self._mapping('c1', 'scraped')
        AutoScrapeSchedule.objects.filter(pk=self.auto.pk).update(is_active=False)
        run, result, scrape_mock, _ = self._run(rs.KIND_SCRAPE)
        self.assertEqual(run.status, 'skipped')
        scrape_mock.assert_not_called()

    def test_nothing_in_scope_finishes_without_scraping(self):
        self._mapping('a1', 'scraped', self.amazon)
        self._vendor_row(self.amazon, rs.KIND_SCRAPE)
        run, result, scrape_mock, _ = self._run(rs.KIND_SCRAPE)
        self.assertEqual((run.status, result['stop_reason']), ('success', 'nothing_in_scope'))
        scrape_mock.assert_not_called()


class PushHoldTests(Base):
    def _fail_rows(self):
        from catalog.scrape_failure import apply_no_vendor_price_fallback, fail_product_mapping

        pm = self._mapping('f1', 'pending')
        ProductMapping.objects.filter(pk=pm.pk).update(store_price=50, store_stock=7)
        pm.refresh_from_db()
        pm2 = self._mapping('n1', 'pending')
        with patch('catalog.marketplace_push.push_product_mapping_to_marketplace', return_value=(True, None)) as push, \
                patch('sync.tasks._build_store_vendor_pricing_inventory_caches', return_value=({}, None, {}, None)):
            fail_product_mapping(pm, 'scrape_failed', store=self.store)
            apply_no_vendor_price_fallback(pm2, store=self.store, price_by_vendor_id={}, price_fallback=None)
        pm.refresh_from_db()
        self.assertEqual((pm.store_stock, pm.sync_status), (0, 'failed'))
        return push

    def test_no_per_row_push_during_a_run(self):
        self._running(rs.KIND_UPDATE)
        self._fail_rows().assert_not_called()

    def test_per_row_push_as_before_without_a_run(self):
        self.assertEqual(self._fail_rows().call_count, 2)


class ManualSyncExcludeTests(Base):
    def test_exclude_vendor_ids(self):
        from catalog.marketplace_push import manual_sync_mappings

        c1 = self._mapping('c1', 'scraped', self.costco)
        a1 = self._mapping('a1', 'scraped', self.amazon)
        ProductMapping.objects.filter(pk__in=[c1.pk, a1.pk]).update(store_price=10, marketplace_id='m')
        ids = set(manual_sync_mappings(self.store, exclude_vendor_ids=[self.amazon.id]).values_list('id', flat=True))
        self.assertEqual(ids, {c1.id})


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class SerializerTests(Base):
    def _ser(self):
        from rest_framework.test import APIRequestFactory

        from stores.serializers import StoreSerializer

        req = APIRequestFactory().get('/')
        req.user = self.user
        return StoreSerializer, {'request': req}

    def _read(self):
        cls, ctx = self._ser()
        return cls(Store.objects.get(pk=self.store.pk), context=ctx).data

    def test_save_and_read_both_cards_with_vendor_times(self):
        cls, ctx = self._ser()
        ser = cls(context=ctx)
        ser._save_sync_schedule(self.store, {
            'enabled': True, 'run_times': ['22:00', '10:00'], 'timezone': 'Australia/Sydney',
            'retry_failed_cycles': 3,
            'vendors': [{'vendor_id': str(self.costco.id), 'run_times': ['07:30'], 'retry_failed_cycles': 1}],
        }, SyncSchedule)
        ser._save_auto_scrape(self.store, {
            'enabled': True, 'run_times': ['05:00'], 'timezone': 'Australia/Sydney', 'retry_failed_cycles': 4,
            'vendors': [{'vendor_id': str(self.amazon.id), 'run_times': ['04:00', '16:00']}],
        })
        data = self._read()
        self.assertEqual(data['sync_schedule']['run_times'], ['10:00', '22:00'])
        self.assertEqual(data['sync_schedule']['retry_failed_cycles'], 3)
        self.assertEqual(data['sync_schedule']['crontab_hour'], '10')
        self.assertEqual(data['auto_scrape']['retry_failed_cycles'], 4)
        upd = data['vendor_schedules']['update']
        scr = data['vendor_schedules']['scrape']
        self.assertEqual([(r['vendor_id'], r['run_times'], r['retry_failed_cycles']) for r in upd],
                         [(str(self.costco.id), ['07:30'], 1)])
        self.assertEqual([(r['vendor_id'], r['run_times']) for r in scr], [(str(self.amazon.id), ['04:00', '16:00'])])

        # Removing a vendor row deletes it; turning Scheduled Updates off keeps vendor rows.
        ser._save_sync_schedule(self.store, {
            'enabled': True, 'run_times': ['10:00'], 'timezone': 'Australia/Sydney', 'vendors': [],
        }, SyncSchedule)
        self.assertFalse(VendorSchedule.objects.filter(store=self.store, kind='update').exists())
        ser._save_sync_schedule(self.store, {'enabled': False}, SyncSchedule)
        self.assertFalse(SyncSchedule.objects.filter(store=self.store).exists())
        self.assertTrue(VendorSchedule.objects.filter(store=self.store, kind='scrape').exists())

    def test_saving_does_not_fire_a_run_time_that_just_passed(self):
        cls, ctx = self._ser()
        ser = cls(context=ctx)
        now_local = timezone.now().astimezone(SYD)
        just_passed = (now_local - timedelta(minutes=1)).strftime('%H:%M')
        ser._save_auto_scrape(self.store, {
            'enabled': True, 'run_times': [just_passed], 'timezone': 'Australia/Sydney',
            'vendors': [{'vendor_id': str(self.costco.id), 'run_times': [just_passed]}],
        })
        self.assertEqual(rs.check_due(rs.KIND_SCRAPE), [])

    def test_validation_errors(self):
        from rest_framework.exceptions import ValidationError

        cls, ctx = self._ser()
        ser = cls(context=ctx)
        with self.assertRaises(ValidationError):
            ser._validate_schedule_payloads({'auto_scrape': {
                'enabled': True, 'run_times': ['05:00'],
                'vendors': [{'vendor_id': '00000000-0000-0000-0000-000000000000', 'run_times': ['04:00']}],
            }}, 'inventory_only')
        with self.assertRaises(ValidationError):
            ser._validate_schedule_payloads({'sync_schedule': {
                'enabled': True, 'run_times': ['05:00'],
                'vendors': [{'vendor_id': str(self.costco.id), 'run_times': ['04:00']}],
            }}, 'full_store')
        with self.assertRaises(ValidationError):
            ser._validate_schedule_payloads({'sync_schedule': {'enabled': True, 'run_times': ['99:00']}},
                                            'inventory_only')
        # Off or absent is fine.
        ser._validate_schedule_payloads({'auto_scrape': {'enabled': False}}, 'inventory_only')


class MydealBotUploadTests(Base):
    """MyDeal inventory-only stores: a Scheduled Update queues the inventory bot upload."""

    def setUp(self):
        super().setUp()
        mydeal, _ = Marketplace.objects.get_or_create(code='mydeal', defaults={'name': 'MyDeal'})
        Store.objects.filter(pk=self.store.pk).update(marketplace=mydeal)
        self.store.refresh_from_db()
        self.row_no = 0

    def _template(self, sku):
        from catalog.models import MydealTemplateRow

        for kind in (MydealTemplateRow.Kind.INVENTORY, MydealTemplateRow.Kind.PRICE):
            self.row_no += 1
            MydealTemplateRow.objects.create(
                store=self.store, kind=kind, row_number=self.row_no, deal_id='D', variant_id='V', sku=sku,
            )

    def _run(self, vendor=None):
        run = self._running(rs.KIND_UPDATE, vendor)

        def scrape(_store, _source, _hb):
            ProductMapping.objects.filter(store=self.store, sync_status='pending').update(sync_status='scraped')
            return {}

        with patch.object(rs, '_catalog_scrape_pass', side_effect=scrape), \
                patch('sync.tasks._execute_store_push_listings_only') as server_push:
            result = rs.execute_run(str(run.id))
        server_push.assert_not_called()  # MyDeal has no server push
        run.refresh_from_db()
        return run, result

    def test_store_wide_update_queues_upload_without_own_time_vendors_or_unmapped(self):
        from catalog.models import MydealUploadRun

        self._mapping('c1', 'scraped', self.costco)
        self._mapping('a1', 'scraped', self.amazon)
        self._template('c1')
        self._template('a1')
        self._template('not-in-catalog')  # Unmapped group
        self._vendor_row(self.amazon, rs.KIND_UPDATE)
        run, result = self._run()
        self.assertEqual(result['push'], 'queued')
        self.assertEqual(run.status, 'success')
        upload = MydealUploadRun.objects.get(pk=result['upload_run_id'])
        self.assertEqual([v['id'] for v in upload.vendors], [str(self.costco.id)])
        self.assertEqual(sorted(upload.kinds), ['inventory', 'price'])
        self.assertIsNone(upload.user)
        self.assertEqual(upload.status, MydealUploadRun.Status.PENDING)

    def test_vendor_update_uploads_only_that_vendor(self):
        from catalog.models import MydealUploadRun

        self._mapping('c1', 'scraped', self.costco)
        self._mapping('a1', 'scraped', self.amazon)
        self._template('c1')
        self._template('a1')
        self._vendor_row(self.amazon, rs.KIND_UPDATE)
        _, result = self._run(vendor=self.amazon)
        upload = MydealUploadRun.objects.get(pk=result['upload_run_id'])
        self.assertEqual([v['id'] for v in upload.vendors], [str(self.amazon.id)])

    def test_upload_already_waiting_is_skipped_not_failed(self):
        self._mapping('c1', 'scraped', self.costco)
        self._template('c1')
        _, first = self._run()
        self.assertEqual(first['push'], 'queued')
        run, second = self._run()
        self.assertEqual(second['push'], 'skipped_upload_already_queued')
        self.assertEqual(run.status, 'success')

    def test_no_template_rows(self):
        self._mapping('c1', 'scraped', self.costco)
        _, result = self._run()
        self.assertEqual(result['push'], 'skipped_no_template_rows')

    def test_auto_scraping_still_never_uploads(self):
        from catalog.models import MydealUploadRun

        self._mapping('c1', 'scraped', self.costco)
        self._template('c1')
        run = self._running(rs.KIND_SCRAPE)
        with patch.object(rs, '_catalog_scrape_pass', return_value={}):
            rs.execute_run(str(run.id))
        self.assertFalse(MydealUploadRun.objects.exists())
