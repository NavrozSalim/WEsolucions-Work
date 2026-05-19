"""Multi-tenant isolation for catalog sync and desktop ingest helpers."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from catalog.ingest_views import _apply_to_mappings
from catalog.models import CatalogUpload, CatalogUploadRow, HebScrapeJob, ProductMapping
from catalog.scrape_progress import build_scrape_progress_payload, heal_stale_server_vendor_job
from catalog.tasks import (
    run_catalog_sync,
    run_store_wide_catalog_scrape,
    run_vevor_au_ingest,
    store_has_scrapeable_pending_mappings,
)
from marketplace.models import Marketplace
from products.models import Product
from stores.models import Store
from vendor.models import Vendor

User = get_user_model()


def _noop_reset(*_a, **_k):
    return {'batches': 0, 'rows_updated': 0, 'elapsed_ms': 0}


class CatalogSyncMultiTenantTests(TestCase):
    def setUp(self):
        self.mp, _ = Marketplace.objects.get_or_create(
            code='kogan_mt',
            defaults={'name': 'Kogan MT'},
        )
        self.vendor = Vendor.objects.get(code='amazonau')
        self.user_a = User.objects.create_user(
            username='ua_mt', email='ua_mt@example.com', password='pass12345',
        )
        self.user_b = User.objects.create_user(
            username='ub_mt', email='ub_mt@example.com', password='pass12345',
        )
        self.store_a = Store.objects.create(
            user=self.user_a,
            name='Store Alpha',
            region='AU',
            api_token='tok-a',
            marketplace=self.mp,
        )
        self.store_b = Store.objects.create(
            user=self.user_b,
            name='Store Beta',
            region='AU',
            api_token='tok-b',
            marketplace=self.mp,
        )

    def _make_upload(self, *, user, store, mid: str, vsku: str):
        up = CatalogUpload.objects.create(
            user=user,
            store=store,
            original_filename='t.csv',
            status=CatalogUpload.Status.VALIDATED,
            total_rows=1,
        )
        CatalogUploadRow.objects.create(
            catalog_upload=up,
            row_number=2,
            vendor_name_raw='AmazonAU',
            store_name_raw=store.name,
            marketplace_parent_sku_raw='',
            marketplace_child_sku_raw='',
            marketplace_id_raw=mid,
            vendor_sku_raw=vsku,
            vendor_id_raw='',
            vendor_url_raw='https://www.amazon.com.au/dp/B0TESTMT1',
            action_raw='Add',
            vendor=self.vendor,
        )
        return up

    @patch('catalog.tasks._chunked_reset_store_active_listings_pending_scrape', side_effect=_noop_reset)
    def test_two_users_same_marketplace_sku_both_get_listings(self, _mock_reset):
        mid = 'SHARED-MID-99999'
        vsku = 'SHARED-VSKU-888'
        ua = self._make_upload(user=self.user_a, store=self.store_a, mid=mid, vsku=vsku)
        ub = self._make_upload(user=self.user_b, store=self.store_b, mid=mid, vsku=vsku)

        ra = run_catalog_sync(str(ua.id))
        rb = run_catalog_sync(str(ub.id))

        self.assertEqual(ra.get('errors', 0), 0, ra)
        self.assertEqual(rb.get('errors', 0), 0, rb)
        self.assertEqual(ra.get('added'), 1, ra)
        self.assertEqual(rb.get('added'), 1, rb)

        self.assertEqual(ProductMapping.objects.filter(store=self.store_a).count(), 1)
        self.assertEqual(ProductMapping.objects.filter(store=self.store_b).count(), 1)
        self.assertEqual(
            Product.objects.filter(vendor=self.vendor, vendor_sku=vsku).count(),
            2,
        )
        self.assertEqual(
            Product.objects.filter(
                vendor=self.vendor, vendor_sku=vsku, owner=self.user_a,
            ).count(),
            1,
        )
        self.assertEqual(
            Product.objects.filter(
                vendor=self.vendor, vendor_sku=vsku, owner=self.user_b,
            ).count(),
            1,
        )

    @patch('catalog.tasks._chunked_reset_store_active_listings_pending_scrape', side_effect=_noop_reset)
    def test_replace_store_catalog_deactivates_existing_listings(self, _mock_reset):
        mid_old = 'OLD-MID-1'
        mid_new = 'NEW-MID-2'
        u_old = self._make_upload(
            user=self.user_a, store=self.store_a, mid=mid_old, vsku='VSKU-OLD',
        )
        run_catalog_sync(str(u_old.id))
        self.assertEqual(
            ProductMapping.objects.filter(store=self.store_a, is_active=True).count(),
            1,
        )

        u_new = self._make_upload(
            user=self.user_a, store=self.store_a, mid=mid_new, vsku='VSKU-NEW',
        )
        result = run_catalog_sync(str(u_new.id), replace_store_catalog=True)
        self.assertEqual(result.get('replace_deactivated'), 1)
        self.assertEqual(
            ProductMapping.objects.filter(store=self.store_a, is_active=True).count(),
            1,
        )
        self.assertFalse(
            ProductMapping.objects.filter(
                store=self.store_a, marketplace_id=mid_old, is_active=True,
            ).exists(),
        )

    @patch('catalog.tasks._chunked_reset_store_active_listings_pending_scrape', side_effect=_noop_reset)
    def test_same_store_second_sync_updates_existing(self, _mock_reset):
        mid = 'MID-ONLY-ONE-STORE'
        vsku = 'VSKU-ONE'
        u1 = self._make_upload(user=self.user_a, store=self.store_a, mid=mid, vsku=vsku)
        r1 = run_catalog_sync(str(u1.id))
        self.assertEqual(r1.get('added'), 1)
        self.assertEqual(r1.get('updated', 0), 0)

        u2 = self._make_upload(user=self.user_a, store=self.store_a, mid=mid, vsku=vsku)
        r2 = run_catalog_sync(str(u2.id))
        self.assertEqual(r2.get('added', 0), 0)
        self.assertGreaterEqual(r2.get('updated', 0), 1)
        self.assertEqual(ProductMapping.objects.filter(store=self.store_a).count(), 1)

    @patch('catalog.tasks._chunked_reset_store_active_listings_pending_scrape', side_effect=_noop_reset)
    def test_upload_user_must_match_store_owner(self, _mock_reset):
        mid = 'MID-MISMATCH'
        vsku = 'VSKU-MISMATCH'
        up = CatalogUpload.objects.create(
            user=self.user_b,
            store=self.store_a,
            original_filename='bad.csv',
            status=CatalogUpload.Status.VALIDATED,
            total_rows=1,
        )
        CatalogUploadRow.objects.create(
            catalog_upload=up,
            row_number=2,
            vendor_name_raw='AmazonAU',
            store_name_raw=self.store_a.name,
            marketplace_id_raw=mid,
            vendor_sku_raw=vsku,
            vendor_url_raw='https://www.amazon.com.au/dp/B0TESTMT2',
            action_raw='Add',
            vendor=self.vendor,
        )
        out = run_catalog_sync(str(up.id))
        self.assertEqual(out.get('error'), 'upload_store_user_mismatch')


class IngestApplyTenantTests(TestCase):
    def setUp(self):
        self.mp, _ = Marketplace.objects.get_or_create(
            code='kogan_ing',
            defaults={'name': 'Kogan Ingest'},
        )
        self.vendor = Vendor.objects.get(code='hebus')
        self.user_a = User.objects.create_user(
            username='ing_ua', email='ing_ua@example.com', password='pass12345',
        )
        self.user_b = User.objects.create_user(
            username='ing_ub', email='ing_ub@example.com', password='pass12345',
        )
        self.store_a = Store.objects.create(
            user=self.user_a, name='SA', region='USA', api_token='x', marketplace=self.mp,
        )
        self.store_b = Store.objects.create(
            user=self.user_b, name='SB', region='USA', api_token='y', marketplace=self.mp,
        )
        self.product_a = Product.objects.create(
            vendor=self.vendor,
            owner=self.user_a,
            vendor_sku='HEB-URL-1',
            variation_id='',
            vendor_url='https://www.heb.com/product/test-ingest-tenant',
        )
        self.product_b = Product.objects.create(
            vendor=self.vendor,
            owner=self.user_b,
            vendor_sku='HEB-URL-1',
            variation_id='',
            vendor_url='https://www.heb.com/product/test-ingest-tenant',
        )
        self.pm_a = ProductMapping.objects.create(
            store=self.store_a,
            product=self.product_a,
            marketplace_child_sku='c1',
            is_active=True,
        )
        self.pm_b = ProductMapping.objects.create(
            store=self.store_b,
            product=self.product_b,
            marketplace_child_sku='c2',
            is_active=True,
        )

    def test_apply_to_mappings_restricts_to_one_user(self):
        n = _apply_to_mappings(
            self.product_a,
            Decimal('9.99'),
            5,
            'Title',
            restrict_to_user_id=self.user_a.id,
        )
        self.assertEqual(n, 1)
        self.pm_a.refresh_from_db()
        self.pm_b.refresh_from_db()
        self.assertEqual(self.pm_a.sync_status, 'scraped')
        self.assertNotEqual(self.pm_b.sync_status, 'scraped')


class VevorIngestTenantTests(TestCase):
    def test_vevor_requires_store_id(self):
        out = run_vevor_au_ingest(store_id=None)
        self.assertEqual(out.get('status'), 'skipped')
        self.assertEqual(out.get('updated'), 0)

    @override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
    @patch('scrapers.vevor_au_ingest.fetch_vevor_feed')
    def test_vevor_skips_when_no_pending_rows(self, mock_fetch):
        mp, _ = Marketplace.objects.get_or_create(code='kogan_vevor', defaults={'name': 'Kogan Vevor'})
        user = User.objects.create_user(username='vevor_u', email='vevor_u@example.com', password='pass12345')
        store = Store.objects.create(
            user=user, name='Vevor Store', region='AU', api_token='tok-v', marketplace=mp,
        )
        vendor, _ = Vendor.objects.get_or_create(code='vevorau', defaults={'name': 'VevorAU'})
        product = Product.objects.create(
            vendor=vendor, vendor_sku='VEV-SKU-1', owner=user,
        )
        ProductMapping.objects.create(
            store=store,
            product=product,
            marketplace_id='MID-1',
            sync_status='scraped',
            is_active=True,
        )
        out = run_vevor_au_ingest(store_id=str(store.id))
        self.assertEqual(out.get('status'), 'skipped')
        self.assertEqual(out.get('reason'), 'no_pending_vevor')
        self.assertEqual(out.get('updated'), 0)
        mock_fetch.assert_not_called()

    @override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
    def test_vevor_only_store_has_no_scrapeable_pending(self):
        mp, _ = Marketplace.objects.get_or_create(code='kogan_vevor2', defaults={'name': 'Kogan Vevor'})
        user = User.objects.create_user(username='vevor_u2', email='vevor_u2@example.com', password='pass12345')
        store = Store.objects.create(
            user=user, name='Vevor Only', region='AU', api_token='tok-v2', marketplace=mp,
        )
        vendor, _ = Vendor.objects.get_or_create(code='vevorau', defaults={'name': 'VevorAU'})
        product = Product.objects.create(vendor=vendor, vendor_sku='VEV-SKU-2', owner=user)
        ProductMapping.objects.create(
            store=store, product=product, marketplace_id='MID-2', sync_status='pending', is_active=True,
        )
        self.assertFalse(store_has_scrapeable_pending_mappings(store))

    @override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
    def test_store_scrape_skips_when_only_vevor_pending(self):
        mp, _ = Marketplace.objects.get_or_create(code='kogan_vevor3', defaults={'name': 'Kogan Vevor'})
        user = User.objects.create_user(username='vevor_u3', email='vevor_u3@example.com', password='pass12345')
        store = Store.objects.create(
            user=user, name='Vevor Skip', region='AU', api_token='tok-v3', marketplace=mp,
        )
        vendor, _ = Vendor.objects.get_or_create(code='vevorau', defaults={'name': 'VevorAU'})
        product = Product.objects.create(vendor=vendor, vendor_sku='VEV-SKU-3', owner=user)
        ProductMapping.objects.create(
            store=store, product=product, marketplace_id='MID-3', sync_status='pending', is_active=True,
        )
        out = run_store_wide_catalog_scrape(str(store.id))
        self.assertTrue(out.get('skipped'))
        self.assertEqual(out.get('reason'), 'no_scrapeable_pending')
        self.assertEqual(out.get('rows_processed'), 0)

    @override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
    def test_heal_stale_vevor_claimed_job_when_no_sync_pending(self):
        mp, _ = Marketplace.objects.get_or_create(code='kogan_vevor4', defaults={'name': 'Kogan Vevor'})
        user = User.objects.create_user(username='vevor_u4', email='vevor_u4@example.com', password='pass12345')
        store = Store.objects.create(
            user=user, name='Vevor Heal', region='AU', api_token='tok-v4', marketplace=mp,
        )
        vendor, _ = Vendor.objects.get_or_create(code='vevorau', defaults={'name': 'VevorAU'})
        product = Product.objects.create(vendor=vendor, vendor_sku='VEV-SKU-4', owner=user)
        ProductMapping.objects.create(
            store=store, product=product, marketplace_id='MID-4', sync_status='scraped', is_active=True,
        )
        job = HebScrapeJob.objects.create(
            store=store,
            requested_by=user,
            vendor_code='vevor',
            status=HebScrapeJob.Status.CLAIMED,
        )
        heal_stale_server_vendor_job(store, 'vevor', job)
        job.refresh_from_db()
        self.assertEqual(job.status, HebScrapeJob.Status.DONE)
        self.assertIsNotNone(job.completed_at)

        payload = build_scrape_progress_payload(store)
        vevor = payload['vendors']['vevor']
        self.assertEqual(vevor['job']['status'], 'done')
        self.assertEqual(vevor['sync_pending'], 0)


class CostcoServerScrapeRoutingTests(TestCase):
    """Costco AU joins the live server-scrape path when proxies are configured.

    These tests cover the routing-level decisions only (ingest-only flag, store
    has scrapeable pending, vendor payload runner kind). The actual HTTP fetch
    is exercised in ``scrapers.test_costco_au_scraper``.
    """

    @override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
    def test_costco_pending_is_scrapeable_when_proxies_set(self):
        from unittest.mock import patch as _patch
        from catalog.tasks import store_has_scrapeable_pending_mappings

        mp, _ = Marketplace.objects.get_or_create(code='kogan_costco1', defaults={'name': 'Kogan Costco'})
        user = User.objects.create_user(username='costco_u1', email='costco_u1@example.com', password='pass12345')
        store = Store.objects.create(
            user=user, name='Costco Live', region='AU', api_token='tok-c1', marketplace=mp,
        )
        vendor, _ = Vendor.objects.get_or_create(code='costcoau', defaults={'name': 'CostcoAU'})
        product = Product.objects.create(vendor=vendor, vendor_sku='COS-1', owner=user)
        ProductMapping.objects.create(
            store=store, product=product, marketplace_id='MIDC-1', sync_status='pending', is_active=True,
        )

        # No proxies → ingest-only behavior (current production default).
        with _patch.dict('os.environ', {}, clear=False):
            import os as _os
            for k in ('COSTCO_AU_PROXY_URLS', 'PROXY_URLS', 'COSTCO_AU_PROXY_URL',
                      'PROXY_URL', 'PROXY_ENDPOINTS'):
                _os.environ.pop(k, None)
            self.assertFalse(store_has_scrapeable_pending_mappings(store))

        # With proxies → Costco is scrapeable like Amazon AU.
        with _patch.dict('os.environ', {'COSTCO_AU_PROXY_URLS': 'http://u:p@a:1'}, clear=False):
            self.assertTrue(store_has_scrapeable_pending_mappings(store))

    @override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
    def test_vendor_payload_runner_flips_to_live_when_proxies_set(self):
        from unittest.mock import patch as _patch

        mp, _ = Marketplace.objects.get_or_create(code='kogan_costco2', defaults={'name': 'Kogan Costco'})
        user = User.objects.create_user(username='costco_u2', email='costco_u2@example.com', password='pass12345')
        store = Store.objects.create(
            user=user, name='Costco Payload', region='AU', api_token='tok-c2', marketplace=mp,
        )
        vendor, _ = Vendor.objects.get_or_create(code='costcoau', defaults={'name': 'CostcoAU'})
        product = Product.objects.create(vendor=vendor, vendor_sku='COS-2', owner=user)
        ProductMapping.objects.create(
            store=store, product=product, marketplace_id='MIDC-2', sync_status='pending', is_active=True,
        )

        with _patch.dict('os.environ', {'COSTCO_AU_PROXY_URLS': 'http://u:p@a:1'}, clear=False):
            payload = build_scrape_progress_payload(store)
        costco = payload['vendors'].get('costco') or {}
        self.assertEqual(costco.get('runner'), 'live')
        # Live vendors never carry a desktop job/queue payload.
        self.assertIsNone(costco.get('job'))
        self.assertIsNone(costco.get('queue'))
