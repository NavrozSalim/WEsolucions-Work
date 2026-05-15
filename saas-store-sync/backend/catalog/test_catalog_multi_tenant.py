"""Multi-tenant isolation for catalog sync and desktop ingest helpers."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from catalog.ingest_views import _apply_to_mappings
from catalog.models import CatalogUpload, CatalogUploadRow, ProductMapping
from catalog.tasks import run_catalog_sync, run_vevor_au_ingest
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
