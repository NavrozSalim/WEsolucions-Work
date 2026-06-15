"""Multi-tenant isolation for catalog sync and desktop ingest helpers."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from catalog.ingest_views import _apply_to_mappings
from catalog.models import CatalogUpload, CatalogUploadRow, HebScrapeJob, IngestToken, ProductMapping
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


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class HebNextJobTenantTests(TestCase):
    """Desktop poller only claims jobs for stores owned by the token user."""

    def setUp(self):
        import hashlib

        self.mp, _ = Marketplace.objects.get_or_create(
            code='kogan_heb_job',
            defaults={'name': 'Kogan HEB Job'},
        )
        self.vendor = Vendor.objects.get(code='hebus')
        self.user_a = User.objects.create_user(
            username='heb_ja', email='heb_ja@example.com', password='pass12345',
        )
        self.user_b = User.objects.create_user(
            username='heb_jb', email='heb_jb@example.com', password='pass12345',
        )
        self.store_a = Store.objects.create(
            user=self.user_a, name='HEB A', region='USA', api_token='ha', marketplace=self.mp,
        )
        self.store_b = Store.objects.create(
            user=self.user_b, name='HEB B', region='USA', api_token='hb', marketplace=self.mp,
        )
        product_a = Product.objects.create(
            vendor=self.vendor,
            owner=self.user_a,
            vendor_sku='HEB-JOB-1',
            vendor_url='https://www.heb.com/product-detail/111',
        )
        product_b = Product.objects.create(
            vendor=self.vendor,
            owner=self.user_b,
            vendor_sku='HEB-JOB-2',
            vendor_url='https://www.heb.com/product-detail/222',
        )
        ProductMapping.objects.create(
            store=self.store_a, product=product_a, marketplace_id='HA-1', is_active=True,
        )
        ProductMapping.objects.create(
            store=self.store_b, product=product_b, marketplace_id='HB-1', is_active=True,
        )
        raw_a = 'test-token-user-a-' + ('x' * 24)
        raw_b = 'test-token-user-b-' + ('y' * 24)
        self.token_a = IngestToken.objects.create(
            label='heb-a',
            token_hash=hashlib.sha256(raw_a.encode()).hexdigest(),
            token_prefix=raw_a[:8],
            scopes=['heb'],
            created_by=self.user_a,
        )
        self.token_b = IngestToken.objects.create(
            label='heb-b',
            token_hash=hashlib.sha256(raw_b.encode()).hexdigest(),
            token_prefix=raw_b[:8],
            scopes=['heb'],
            created_by=self.user_b,
        )
        self.raw_a = raw_a
        self.raw_b = raw_b
        # User B's job is older — without tenant filter, poller A would steal it with 0 URLs.
        self.job_b = HebScrapeJob.objects.create(
            store=self.store_b,
            requested_by=self.user_b,
            vendor_code='heb',
            status=HebScrapeJob.Status.PENDING,
        )
        self.job_a = HebScrapeJob.objects.create(
            store=self.store_a,
            requested_by=self.user_a,
            vendor_code='heb',
            status=HebScrapeJob.Status.PENDING,
        )

    def test_next_job_scoped_to_token_owner(self):
        from django.db import transaction
        from rest_framework.test import APIClient

        client = APIClient()
        with transaction.atomic():
            resp = client.get(
                '/api/v1/ingest/heb/next-job/',
                HTTP_AUTHORIZATION=f'Bearer {self.raw_a}',
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['job_id'], str(self.job_a.id))
        self.assertEqual(data['url_count'], 1)
        self.assertIn('heb.com', data['urls'][0])

        self.job_b.refresh_from_db()
        self.assertEqual(self.job_b.status, HebScrapeJob.Status.PENDING)


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class SearsTemplateIngestTests(TestCase):
    """Sears catalog upload: Child SKU is required; internal product key derives from it."""

    def setUp(self):
        self.mp, _ = Marketplace.objects.get_or_create(
            code='sears',
            defaults={'name': 'Sears'},
        )
        self.vendor = Vendor.objects.get(code='amazonus')
        self.user = User.objects.create_user(
            username='sears_u', email='sears_u@example.com', password='pass12345',
        )
        self.store = Store.objects.create(
            user=self.user,
            name='Sears Store',
            region='USA',
            api_token='tok-sears',
            marketplace=self.mp,
        )

    def _sears_row(self, *, child_sku: str, vendor_sku: str = '') -> list:
        return [
            'AmazonUS',
            'B0TEST123',
            'No',
            '',
            'Sears',
            self.store.name,
            'PARENT-1',
            child_sku,
            '',
            'https://www.amazon.com/dp/B0TEST123',
            'Add',
        ]

    @patch('catalog.tasks._chunked_reset_store_active_listings_pending_scrape', side_effect=_noop_reset)
    def test_sync_uses_marketplace_child_sku_as_product_key(self, _mock_reset):
        up = CatalogUpload.objects.create(
            user=self.user,
            store=self.store,
            original_filename='sears.csv',
            status=CatalogUpload.Status.VALIDATED,
            total_rows=1,
        )
        from catalog.services import _make_row_ingest_context, build_catalog_row_instance

        header = [
            'Vendor Name', 'Vendor ID', 'Is Variation', 'Variation ID',
            'Marketplace Name', 'Store Name', 'Marketplace Parent SKU',
            'Marketplace Child SKU', 'Marketplace ID', 'Vendor URL', 'Action',
        ]
        ctx = _make_row_ingest_context(self.store, header)
        inst, err = build_catalog_row_instance(up, 2, self._sears_row(child_sku='CHILD-SEARS-1'), ctx)
        self.assertIsNone(err, err)
        self.assertEqual(inst.marketplace_child_sku_raw, 'CHILD-SEARS-1')
        inst.save()

        result = run_catalog_sync(str(up.id))
        self.assertEqual(result.get('errors', 0), 0, result)
        self.assertEqual(result.get('added'), 1, result)

        pm = ProductMapping.objects.get(store=self.store)
        self.assertEqual(pm.marketplace_child_sku, 'CHILD-SEARS-1')
        self.assertEqual(pm.product.vendor_sku, 'CHILD-SEARS-1')

    def test_add_rejected_without_marketplace_child_sku(self):
        up = CatalogUpload.objects.create(
            user=self.user,
            store=self.store,
            original_filename='sears.csv',
            status=CatalogUpload.Status.VALIDATED,
            total_rows=1,
        )
        from catalog.services import _make_row_ingest_context, build_catalog_row_instance

        header = [
            'Vendor Name', 'Vendor ID', 'Is Variation', 'Variation ID',
            'Marketplace Name', 'Store Name', 'Marketplace Parent SKU',
            'Marketplace Child SKU', 'Marketplace ID', 'Vendor URL', 'Action',
        ]
        ctx = _make_row_ingest_context(self.store, header)
        row = self._sears_row(child_sku='')
        row[7] = ''
        inst, err = build_catalog_row_instance(up, 2, row, ctx)
        self.assertIsNone(inst)
        self.assertIn('Marketplace Child SKU', err or '')

    def test_ingest_minimal_sears_csv_without_vendor_sku(self):
        from catalog.services import ingest_stored_catalog_file
        from django.core.files.base import ContentFile

        csv_content = (
            'Vendor Name,Vendor ID,Store Name,Marketplace Parent SKU,'
            'Marketplace Child SKU,Vendor URL,Action\n'
            f'AmazonUS,B0TEST123,{self.store.name},PARENT-1,CHILD-MIN-1,'
            'https://www.amazon.com/dp/B0TEST123,Add\n'
        )
        upload = CatalogUpload.objects.create(
            user=self.user,
            store=self.store,
            original_filename='sears_minimal.csv',
            status=CatalogUpload.Status.INGESTING,
        )
        upload.source_file.save(
            'sears_minimal.csv',
            ContentFile(csv_content.encode('utf-8')),
            save=True,
        )
        result = ingest_stored_catalog_file(str(upload.id))
        upload.refresh_from_db()
        self.assertEqual(result.get('total_rows'), 1, result)
        self.assertEqual(upload.status, CatalogUpload.Status.VALIDATED)
        self.assertEqual(upload.total_rows, 1)
        row = upload.rows.get()
        self.assertEqual(row.marketplace_child_sku_raw, 'CHILD-MIN-1')
        self.assertEqual(row.vendor_sku_raw, '')

    def test_ingest_legacy_sears_csv_without_vendor_sku_column(self):
        from catalog.services import ingest_stored_catalog_file
        from django.core.files.base import ContentFile

        csv_content = (
            'Vendor Name,Vendor ID,Is Variation,Variation ID,Marketplace Name,Store Name,'
            'Marketplace Parent SKU,Marketplace Child SKU,Marketplace ID,Vendor URL,Action\n'
            f'AmazonUS,B0TEST123,No,,Sears,{self.store.name},PARENT-1,CHILD-LEG-1,,'
            'https://www.amazon.com/dp/B0TEST123,Add\n'
        )
        upload = CatalogUpload.objects.create(
            user=self.user,
            store=self.store,
            original_filename='sears_legacy.csv',
            status=CatalogUpload.Status.INGESTING,
        )
        upload.source_file.save(
            'sears_legacy.csv',
            ContentFile(csv_content.encode('utf-8')),
            save=True,
        )
        result = ingest_stored_catalog_file(str(upload.id))
        upload.refresh_from_db()
        self.assertEqual(result.get('total_rows'), 1, result)
        self.assertEqual(upload.status, CatalogUpload.Status.VALIDATED)
        self.assertEqual(upload.rows.get().marketplace_child_sku_raw, 'CHILD-LEG-1')


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class CatalogResetPendingScopeTests(TestCase):
    def setUp(self):
        from rest_framework.test import APIClient

        self.client = APIClient()
        self.mp, _ = Marketplace.objects.get_or_create(
            code='reset_scope_mt',
            defaults={'name': 'Reset Scope MT'},
        )
        self.vendor = Vendor.objects.get(code='amazonus')
        self.user = User.objects.create_user(
            username='reset_scope_u',
            email='reset_scope_u@example.com',
            password='pass12345',
        )
        self.store = Store.objects.create(
            user=self.user,
            name='Reset Scope Store',
            region='USA',
            api_token='tok-reset',
            marketplace=self.mp,
        )
        self.client.force_authenticate(user=self.user)
        self.url = f'/api/v1/stores/{self.store.id}/catalog/reset-pending/'

    def _pm(self, sku: str, status: str):
        product = Product.objects.create(
            vendor=self.vendor,
            owner=self.user,
            vendor_sku=sku,
            variation_id='',
            vendor_url=f'https://www.amazon.com/dp/{sku}',
        )
        return ProductMapping.objects.create(
            store=self.store,
            product=product,
            marketplace_child_sku=sku,
            is_active=True,
            sync_status=status,
            failed_sync_count=2 if status in ('failed', 'needs_attention') else 0,
            scrape_error='old error' if status in ('failed', 'needs_attention') else None,
        )

    def test_reset_failed_scope_only(self):
        failed = self._pm('FAIL-1', 'failed')
        synced = self._pm('SYNC-1', 'synced')
        attention = self._pm('ATTN-1', 'needs_attention')

        resp = self.client.post(self.url, {'confirm': True, 'scope': 'failed'}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['listings_reset'], 1)

        failed.refresh_from_db()
        synced.refresh_from_db()
        attention.refresh_from_db()
        self.assertEqual(failed.sync_status, 'pending')
        self.assertIsNone(failed.scrape_error)
        self.assertEqual(synced.sync_status, 'synced')
        self.assertEqual(attention.sync_status, 'needs_attention')

    def test_reset_needs_attention_scope_only(self):
        failed = self._pm('FAIL-2', 'failed')
        attention = self._pm('ATTN-2', 'needs_attention')

        resp = self.client.post(self.url, {'confirm': True, 'scope': 'needs_attention'}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['listings_reset'], 1)

        failed.refresh_from_db()
        attention.refresh_from_db()
        self.assertEqual(failed.sync_status, 'failed')
        self.assertEqual(attention.sync_status, 'pending')
        self.assertIsNone(attention.scrape_error)


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class CatalogFailedZeroViewTests(TestCase):
    def setUp(self):
        from rest_framework.test import APIClient

        self.client = APIClient()
        self.mp, _ = Marketplace.objects.get_or_create(
            code='failed_zero_mt',
            defaults={'name': 'Failed Zero MT'},
        )
        self.user = User.objects.create_user(
            username='failed_zero_u',
            email='failed_zero_u@example.com',
            password='pass12345',
        )
        self.store = Store.objects.create(
            user=self.user,
            name='Failed Zero Store',
            region='USA',
            api_token='tok-fz',
            marketplace=self.mp,
            connection_status='connected',
        )
        self.client.force_authenticate(user=self.user)
        self.url = f'/api/v1/stores/{self.store.id}/catalog/failed-zero/'

    def test_requires_confirm(self):
        resp = self.client.post(self.url, {}, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('confirm', resp.json()['error'])

    @patch('sync.tasks.run_store_failed_zero_inventory.delay')
    def test_queues_when_confirm_true(self, mock_delay):
        mock_delay.return_value = MagicMock(id='job-fz-1')
        resp = self.client.post(self.url, {'confirm': True}, format='json')
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.json()['job_id'], 'job-fz-1')
        mock_delay.assert_called_once_with(str(self.store.id))


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class CatalogPushListingsViewTests(TestCase):
    def setUp(self):
        from rest_framework.test import APIClient

        self.client = APIClient()
        self.mp, _ = Marketplace.objects.get_or_create(
            code='push_listings_mt',
            defaults={'name': 'Push Listings MT'},
        )
        self.user = User.objects.create_user(
            username='push_listings_u',
            email='push_listings_u@example.com',
            password='pass12345',
        )
        self.store = Store.objects.create(
            user=self.user,
            name='Push Listings Store',
            region='USA',
            api_token='tok-push',
            marketplace=self.mp,
            connection_status='connected',
        )
        self.client.force_authenticate(user=self.user)
        self.url = f'/api/v1/stores/{self.store.id}/catalog/push-listings/'

    @patch('sync.tasks.run_store_push_listings_only.delay', side_effect=ConnectionError('redis down'))
    def test_push_listings_returns_503_when_worker_unavailable(self, _delay):
        resp = self.client.post(self.url, {}, format='json')
        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertIn('error', body)
        self.assertIn('celery_worker_sync', body['error'])

    @patch('sync.tasks.run_store_push_listings_only.delay')
    def test_push_listings_queues_job_and_returns_total(self, delay_mock):
        delay_mock.return_value.id = 'task-push-abc'
        resp = self.client.post(self.url, {}, format='json')
        self.assertEqual(resp.status_code, 202)
        body = resp.json()
        self.assertEqual(body['job_id'], 'task-push-abc')
        self.assertIn('total_listings', body)
        delay_mock.assert_called_once()

    def test_push_listings_returns_409_when_lock_held(self):
        from sync.push_listings_lock import try_acquire_push_listings_lock

        try_acquire_push_listings_lock(str(self.store.id), 'existing-task')
        resp = self.client.post(self.url, {}, format='json')
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json().get('error'), 'push_listings_already_running')

    def test_execute_push_listings_empty_catalog_does_not_raise(self):
        """Regression: _execute_store_push_listings_only uses time.monotonic() at module scope."""
        from sync.tasks import _execute_store_push_listings_only

        result = _execute_store_push_listings_only(str(self.store.id), disable_schedule=False)
        self.assertEqual(result.get('pushed'), 0)
        self.assertEqual(result.get('failed'), 0)
        self.assertEqual(result.get('skipped_no_listing'), 0)


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class CatalogPushListingsProgressViewTests(TestCase):
    def setUp(self):
        from rest_framework.test import APIClient

        self.client = APIClient()
        self.mp, _ = Marketplace.objects.get_or_create(
            code='push_progress_mt',
            defaults={'name': 'Push Progress MT'},
        )
        self.user = User.objects.create_user(
            username='push_progress_u',
            email='push_progress_u@example.com',
            password='pass12345',
        )
        self.store = Store.objects.create(
            user=self.user,
            name='Push Progress Store',
            region='USA',
            api_token='tok-pp',
            marketplace=self.mp,
            connection_status='connected',
        )
        self.client.force_authenticate(user=self.user)
        self.url = f'/api/v1/stores/{self.store.id}/catalog/push-listings/progress/'

    def test_progress_inactive_when_no_lock(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body['active'])
        self.assertIsNone(body['phase'])
        self.assertEqual(body['store_id'], str(self.store.id))

    def test_progress_active_with_lock_and_sync_logs(self):
        from catalog.activity_log import append_catalog_log
        from sync.push_listings_lock import try_acquire_push_listings_lock

        try_acquire_push_listings_lock(str(self.store.id), 'task-progress-xyz')
        append_catalog_log(
            self.store.id,
            'Marketplace sync started — pushing local prices and stock to your marketplace.',
            action_type='sync_start',
        )
        append_catalog_log(
            self.store.id,
            'Marketplace sync in progress: 50 of 200 processed (45 pushed, 2 failed, 3 skipped).',
            action_type='sync_progress',
            metadata={
                'processed': 50,
                'total': 200,
                'pushed': 45,
                'failed': 2,
                'skipped_no_listing': 3,
            },
        )

        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body['active'])
        self.assertEqual(body['processed'], 50)
        self.assertEqual(body['total'], 200)
        self.assertEqual(body['pushed'], 45)
        self.assertEqual(body['failed'], 2)
        self.assertEqual(body['skipped_no_listing'], 3)
        self.assertEqual(body['pct'], 25)

    def test_progress_queue_build_when_sync_started_without_progress_log(self):
        from catalog.activity_log import append_catalog_log
        from sync.push_listings_lock import try_acquire_push_listings_lock

        try_acquire_push_listings_lock(str(self.store.id), 'task-progress-queued')
        append_catalog_log(
            self.store.id,
            'Marketplace sync started — pushing local prices and stock to your marketplace.',
            action_type='sync_start',
        )

        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body['active'])
        self.assertEqual(body['processed'], 0)
        self.assertEqual(body['pct'], 0)
        self.assertEqual(body['sync_step'], 'queue_build')
