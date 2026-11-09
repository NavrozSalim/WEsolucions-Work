"""Costco AU routing follows the AU worker's proxies, not the main server's env."""
import os
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from catalog.costco_mode import (
    COSTCO_AU_SERVER_CACHE_KEY,
    costco_au_runs_on_server,
    publish_costco_au_worker_mode,
    reset_costco_au_mode_memo,
)
from catalog.tasks import _ingest_only_vendor_ids, _is_ingest_only_product
from catalog.views import CatalogScrapeTriggerView
from vendor.models import Vendor

_NO_PROXY_ENV = {
    k: '' for k in (
        'COSTCO_AU_PROXY_URLS', 'PROXY_URLS', 'COSTCO_AU_PROXY_URL', 'PROXY_URL', 'PROXY_ENDPOINTS',
    )
}
_AU_PROXY_ENV = {**_NO_PROXY_ENV, 'COSTCO_AU_PROXY_URLS': 'http://u:p@10.0.0.1:8000,http://u:p@10.0.0.2:8000'}


class CostcoModeTests(TestCase):
    def setUp(self):
        cache.delete(COSTCO_AU_SERVER_CACHE_KEY)
        reset_costco_au_mode_memo()
        self.costco, _ = Vendor.objects.get_or_create(code='costcoau', defaults={'name': 'CostcoAU'})

    def tearDown(self):
        cache.delete(COSTCO_AU_SERVER_CACHE_KEY)
        reset_costco_au_mode_memo()

    @patch.dict(os.environ, _NO_PROXY_ENV)
    def test_main_server_without_proxies_uses_desktop_until_au_worker_publishes(self):
        self.assertFalse(costco_au_runs_on_server())
        self.assertIn(self.costco.id, _ingest_only_vendor_ids())

        with patch.dict(os.environ, _AU_PROXY_ENV):
            self.assertEqual(publish_costco_au_worker_mode(['heavy-au']), 2)

        # Back on the main server env (no proxies), the shared flag decides.
        self.assertTrue(costco_au_runs_on_server())
        self.assertNotIn(self.costco.id, _ingest_only_vendor_ids())
        self.assertFalse(_is_ingest_only_product(SimpleNamespace(vendor=self.costco)))
        self.assertTrue(CatalogScrapeTriggerView._vendor_runs_live('costco', {'runner': 'desktop'}))

    @patch.dict(os.environ, _NO_PROXY_ENV)
    def test_second_au_worker_without_proxies_keeps_flag(self):
        cache.set(COSTCO_AU_SERVER_CACHE_KEY, 10, timeout=None)
        self.assertEqual(publish_costco_au_worker_mode(['heavy-au']), 0)
        self.assertEqual(cache.get(COSTCO_AU_SERVER_CACHE_KEY), 10)
        self.assertTrue(costco_au_runs_on_server())

    @patch.dict(os.environ, _NO_PROXY_ENV)
    def test_worker_without_proxies_skips_costco_rows_even_when_flag_set(self):
        cache.set(COSTCO_AU_SERVER_CACHE_KEY, 10, timeout=None)
        product = SimpleNamespace(vendor=self.costco)
        # Routing on the main server says "server"...
        self.assertFalse(_is_ingest_only_product(product))
        # ...but a heavy-au worker without its own proxies leaves the row Pending.
        self.assertTrue(_is_ingest_only_product(product, here=True))
        self.assertIn(self.costco.id, _ingest_only_vendor_ids(here=True))

    @patch.dict(os.environ, _AU_PROXY_ENV)
    def test_worker_with_proxies_scrapes_costco_rows(self):
        product = SimpleNamespace(vendor=self.costco)
        self.assertFalse(_is_ingest_only_product(product, here=True))
        self.assertNotIn(self.costco.id, _ingest_only_vendor_ids(here=True))

    @patch.dict(os.environ, _NO_PROXY_ENV)
    def test_other_workers_do_not_touch_flag(self):
        cache.set(COSTCO_AU_SERVER_CACHE_KEY, 10, timeout=None)
        self.assertIsNone(publish_costco_au_worker_mode(['light', 'sync']))
        self.assertIsNone(publish_costco_au_worker_mode(['heavy-us']))
        self.assertEqual(cache.get(COSTCO_AU_SERVER_CACHE_KEY), 10)

    @patch.dict(os.environ, _AU_PROXY_ENV)
    def test_local_proxies_still_count(self):
        self.assertTrue(costco_au_runs_on_server())

    @patch.dict(os.environ, _NO_PROXY_ENV)
    def test_worker_ready_signal_publishes(self):
        from core.celery import _publish_worker_scrape_capabilities

        fake_app = SimpleNamespace(
            amqp=SimpleNamespace(queues=SimpleNamespace(consume_from={'heavy-au': object()})),
        )
        with patch.dict(os.environ, _AU_PROXY_ENV):
            _publish_worker_scrape_capabilities(sender=SimpleNamespace(app=fake_app))
        self.assertEqual(cache.get(COSTCO_AU_SERVER_CACHE_KEY), 2)
