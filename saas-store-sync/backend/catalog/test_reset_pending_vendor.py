"""Catalog Reset status: all vendors, or one vendor's listings only."""
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from catalog.models import CatalogActivityLog, ProductMapping
from marketplace.models import Marketplace
from products.models import Product
from stores.models import Store
from vendor.models import Vendor

User = get_user_model()


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class ResetPendingVendorTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='reset_vendor_u', email='reset_vendor@example.com', password='pass12345',
        )
        mp, _ = Marketplace.objects.get_or_create(code='sears_reset_vendor', defaults={'name': 'Sears RV'})
        self.store = Store.objects.create(
            user=self.user, name='Reset Vendor Store', region='USA', api_token='tok-rv', marketplace=mp,
        )
        self.amazon, _ = Vendor.objects.get_or_create(code='amazonus', defaults={'name': 'AmazonUS'})
        self.ebay, _ = Vendor.objects.get_or_create(code='ebayus', defaults={'name': 'EbayUS'})
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = f'/api/v1/stores/{self.store.id}/catalog/reset-pending/'
        self.rows = {
            'amz_failed': self._mapping('A1', self.amazon, 'failed'),
            'amz_scraped': self._mapping('A2', self.amazon, 'scraped'),
            'eb_failed': self._mapping('E1', self.ebay, 'failed'),
            'eb_scraped': self._mapping('E2', self.ebay, 'scraped'),
        }

    def _mapping(self, sku, vendor, status):
        product = Product.objects.create(
            vendor=vendor, vendor_sku=sku, owner=self.user, vendor_url=f'https://example.com/{sku}',
        )
        return ProductMapping.objects.create(
            store=self.store, product=product, marketplace_id=sku, sync_status=status,
            is_active=True, scrape_error='old error', failed_sync_count=3,
        )

    def _status(self, key):
        return ProductMapping.objects.get(pk=self.rows[key].pk).sync_status

    def test_one_vendor_only_resets_that_vendor(self):
        res = self.client.post(self.url, {'confirm': True, 'scope': 'failed', 'vendor_id': str(self.ebay.id)},
                               format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data['listings_reset'], 1)
        self.assertEqual(res.data['vendor_name'], 'EbayUS')
        self.assertEqual(self._status('eb_failed'), 'pending')
        self.assertEqual(self._status('amz_failed'), 'failed')
        self.assertEqual(self._status('eb_scraped'), 'scraped')
        row = ProductMapping.objects.get(pk=self.rows['eb_failed'].pk)
        self.assertIsNone(row.scrape_error)
        self.assertEqual(row.failed_sync_count, 0)

    def test_reset_all_for_one_vendor(self):
        res = self.client.post(self.url, {'confirm': True, 'scope': 'all', 'vendor_id': str(self.amazon.id)},
                               format='json')
        self.assertEqual(res.data['listings_reset'], 2)
        self.assertEqual(self._status('amz_failed'), 'pending')
        self.assertEqual(self._status('amz_scraped'), 'pending')
        self.assertEqual(self._status('eb_failed'), 'failed')
        self.assertEqual(self._status('eb_scraped'), 'scraped')

    def test_all_vendors_when_vendor_is_empty_or_all(self):
        for vendor_value in ('', 'all'):
            for key in ('amz_failed', 'eb_failed'):
                ProductMapping.objects.filter(pk=self.rows[key].pk).update(sync_status='failed')
            res = self.client.post(self.url, {'confirm': True, 'scope': 'failed', 'vendor_id': vendor_value},
                                   format='json')
            self.assertEqual(res.data['listings_reset'], 2, vendor_value)
            self.assertIsNone(res.data['vendor_id'])

    def test_without_vendor_field_behaves_as_before(self):
        res = self.client.post(self.url, {'confirm': True, 'scope': 'scraped'}, format='json')
        self.assertEqual(res.data['listings_reset'], 2)

    def test_unknown_vendor_is_refused_and_nothing_changes(self):
        for bad in ('999999999', 'not-a-vendor-id'):
            res = self.client.post(self.url, {'confirm': True, 'scope': 'all', 'vendor_id': bad}, format='json')
            self.assertEqual(res.status_code, 400, bad)
        self.assertEqual(self._status('amz_failed'), 'failed')
        self.assertEqual(self._status('eb_scraped'), 'scraped')

    def test_log_names_the_vendor(self):
        self.client.post(self.url, {'confirm': True, 'scope': 'failed', 'vendor_id': str(self.ebay.id)},
                         format='json')
        log = CatalogActivityLog.objects.filter(store=self.store).order_by('-created_at').first()
        self.assertIn('from EbayUS', log.message)
