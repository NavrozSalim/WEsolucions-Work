"""A changed Costway / Vevor feed format fails the feed, so wrong prices and stock never reach the marketplace."""
import os
import tempfile
from decimal import Decimal
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings

from catalog.models import CatalogActivityLog, ProductMapping
from catalog.tasks import run_costway_au_ingest, run_vevor_au_ingest
from marketplace.models import Marketplace
from products.models import Product
from scrapers.feed_format import FeedFormatError, check_feed_quality, is_whole_number
from stores.models import Store
from vendor.models import Vendor

LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'feed-format-tests'}}
VEVOR_HEADER = ['SKU', 'Product link', 'Inventory quantity', 'after coupon price']
COSTWAY_HEADER = ['SKU', 'Item NO.', 'Title', 'Description', 'Price', 'Category', 'Link', 'QTY', 'Weight', 'Image']


def _xlsx(rows):
    from openpyxl import Workbook

    wb = Workbook()
    for row in rows:
        wb.active.append(row)
    tmp = tempfile.NamedTemporaryFile(prefix='vevor_fmt_', suffix='.xlsx', delete=False)
    tmp.close()
    wb.save(tmp.name)
    return tmp.name


def _csv(rows):
    tmp = tempfile.NamedTemporaryFile(prefix='costway_fmt_', suffix='.csv', delete=False, mode='w',
                                      encoding='utf-8', newline='')
    import csv

    csv.writer(tmp).writerows(rows)
    tmp.close()
    return tmp.name


def _vevor_rows(n, price='49.90', stock='7'):
    return [VEVOR_HEADER] + [[f'VEV{i:05d}', '', stock, price] for i in range(n)]


def _costway_rows(n, price='19.95', qty='4'):
    return [COSTWAY_HEADER] + [
        [f'TP{i:05d}', str(70000 + i), 'T', 'D', price, 'Cat', f'https://au.costway.com/p{i}.html', qty, '1.5', '']
        for i in range(n)
    ]


@override_settings(CACHES=LOCMEM)
class QualityCheckTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_good_feed_passes_and_is_remembered(self):
        check_feed_quality('vevor', 'Vevor AU', rows=1000, priced=990)
        self.assertEqual(cache.get('vendor_feed_last_good_rows:vevor'), 1000)

    def test_mostly_zero_prices_fail(self):
        with self.assertRaisesRegex(FeedFormatError, 'only 500 of 1000 rows have a price above 0'):
            check_feed_quality('vevor', 'Vevor AU', rows=1000, priced=500)

    def test_stock_that_is_not_whole_numbers_fails(self):
        with self.assertRaisesRegex(FeedFormatError, '300 of 1000 stock values are not whole numbers'):
            check_feed_quality('vevor', 'Vevor AU', rows=1000, priced=1000, bad_stock=300)

    def test_unreadable_rows_fail(self):
        with self.assertRaisesRegex(FeedFormatError, '200 of 1000 rows could not be read'):
            check_feed_quality('costway', 'Costway AU', rows=1000, priced=800, skipped=200)

    def test_no_rows_fail(self):
        with self.assertRaisesRegex(FeedFormatError, 'no product rows'):
            check_feed_quality('costway', 'Costway AU', rows=0, priced=0)

    def test_big_drop_in_rows_fails_until_the_feed_is_back(self):
        check_feed_quality('vevor', 'Vevor AU', rows=10000, priced=10000)
        with self.assertRaisesRegex(FeedFormatError, '2000 rows, less than half of the last good file \\(10000'):
            check_feed_quality('vevor', 'Vevor AU', rows=2000, priced=2000)
        self.assertEqual(cache.get('vendor_feed_last_good_rows:vevor'), 10000)
        check_feed_quality('vevor', 'Vevor AU', rows=9500, priced=9500)   # small changes are fine
        self.assertEqual(cache.get('vendor_feed_last_good_rows:vevor'), 9500)

    def test_whole_number(self):
        for ok in (None, '', 0, 12, 3.0, '117', '117.0', '1,200'):
            self.assertTrue(is_whole_number(ok), ok)
        for bad in ('in stock', '2.5', 2.5, -1, True, '11.20000kg'):
            self.assertFalse(is_whole_number(bad), bad)


@override_settings(CACHES=LOCMEM)
class LiveLoaderTests(SimpleTestCase):
    """load_*_feed_lookups (the path every update uses) runs the quality check."""

    def setUp(self):
        cache.clear()
        self.paths = []

    def tearDown(self):
        for p in self.paths:
            if os.path.exists(p):
                os.unlink(p)

    def _vevor(self, rows):
        from scrapers import vevor_au_ingest

        path = _xlsx(rows)
        self.paths.append(path)
        with patch.object(vevor_au_ingest, 'fetch_vevor_feed', return_value=path):
            return vevor_au_ingest.load_vevor_feed_lookups()

    def _costway(self, rows):
        from scrapers import costway_au_ingest

        path = _csv(rows)
        self.paths.append(path)
        with patch.object(costway_au_ingest, 'fetch_costway_feed', return_value=path):
            return costway_au_ingest.load_costway_feed_lookups()

    def test_vevor_normal_file_loads(self):
        out = self._vevor(_vevor_rows(50))
        self.assertEqual(out['lookup']['VEV00001'], {'Posted Price': 49.9, 'Posted Inventory': 7})

    def test_vevor_price_column_with_text_fails(self):
        with self.assertRaisesRegex(FeedFormatError, 'price above 0'):
            self._vevor(_vevor_rows(50, price='see website'))

    def test_vevor_stock_column_with_weights_fails(self):
        with self.assertRaisesRegex(FeedFormatError, 'not whole numbers'):
            self._vevor(_vevor_rows(50, stock='11.20000'))

    def test_costway_normal_file_loads(self):
        out = self._costway(_costway_rows(50))
        self.assertEqual(out['lookup']['70001-TP00001']['Posted Inventory'], 4)

    def test_costway_unreadable_rows_fail(self):
        with self.assertRaisesRegex(FeedFormatError, 'rows could not be read'):
            self._costway(_costway_rows(50, qty='in stock'))

    def test_costway_renamed_column_fails(self):
        rows = _costway_rows(50)
        rows[0] = ['Stock' if c == 'QTY' else c for c in rows[0]]
        with self.assertRaisesRegex(FeedFormatError, 'column "QTY" not found'):
            self._costway(rows)


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode(), CACHES=LOCMEM)
class FeedJobKeepsLastGoodValuesTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username='fmt', email='fmt@example.com', password='pass12345')
        mp, _ = Marketplace.objects.get_or_create(code='kogan_fmt', defaults={'name': 'Kogan'})
        self.store = Store.objects.create(user=user, name='Fmt', region='AU', api_token='tok-fmt',
                                          marketplace=mp, connection_status='connected')
        self.user = user

    def _pending(self, vendor_code, name, sku):
        vendor, _ = Vendor.objects.get_or_create(code=vendor_code, defaults={'name': name})
        product = Product.objects.create(vendor=vendor, vendor_sku=sku, owner=self.user)
        return ProductMapping.objects.create(
            store=self.store, product=product, marketplace_id=f'MID-{sku}', sync_status='pending',
            is_active=True, store_price=Decimal('80.00'), store_stock=5,
        )

    def _check(self, run, pm, vendor_word):
        out = run(store_id=str(self.store.id))
        self.assertEqual(out['status'], 'format_changed')
        pm.refresh_from_db()
        self.assertEqual((pm.sync_status, pm.store_price, pm.store_stock), ('pending', Decimal('80.00'), 5))
        log = CatalogActivityLog.objects.get(store=self.store, action_type='feed_format_error')
        self.assertIn(f'{vendor_word} AU feed format changed', log.message)
        self.assertIn('nothing was sent to the marketplace', log.message)

    def test_vevor_format_change_leaves_rows_pending_and_logs(self):
        pm = self._pending('vevorau', 'VevorAU', 'VEV-1')
        err = FeedFormatError('Vevor AU', 'column "after coupon price" not found (columns now: SKU).')
        with patch('scrapers.vevor_au_ingest.load_vevor_feed_lookups', side_effect=err):
            self._check(run_vevor_au_ingest, pm, 'Vevor')

    def test_costway_format_change_leaves_rows_pending_and_logs(self):
        pm = self._pending('costwayau', 'CostwayAU', 'TP1')
        err = FeedFormatError('Costway AU', 'column "QTY" not found (columns now: SKU).')
        with patch('scrapers.costway_au_ingest.load_costway_feed_lookups', side_effect=err):
            self._check(run_costway_au_ingest, pm, 'Costway')
