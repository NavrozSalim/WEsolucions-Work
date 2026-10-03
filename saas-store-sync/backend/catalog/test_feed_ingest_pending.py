"""Catalog feed vendors follow Inventory management Start Scraping rules."""
from decimal import Decimal
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from catalog.models import HebScrapeJob, ProductMapping
from catalog.tasks import run_vevor_au_ingest
from marketplace.models import Marketplace
from products.models import Product
from stores.models import Store
from vendor.models import Vendor


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class VevorPendingFeedIngestTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username='vevor_pending', email='vevor_pending@example.com', password='pass12345',
        )
        mp, _ = Marketplace.objects.get_or_create(code='kogan_vevor_p', defaults={'name': 'Kogan'})
        self.store = Store.objects.create(
            user=self.user, name='Vevor Pending', region='AU', api_token='tok-vp',
            marketplace=mp, connection_status='connected',
        )
        self.vendor, _ = Vendor.objects.get_or_create(code='vevorau', defaults={'name': 'VevorAU'})

    def _mapping(self, sku, status, **extra):
        product = Product.objects.create(vendor=self.vendor, vendor_sku=sku, owner=self.user)
        return ProductMapping.objects.create(
            store=self.store, product=product, marketplace_id=f'MID-{sku}',
            sync_status=status, is_active=True, **extra,
        )

    @patch('catalog.tasks._fail_mapping')
    @patch('scrapers.vevor_au_ingest.load_vevor_feed_lookups')
    def test_start_scraping_only_touches_pending_and_batches_misses(self, mock_feed, mock_fail):
        entry = {'Posted Price': 50.0, 'Posted Inventory': 6}
        mock_feed.return_value = {
            'lookup': {'VEV-HIT': entry, 'VEV-DONE': entry},
            'lookup_compact': {},
            'lookup_by_url': {},
            'feed_rows': 2,
        }
        hit = self._mapping('VEV-HIT', 'pending')
        miss = self._mapping(
            'VEV-MISS', 'pending', store_price=Decimal('30.00'), store_stock=3,
        )
        done = self._mapping('VEV-DONE', 'scraped', store_price=Decimal('11.00'), store_stock=1)
        job = HebScrapeJob.objects.create(
            store=self.store, requested_by=self.user, vendor_code='vevor',
            status=HebScrapeJob.Status.CLAIMED,
        )

        out = run_vevor_au_ingest(store_id=str(self.store.id), job_id=str(job.id))

        mock_fail.assert_not_called()
        self.assertEqual(out['status'], 'ok')
        self.assertEqual(out['matched'], 1)
        self.assertEqual(out['missing'], 1)
        self.assertEqual(out['listing_count'], 2)
        hit.refresh_from_db()
        miss.refresh_from_db()
        done.refresh_from_db()
        self.assertEqual(hit.sync_status, 'scraped')
        self.assertEqual(float(hit.store_price), 50.0)
        self.assertEqual(int(hit.store_stock), 6)
        self.assertEqual(miss.sync_status, 'failed')
        self.assertEqual(int(miss.store_stock), 0)
        self.assertIn('vevor_feed_sku_missing', miss.scrape_error or '')
        self.assertEqual(float(done.store_price), 11.0)
        job.refresh_from_db()
        self.assertEqual(job.status, HebScrapeJob.Status.DONE)
        self.assertEqual(job.stats['total'], 2)
        self.assertEqual(job.stats['processed'], 2)
        self.assertEqual(job.stats['scraped'], 1)
        self.assertEqual(job.stats['failed'], 1)

    @patch('scrapers.vevor_au_ingest.load_vevor_feed_lookups')
    def test_cancelled_job_keeps_cancelled_status(self, mock_feed):
        mock_feed.return_value = {
            'lookup': {'VEV-HIT': {'Posted Price': 5, 'Posted Inventory': 1}},
            'lookup_compact': {},
            'lookup_by_url': {},
            'feed_rows': 1,
        }
        self._mapping('VEV-HIT', 'pending')
        job = HebScrapeJob.objects.create(
            store=self.store, requested_by=self.user, vendor_code='vevor',
            status=HebScrapeJob.Status.CANCELLED,
        )
        run_vevor_au_ingest(store_id=str(self.store.id), job_id=str(job.id))
        job.refresh_from_db()
        self.assertEqual(job.status, HebScrapeJob.Status.CANCELLED)
