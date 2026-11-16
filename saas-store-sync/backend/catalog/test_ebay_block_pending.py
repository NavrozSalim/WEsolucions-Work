"""A listing eBay's bot wall refused stays Pending and untouched (US and AU)."""
import time
from decimal import Decimal
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from catalog.celery_scrape_state import set_celery_scrape_state
from catalog.models import ProductMapping, StoreCatalogCeleryScrapeState
from catalog.scrape_failure import BLOCK_RETRY_LIMIT, FALLBACK_LISTING_PRICE
from catalog.tasks import _process_store_wide_scrape_mappings
from marketplace.models import Marketplace
from products.models import Product
from stores.models import Store
from vendor.models import Vendor

User = get_user_model()

_PATCHES = (
    patch('stores.wallkoala.product_uses_wallkoala', return_value=False),
    patch('stores.nora.product_uses_nora_inventory', return_value=False),
    patch('stores.nora.load_store_nora_stock_map', return_value=None),
)


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class EbayBlockLeavesListingPendingTests(TestCase):
    def setUp(self):
        for p in _PATCHES:
            p.start()
            self.addCleanup(p.stop)
        self.user = User.objects.create_user(
            username='ebay_block_u', email='ebay_block@example.com', password='pass12345',
        )
        mp, _ = Marketplace.objects.get_or_create(code='kogan_ebay_block', defaults={'name': 'Kogan EB'})
        self.store = Store.objects.create(
            user=self.user, name='eBay Block Store', region='USA', api_token='tok-eb', marketplace=mp,
        )
        self.ebay, _ = Vendor.objects.get_or_create(code='ebayus', defaults={'name': 'eBay US'})
        self.amazon, _ = Vendor.objects.get_or_create(code='amazonus', defaults={'name': 'Amazon US'})
        set_celery_scrape_state(self.store, task_id='root-eb', scope=StoreCatalogCeleryScrapeState.Scope.STORE)

    def _mapping(self, sku, vendor, url):
        product = Product.objects.create(vendor=vendor, vendor_sku=sku, owner=self.user, vendor_url=url)
        return ProductMapping.objects.create(
            store=self.store, product=product, marketplace_id=sku, sync_status='pending', is_active=True,
            store_price=Decimal('25.00'), store_stock=4, title='Real product title',
        )

    def _run(self, **kw):
        qs = ProductMapping.objects.filter(store=self.store).select_related('product', 'product__vendor')
        return _process_store_wide_scrape_mappings(
            qs.order_by('id'), store=self.store, store_id=str(self.store.id), session={},
            emit_stall_log=False, **kw,
        )

    @patch('scrapers.get_price_and_stock')
    def test_blocked_ebay_listing_stays_pending_untouched(self, mock_price):
        pm = self._mapping('E1', self.ebay, 'https://www.ebay.com/itm/111')
        mock_price.return_value = {
            'price': None, 'inventory': None, 'title': 'Error Page', 'error_code': 'challenge',
        }
        stats = self._run()
        pm.refresh_from_db()
        self.assertEqual(pm.sync_status, 'pending')
        self.assertEqual(pm.store_price, Decimal('25.00'))
        self.assertEqual(pm.store_stock, 4)
        self.assertEqual(pm.title, 'Real product title')
        self.assertEqual(pm.failed_sync_count, 1)
        self.assertIn('will retry (1/3)', pm.scrape_error)
        self.assertEqual(stats['blocked'], 1)
        self.assertEqual(stats['failed'], 0)

    @patch('scrapers.get_price_and_stock')
    def test_ebay_listing_fails_after_retry_limit(self, mock_price):
        pm = self._mapping('E2', self.ebay, 'https://www.ebay.com/itm/222')
        ProductMapping.objects.filter(pk=pm.pk).update(failed_sync_count=BLOCK_RETRY_LIMIT - 1)
        mock_price.return_value = {'price': None, 'inventory': None, 'title': 'Error Page', 'error_code': 'blocked'}
        stats = self._run()
        pm.refresh_from_db()
        self.assertNotEqual(pm.sync_status, 'pending')
        self.assertEqual(pm.store_price, FALLBACK_LISTING_PRICE)
        self.assertEqual(pm.store_stock, 0)
        # The block page's title is never saved.
        self.assertEqual(pm.title, 'Real product title')
        self.assertEqual(stats['failed'], 1)

    @patch('scrapers.get_price_and_stock')
    def test_gone_ebay_listing_still_fails_right_away(self, mock_price):
        pm = self._mapping('E3', self.ebay, 'https://www.ebay.com/itm/333')
        mock_price.return_value = {'price': None, 'inventory': None, 'title': None, 'error_code': 'not_found'}
        self._run()
        pm.refresh_from_db()
        self.assertNotEqual(pm.sync_status, 'pending')
        self.assertEqual(pm.store_stock, 0)

    @patch('scrapers.get_price_and_stock')
    def test_amazon_block_is_unchanged(self, mock_price):
        pm = self._mapping('A1', self.amazon, 'https://www.amazon.com/dp/B000000001')
        mock_price.return_value = {'price': None, 'inventory': None, 'title': None, 'error_code': 'blocked_captcha'}
        self._run()
        pm.refresh_from_db()
        self.assertNotEqual(pm.sync_status, 'pending')
        self.assertEqual(pm.store_price, FALLBACK_LISTING_PRICE)

    @patch('scrapers.get_price_and_stock')
    def test_success_clears_block_count(self, mock_price):
        pm = self._mapping('E4', self.ebay, 'https://www.ebay.com/itm/444')
        ProductMapping.objects.filter(pk=pm.pk).update(failed_sync_count=1)
        mock_price.return_value = {'price': 10.0, 'inventory': 3, 'title': 'Tuner'}
        self._run()
        pm.refresh_from_db()
        self.assertEqual(pm.sync_status, 'scraped')
        self.assertEqual(pm.failed_sync_count, 0)

    @patch('scrapers.get_price_and_stock')
    def test_batch_stops_at_its_time_budget(self, mock_price):
        self._mapping('E5', self.ebay, 'https://www.ebay.com/itm/555')
        self._mapping('E6', self.ebay, 'https://www.ebay.com/itm/666')
        stats = self._run(deadline_mono=time.monotonic() - 1)
        mock_price.assert_not_called()
        self.assertTrue(stats['stalled'])
        self.assertEqual(stats['error_summary'], 'batch_time_limit')
        self.assertEqual(
            ProductMapping.objects.filter(store=self.store, sync_status='pending').count(), 2,
        )
