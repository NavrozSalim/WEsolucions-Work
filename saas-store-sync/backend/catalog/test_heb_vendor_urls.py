"""Tests for HEB URL resolution and ingest URL collection."""
from __future__ import annotations

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from catalog.ingest_views import _collect_vendor_urls, _pick_pending_desktop_job
from catalog.models import HebScrapeJob, IngestToken, ProductMapping
from catalog.vendor_url_resolve import (
    heb_product_id_from_sku,
    heb_url_from_vendor_id,
    resolve_heb_product_url,
)
from marketplace.models import Marketplace
from products.models import Product
from stores.models import Store
from vendor.models import Vendor

User = get_user_model()


class HebUrlResolveTests(TestCase):
    def test_heb_product_id_from_composite_sku(self):
        self.assertEqual(heb_product_id_from_sku('AHJH-150275-0311-PK3'), '150275')

    def test_heb_url_from_vendor_id(self):
        self.assertEqual(
            heb_url_from_vendor_id('150275'),
            'https://www.heb.com/product-detail/150275',
        )

    def test_resolve_from_sku_when_vendor_url_empty(self):
        vendor = Vendor.objects.get(code='hebus')
        product = Product(vendor=vendor, vendor_sku='AHJH-150275-0311-PK3', vendor_url='')
        url = resolve_heb_product_url(product)
        self.assertEqual(url, 'https://www.heb.com/product-detail/150275')


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class HebIngestUrlCollectionTests(TestCase):
    def setUp(self):
        self.mp, _ = Marketplace.objects.get_or_create(
            code='kogan_heb_urls',
            defaults={'name': 'Kogan HEB URLs'},
        )
        self.vendor = Vendor.objects.get(code='hebus')
        self.user = User.objects.create_user(
            username='heb_url_u', email='heb_url_u@example.com', password='pass12345',
        )
        self.store = Store.objects.create(
            user=self.user, name='AHJ Test', region='USA', api_token='tok-heb-url', marketplace=self.mp,
        )
        self.product = Product.objects.create(
            vendor=self.vendor,
            owner=self.user,
            vendor_sku='AHJH-150275-0311-PK3',
            vendor_url='',
        )
        ProductMapping.objects.create(
            store=self.store,
            product=self.product,
            marketplace_id='AHJ-1',
            is_active=True,
        )

    def test_collect_urls_backfills_from_sku(self):
        urls = _collect_vendor_urls(str(self.store.id), vendor='heb', restrict_to_user_id=self.user.id)
        self.assertEqual(len(urls), 1)
        self.assertIn('150275', urls[0])
        self.product.refresh_from_db()
        self.assertIn('heb.com', self.product.vendor_url)

    def test_collect_urls_pending_only_skips_scraped(self):
        scraped = Product.objects.create(
            vendor=self.vendor,
            owner=self.user,
            vendor_sku='AHJH-999001-0311-PK3',
            vendor_url='https://www.heb.com/product-detail/999001',
        )
        ProductMapping.objects.create(
            store=self.store,
            product=scraped,
            marketplace_id='AHJ-scraped',
            is_active=True,
            sync_status='scraped',
        )
        all_urls = _collect_vendor_urls(
            str(self.store.id), vendor='heb', restrict_to_user_id=self.user.id,
        )
        pending_urls = _collect_vendor_urls(
            str(self.store.id),
            vendor='heb',
            restrict_to_user_id=self.user.id,
            pending_only=True,
        )
        self.assertEqual(len(all_urls), 2)
        self.assertEqual(len(pending_urls), 1)
        self.assertIn('150275', pending_urls[0])
        self.assertTrue(all('999001' not in u for u in pending_urls))


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class HebPickPendingJobTests(TestCase):
    def setUp(self):
        import hashlib

        self.mp, _ = Marketplace.objects.get_or_create(
            code='kogan_heb_pick',
            defaults={'name': 'Kogan HEB Pick'},
        )
        self.user_a = User.objects.create_user(
            username='pick_a', email='pick_a@example.com', password='pass12345',
        )
        self.user_b = User.objects.create_user(
            username='pick_b', email='pick_b@example.com', password='pass12345',
        )
        self.store_a = Store.objects.create(
            user=self.user_a, name='A', region='USA', api_token='pa', marketplace=self.mp,
        )
        raw = 'pick-token-a-' + ('x' * 24)
        self.token_a = IngestToken.objects.create(
            label='pick-a',
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            token_prefix=raw[:8],
            scopes=['heb'],
            created_by=self.user_a,
        )
        self.job_b = HebScrapeJob.objects.create(
            store=Store.objects.create(
                user=self.user_b, name='B', region='USA', api_token='pb', marketplace=self.mp,
            ),
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

    def test_pick_pending_scoped_to_token_owner(self):
        picked = _pick_pending_desktop_job('heb', self.token_a)
        self.assertEqual(picked.id, self.job_a.id)
