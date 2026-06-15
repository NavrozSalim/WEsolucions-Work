"""Tests for marketplace push progress payload."""
from __future__ import annotations

from django.test import SimpleTestCase

from catalog.push_listings_progress import _infer_sync_step


class PushListingsProgressTests(SimpleTestCase):
    def test_infer_sync_step_waiting_sears(self):
        self.assertEqual(
            _infer_sync_step('Waiting for Sears processing report 123', {}),
            'waiting_sears',
        )

    def test_infer_sync_step_queue_build(self):
        self.assertEqual(
            _infer_sync_step('preparing bulk push — 100 of 200 queued', {}),
            'queue_build',
        )

    def test_infer_sync_step_bulk_push_from_message(self):
        self.assertEqual(
            _infer_sync_step('batch 2 of 10 listings', {}),
            'bulk_push',
        )

    def test_infer_sync_step_prefers_metadata(self):
        self.assertEqual(
            _infer_sync_step('anything', {'sync_step': 'waiting_sears'}),
            'waiting_sears',
        )
