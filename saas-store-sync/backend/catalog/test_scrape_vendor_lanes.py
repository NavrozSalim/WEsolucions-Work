"""All-vendors store scrapes give each vendor lanes of its own, so a slow vendor cannot hold up a fast one."""
import uuid
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from catalog.celery_scrape_state import set_celery_scrape_state
from catalog.models import ProductMapping, StoreCatalogCeleryScrapeState
from catalog.tasks import _plan_store_scrape_lanes, run_store_wide_catalog_scrape
from marketplace.models import Marketplace
from products.models import Product
from stores.models import Store
from vendor.models import Vendor

User = get_user_model()


def _pairs(**per_vendor):
    """{'amz': 5} -> [('amz-0', 'amz'), ...] in scrape order, vendors interleaved like random ids."""
    queues = {v: [(f'{v}-{i}', v) for i in range(n)] for v, n in per_vendor.items()}
    out = []
    while any(queues.values()):
        for q in queues.values():
            if q:
                out.append(q.pop(0))
    return out


def _vendors_of(lane):
    return {mapping_id.split('-')[0] for chunk in lane for mapping_id in chunk}


class PlanStoreScrapeLanesTests(SimpleTestCase):
    def test_each_vendor_gets_its_own_lanes(self):
        lanes = _plan_store_scrape_lanes(_pairs(amz=106, ebay=61), chunk_size=10, lane_total=5)
        self.assertEqual(len(lanes), 5)
        for lane in lanes:
            self.assertEqual(len(_vendors_of(lane)), 1)
        by_vendor = [next(iter(_vendors_of(lane))) for lane in lanes]
        self.assertEqual(by_vendor.count('amz'), 3)
        self.assertEqual(by_vendor.count('ebay'), 2)

    def test_every_listing_is_scraped_exactly_once(self):
        pairs = _pairs(amz=37, ebay=12, costco=5)
        lanes = _plan_store_scrape_lanes(pairs, chunk_size=4, lane_total=6)
        seen = [mapping_id for lane in lanes for chunk in lane for mapping_id in chunk]
        self.assertEqual(sorted(seen), sorted(mapping_id for mapping_id, _ in pairs))
        for lane in lanes:
            for chunk in lane:
                self.assertLessEqual(len(chunk), 4)

    def test_small_vendor_still_gets_a_lane(self):
        lanes = _plan_store_scrape_lanes(_pairs(amz=500, ebay=3), chunk_size=10, lane_total=4)
        by_vendor = [next(iter(_vendors_of(lane))) for lane in lanes]
        self.assertEqual(by_vendor.count('ebay'), 1)
        self.assertEqual(by_vendor.count('amz'), 3)

    def test_no_more_lanes_than_a_vendor_has_batches(self):
        lanes = _plan_store_scrape_lanes(_pairs(amz=10, ebay=10), chunk_size=10, lane_total=6)
        self.assertEqual(len(lanes), 2)

    def test_fewer_lanes_than_vendors_alternates_vendors(self):
        lanes = _plan_store_scrape_lanes(_pairs(amz=30, ebay=30, costco=30), chunk_size=10, lane_total=1)
        self.assertEqual(len(lanes), 1)
        first_three = [chunk[0].split('-')[0] for chunk in lanes[0][:3]]
        self.assertEqual(sorted(first_three), ['amz', 'costco', 'ebay'])

    def test_single_vendor_is_unchanged_round_robin(self):
        lanes = _plan_store_scrape_lanes(_pairs(amz=50), chunk_size=10, lane_total=3)
        self.assertEqual([len(lane) for lane in lanes], [2, 2, 1])
        self.assertEqual(lanes[0][0][0], 'amz-0')
        self.assertEqual(lanes[1][0][0], 'amz-10')


@override_settings(
    DEBUG=True,
    ENCRYPTION_KEY=Fernet.generate_key().decode(),
    CATALOG_SCRAPE_CHUNK_SIZE=2,
    CATALOG_SCRAPE_LANES=3,
)
class StoreScrapeStartTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='vendor_lanes_u', email='vendor_lanes@example.com', password='pass12345',
        )
        mp, _ = Marketplace.objects.get_or_create(code='sears_vendor_lanes', defaults={'name': 'Sears VL'})
        self.store = Store.objects.create(
            user=self.user, name='Vendor Lanes Store', region='USA', api_token='tok-vl', marketplace=mp,
        )
        self.amazon, _ = Vendor.objects.get_or_create(code='amazonus', defaults={'name': 'AmazonUS'})
        self.ebay, _ = Vendor.objects.get_or_create(code='ebayus', defaults={'name': 'EbayUS'})
        for i in range(8):
            self._mapping(f'AMZ{i}', self.amazon, 'https://www.amazon.com/dp/B0{:08d}'.format(i))
        for i in range(4):
            self._mapping(f'EB{i}', self.ebay, f'https://www.ebay.com/itm/10000{i}')
        set_celery_scrape_state(
            self.store, task_id='root-vl', scope=StoreCatalogCeleryScrapeState.Scope.STORE,
        )

    def _mapping(self, sku, vendor, url):
        product = Product.objects.create(vendor=vendor, vendor_sku=sku, owner=self.user, vendor_url=url)
        ProductMapping.objects.create(
            store=self.store, product=product, marketplace_id=sku, sync_status='pending', is_active=True,
        )

    @patch('catalog.tasks.catalog_scrape_store_chunk_task.apply_async')
    def test_all_vendor_scrape_starts_single_vendor_lanes(self, mock_apply):
        out = run_store_wide_catalog_scrape(str(self.store.id), parallel=True)

        self.assertEqual(out['status'], 'running')
        self.assertEqual(out['chunks'], 6)
        self.assertEqual(mock_apply.call_count, 3)
        vendor_of = dict(
            ProductMapping.objects.filter(store=self.store).values_list('id', 'product__vendor__code'),
        )
        lane_vendors = []
        for call in mock_apply.call_args_list:
            _store_id, first, rest, lane_count, _vendor = call.kwargs['args']
            self.assertEqual(lane_count, 3)
            ids = list(first) + [i for chunk in rest for i in chunk]
            vendors = {vendor_of[uuid.UUID(i)] for i in ids}
            self.assertEqual(len(vendors), 1)
            lane_vendors.append(vendors.pop())
        self.assertEqual(sorted(lane_vendors), ['amazonus', 'amazonus', 'ebayus'])
