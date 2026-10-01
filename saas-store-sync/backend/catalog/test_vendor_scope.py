"""Vendor picker: store list, scrape scope, and pending-count filter."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from catalog.models import ProductMapping
from catalog.tasks import _count_scrapeable_pending_mappings
from catalog.views import CatalogScrapeTriggerView, _scrape_vendor_scope, ingest_vendor_key_for_code
from marketplace.models import Marketplace
from products.models import Product
from stores.models import Store, StoreVendorPriceSettings
from vendor.models import Vendor


class IngestVendorKeyTests(TestCase):
    def test_feed_and_browser_codes(self):
        self.assertEqual(ingest_vendor_key_for_code('costwayau'), 'costway')
        self.assertEqual(ingest_vendor_key_for_code('vevorau'), 'vevor')
        self.assertEqual(ingest_vendor_key_for_code('hebus'), 'heb')
        self.assertEqual(ingest_vendor_key_for_code('costcoau'), 'costco')
        self.assertIsNone(ingest_vendor_key_for_code('amazonau'))
        self.assertIsNone(ingest_vendor_key_for_code('ebayau'))
        self.assertIsNone(ingest_vendor_key_for_code(''))

    def test_scrape_scope_splits_feed_and_browser(self):
        costway = Vendor.objects.get(code='costwayau')
        amazon = Vendor.objects.get(code='amazonau')
        feed_keys, browser_id = _scrape_vendor_scope(CatalogScrapeTriggerView, costway)
        self.assertEqual(feed_keys, ['costway'])
        self.assertIsNone(browser_id)
        feed_keys, browser_id = _scrape_vendor_scope(CatalogScrapeTriggerView, amazon)
        self.assertEqual(feed_keys, [])
        self.assertEqual(browser_id, str(amazon.id))
        self.assertEqual(_scrape_vendor_scope(CatalogScrapeTriggerView, None), (None, None))


class VendorPendingCountTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='vscope', email='vscope@example.com', password='pw')
        mp, _ = Marketplace.objects.get_or_create(code='kogan_vs', defaults={'name': 'Kogan VS'})
        self.store = Store.objects.create(
            user=self.user, name='Scope Store', region='AU', api_token='tok', marketplace=mp,
        )
        self.amazon = Vendor.objects.get(code='amazonau')
        self.costway = Vendor.objects.get(code='costwayau')
        amazon_product = Product.objects.create(
            owner=self.user, vendor=self.amazon, vendor_sku='AMZ-1', vendor_url='https://www.amazon.com.au/dp/B0SCOPE1',
        )
        costway_product = Product.objects.create(
            owner=self.user, vendor=self.costway, vendor_sku='CW-1',
        )
        ProductMapping.objects.create(store=self.store, product=amazon_product, sync_status='pending')
        ProductMapping.objects.create(store=self.store, product=costway_product, sync_status='pending')

    def test_vendor_id_limits_live_scrape_count(self):
        self.assertEqual(_count_scrapeable_pending_mappings(self.store), 1)
        self.assertEqual(
            _count_scrapeable_pending_mappings(self.store, vendor_id=self.amazon.id),
            1,
        )
        self.assertEqual(
            _count_scrapeable_pending_mappings(self.store, vendor_id=self.costway.id),
            0,
        )


class CatalogStoreVendorsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='vlist', email='vlist@example.com', password='pw')
        mp, _ = Marketplace.objects.get_or_create(code='mydeal', defaults={'name': 'MyDeal'})
        self.store = Store.objects.create(
            user=self.user, name='Two Vendor Store', region='AU', api_token='tok', marketplace=mp,
        )
        self.amazon = Vendor.objects.get(code='amazonau')
        self.costway = Vendor.objects.get(code='costwayau')
        StoreVendorPriceSettings.objects.create(store=self.store, vendor=self.amazon)
        StoreVendorPriceSettings.objects.create(store=self.store, vendor=self.costway)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_store_list_includes_vendors(self):
        res = self.client.get('/api/v1/catalog/stores/')
        self.assertEqual(res.status_code, 200)
        row = next(item for item in res.data if item['name'] == 'Two Vendor Store')
        codes = sorted(v['code'] for v in row['vendors'])
        self.assertEqual(codes, ['amazonau', 'costwayau'])
        self.assertTrue(all(v['id'] and v['name'] for v in row['vendors']))
