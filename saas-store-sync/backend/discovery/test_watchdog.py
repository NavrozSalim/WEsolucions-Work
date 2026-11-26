"""Discovery jobs whose worker died: Stop finishes them, and the watchdog resumes or closes them."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from discovery.locks import discovery_lock_key, refresh_discovery_lock
from discovery.models import DiscoveryJob, DiscoveryProduct
from discovery.watchdog import revive_stalled_discovery_jobs

User = get_user_model()


class DiscoveryWatchdogTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username='discovery_wd', email='discovery_wd@example.com', password='pass12345',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.send = patch('discovery.tasks.run_discovery_job.apply_async').start()
        self.send.return_value.id = 'resent-task'
        self.addCleanup(patch.stopall)

    def _job(self, *, quiet_minutes=60, **fields):
        job = DiscoveryJob.objects.create(
            owner=self.user,
            marketplace='amazon_au',
            mode=DiscoveryJob.Mode.PRODUCT,
            status=DiscoveryJob.Status.RUNNING,
            queue_name='discover-au',
            original_filename='Get Inventory.xlsx',
            **fields,
        )
        then = timezone.now() - timedelta(minutes=quiet_minutes)
        DiscoveryJob.objects.filter(pk=job.pk).update(created_at=then, started_at=then)
        return DiscoveryJob.objects.get(pk=job.pk)

    def _product(self, job, key, minutes_ago):
        row = DiscoveryProduct.objects.create(job=job, product_key=key, data={'asin': key})
        DiscoveryProduct.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(minutes=minutes_ago))

    # --- watchdog -----------------------------------------------------------

    def test_dead_running_job_is_resumed_on_its_region_worker(self):
        job = self._job()
        self._product(job, 'B0SAVED001', 60)
        out = revive_stalled_discovery_jobs()
        self.assertEqual(out, [{'job_id': str(job.id), 'action': 'resumed', 'saved': 1}])
        self.send.assert_called_once_with(args=[str(job.id)], queue='discover-au')
        job.refresh_from_db()
        self.assertEqual(job.status, DiscoveryJob.Status.RUNNING)
        self.assertEqual(job.stats['watchdog_revivals'], 1)

    def test_job_with_a_live_worker_is_left_alone(self):
        job = self._job()
        cache.set(discovery_lock_key(job.id), 'worker-1', 180)
        self.assertEqual(revive_stalled_discovery_jobs(), [])
        self.send.assert_not_called()

    def test_recent_progress_is_left_alone(self):
        job = self._job()
        self._product(job, 'B0FRESH001', 2)
        self.assertEqual(revive_stalled_discovery_jobs(), [])

    def test_dead_job_whose_stop_was_asked_is_closed(self):
        job = self._job(cancel_requested=True)
        with patch('discovery.queue.promote_after_job') as promote:
            out = revive_stalled_discovery_jobs()
        self.assertEqual(out[0]['action'], 'closed')
        job.refresh_from_db()
        self.assertEqual(job.status, DiscoveryJob.Status.CANCELLED)
        self.assertIsNotNone(job.finished_at)
        promote.assert_called_once()
        self.send.assert_not_called()

    def test_gives_up_after_restarts_that_save_nothing(self):
        sent = (timezone.now() - timedelta(minutes=60)).isoformat()
        job = self._job(stats={'watchdog_sent_at': sent, 'watchdog_saved_at_send': 0, 'watchdog_empty_revivals': 2})
        with patch('discovery.queue.promote_after_job'):
            out = revive_stalled_discovery_jobs()
        self.assertEqual(out[0]['action'], 'gave_up')
        job.refresh_from_db()
        self.assertEqual(job.status, DiscoveryJob.Status.CANCELLED)
        self.assertIn('Click Continue', job.error_message)
        self.send.assert_not_called()

    def test_progress_since_the_last_restart_resets_the_count(self):
        sent = (timezone.now() - timedelta(minutes=60)).isoformat()
        job = self._job(stats={'watchdog_sent_at': sent, 'watchdog_saved_at_send': 0, 'watchdog_empty_revivals': 2})
        self._product(job, 'B0NEW00001', 50)
        out = revive_stalled_discovery_jobs()
        self.assertEqual(out[0]['action'], 'resumed')
        job.refresh_from_db()
        self.assertEqual(job.stats['watchdog_empty_revivals'], 0)

    def test_a_job_just_resent_gets_time_to_start(self):
        sent = (timezone.now() - timedelta(minutes=3)).isoformat()
        self._job(stats={'watchdog_sent_at': sent, 'watchdog_saved_at_send': 0})
        self.assertEqual(revive_stalled_discovery_jobs(), [])

    # --- Stop ---------------------------------------------------------------

    def test_stop_finishes_a_running_job_with_no_worker(self):
        job = self._job()
        with patch('discovery.queue.promote_after_job'):
            res = self.client.post(f'/api/v1/discovery/jobs/{job.id}/cancel/')
        self.assertEqual(res.status_code, 200, res.content)
        job.refresh_from_db()
        self.assertEqual(job.status, DiscoveryJob.Status.CANCELLED)
        # Continue is available right away.
        res = self.client.post(f'/api/v1/discovery/jobs/{job.id}/resume/')
        self.assertEqual(res.status_code, 200, res.content)

    def test_stop_on_a_live_job_still_asks_its_worker(self):
        job = self._job()
        cache.set(discovery_lock_key(job.id), 'worker-1', 180)
        self.client.post(f'/api/v1/discovery/jobs/{job.id}/cancel/')
        job.refresh_from_db()
        self.assertEqual(job.status, DiscoveryJob.Status.RUNNING)
        self.assertTrue(job.cancel_requested)

    # --- lock ---------------------------------------------------------------

    def test_live_worker_takes_back_a_lapsed_lock(self):
        key = discovery_lock_key('job-x')
        refresh_discovery_lock('job-x', 'worker-1', force=True)
        self.assertEqual(cache.get(key), 'worker-1')

    def test_live_worker_never_takes_another_workers_lock(self):
        key = discovery_lock_key('job-y')
        cache.set(key, 'worker-2', 180)
        refresh_discovery_lock('job-y', 'worker-1', force=True)
        self.assertEqual(cache.get(key), 'worker-2')
