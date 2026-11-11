"""Auto Purchasing Phase 3: catalog match, vendor price/stock, profit, buyable or blocked."""
from decimal import Decimal
from unittest import mock

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from catalog.models import ProductMapping
from listings.auto_purchase_check import check_order, run_purchase_checks
from listings.models import Environment, MarketplaceOrder, OrderPurchaseCheck, OrderStatus
from listings.test_order_file_import import BIGW_CSV, kogan_xlsx
from marketplace.models import Marketplace
from products.models import Product
from stores.models import Store, StoreVendorPriceSettings
from vendor.models import Vendor, VendorPrice

User = get_user_model()
LIVE = 'listings.auto_purchase_check._live_price_stock'


def live(price, stock, **extra):
    return {'price': Decimal(str(price)) if price is not None else None, 'inventory': stock, 'source': 'live', **extra}


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class PurchaseCheckTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='apc', email='apc@example.com', password='pass12345')
        self.kogan, _ = Marketplace.objects.get_or_create(code='kogan', defaults={'name': 'Kogan'})
        self.mydeal, _ = Marketplace.objects.get_or_create(code='mydeal', defaults={'name': 'MyDeal'})
        self.amazon, _ = Vendor.objects.get_or_create(code='amazonau', defaults={'name': 'AmazonAU'})
        self.ebay, _ = Vendor.objects.get_or_create(code='ebayau', defaults={'name': 'EbayAU'})
        self.costway, _ = Vendor.objects.get_or_create(code='costwayau', defaults={'name': 'CostwayAU'})
        self.store = Store.objects.create(
            user=self.user, name='Kogan-TFS', region='AU', api_token='t', marketplace=self.kogan,
            auto_buy_vendor='amazon_au',
        )
        StoreVendorPriceSettings.objects.create(
            store=self.store, vendor=self.amazon, marketplace_fees_percentage=Decimal('15'),
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _map(self, sku, vendor=None, vendor_sku='B0TEST0001', store=None):
        vendor = vendor or self.amazon
        product = Product.objects.create(
            vendor=vendor, vendor_sku=vendor_sku, owner=self.user,
            vendor_url=f'https://www.amazon.com.au/dp/{vendor_sku}',
        )
        return ProductMapping.objects.create(
            store=store or self.store, product=product, marketplace_child_sku=sku, is_active=True,
        )

    def _order(self, lines, store=None, key='ORD1'):
        return MarketplaceOrder.objects.create(
            store=store or self.store, user=self.user, external_order_key=key, invoice_number=key,
            environment=Environment.PRODUCTION, status=OrderStatus.PAID,
            line_items_json=lines, raw_response_json={'source': 'order_file'},
        )

    @staticmethod
    def line(sku, price_cents, qty=1, commission=None):
        return {'sku': sku, 'title': sku, 'quantity': qty, 'priceCents': price_cents, 'commissionCents': commission}

    # ------------------------------------------------------------------ decision

    @mock.patch(LIVE, return_value=live('20.99', 5))
    def test_prompt_example_is_buyable(self, _live):
        self._map('TFS-KAZ-1-New')
        check = check_order(self._order([self.line('TFS-KAZ-1-New', 3848)]))
        self.assertEqual(check.status, 'buyable')
        self.assertEqual(check.reasons, [])
        self.assertEqual(
            (check.total_sale_cents, check.fee_cents, check.after_fees_cents, check.cost_cents, check.earning_cents),
            (3848, 577, 3271, 2099, 1172),
        )
        ln = check.lines_json[0]
        self.assertEqual((ln['vendor_id'], ln['vendor_stock'], ln['fee_percent']), ('B0TEST0001', 5, '15.00'))
        self.assertIn('amazon.com.au/dp/B0TEST0001', ln['vendor_url'])

    @mock.patch(LIVE)
    def test_not_mapped_then_recheck_after_mapping(self, mock_live):
        order = self._order([self.line('TFS-NEW-SKU-New', 5000)])
        check = check_order(order)
        self.assertEqual(check.status, 'blocked')
        self.assertEqual(check.reasons, ['Not mapped: TFS-NEW-SKU-New'])
        self.assertEqual(check.lines_json[0]['result'], 'not_mapped')
        mock_live.assert_not_called()

        self._map('TFS-NEW-SKU-New')
        mock_live.return_value = live('10.00', 3)
        check = check_order(order)
        self.assertEqual(check.status, 'buyable')
        self.assertEqual(check.earning_cents, 5000 - 750 - 1000)

    @mock.patch(LIVE, return_value=live('20.00', 2))
    def test_stock_below_quantity_blocks(self, _live):
        self._map('SKU-A')
        check = check_order(self._order([self.line('SKU-A', 9178, qty=3)]))
        self.assertEqual(check.status, 'blocked')
        self.assertTrue(check.reasons[0].startswith('Out of stock: SKU-A'))

    @mock.patch(LIVE, return_value=live('40.00', 10))
    def test_loss_blocks(self, _live):
        self._map('SKU-A')
        check = check_order(self._order([self.line('SKU-A', 3848)]))
        self.assertEqual(check.status, 'blocked')
        self.assertEqual(check.earning_cents, 3271 - 4000)
        self.assertEqual(check.reasons, ['Loss: earning -7.29'])

    @mock.patch(LIVE, return_value=live('32.71', 10))
    def test_zero_earning_is_a_loss(self, _live):
        self._map('SKU-A')
        check = check_order(self._order([self.line('SKU-A', 3848)]))
        self.assertEqual(check.earning_cents, 0)
        self.assertEqual(check.status, 'blocked')

    @mock.patch(LIVE)
    def test_other_vendor_blocks_without_scraping(self, mock_live):
        self._map('SKU-E', vendor=self.ebay, vendor_sku='123456')
        check = check_order(self._order([self.line('SKU-E', 5000)]))
        self.assertEqual(check.status, 'blocked')
        self.assertTrue(check.reasons[0].startswith('Other vendor: SKU-E'))
        self.assertIn('Amazon AU', check.reasons[0])
        mock_live.assert_not_called()

    @mock.patch(LIVE, return_value=live(None, 0))
    def test_vendor_out_of_stock_page(self, _live):
        self._map('SKU-A')
        check = check_order(self._order([self.line('SKU-A', 5000)]))
        self.assertEqual(check.lines_json[0]['result'], 'out_of_stock')

    @mock.patch(LIVE, return_value={'error_code': 'blocked', 'error_message': 'Captcha page', 'source': 'live'})
    def test_scrape_error_blocks_with_message(self, _live):
        self._map('SKU-A')
        check = check_order(self._order([self.line('SKU-A', 5000)]))
        self.assertEqual(check.status, 'blocked')
        self.assertIn('Captcha page', check.reasons[0])

    @mock.patch(LIVE, return_value=live('20.00', None))
    def test_unknown_stock_blocks(self, _live):
        self._map('SKU-A')
        check = check_order(self._order([self.line('SKU-A', 5000)]))
        self.assertEqual(check.reasons, ['Stock unknown: SKU-A'])

    @mock.patch(LIVE, return_value=live('10.00', 5))
    def test_one_bad_line_blocks_the_whole_order(self, _live):
        self._map('SKU-A')
        check = check_order(self._order([self.line('SKU-A', 5000), self.line('SKU-MISSING', 5000)]))
        self.assertEqual(check.status, 'blocked')
        self.assertEqual(check.reasons, ['Not mapped: SKU-MISSING'])
        self.assertIsNone(check.earning_cents)

    @mock.patch(LIVE, return_value=live('70.00', 5))
    def test_bigw_uses_commission_from_file(self, _live):
        store = Store.objects.create(
            user=self.user, name='BW-P&P', region='AU', api_token='t', marketplace=self.mydeal,
            auto_buy_vendor='amazon_au',
        )
        self._map('PPCO-11360-New', store=store)
        check = check_order(self._order([self.line('PPCO-11360-New', 9664, qty=2, commission=3190)], store=store))
        self.assertEqual((check.total_sale_cents, check.fee_cents, check.after_fees_cents), (19328, 3190, 16138))
        self.assertEqual(check.cost_cents, 14000)
        self.assertEqual(check.earning_cents, 2138)
        self.assertEqual(check.status, 'buyable')

    @mock.patch(LIVE, return_value=live('10.00', 5))
    def test_missing_fee_percent_blocks(self, _live):
        StoreVendorPriceSettings.objects.filter(store=self.store).delete()
        self._map('SKU-A')
        check = check_order(self._order([self.line('SKU-A', 5000)]))
        self.assertEqual(check.status, 'blocked')
        self.assertIn('Marketplace Fees (%)', check.reasons[0])

    @mock.patch(LIVE)
    def test_costway_uses_feed_price(self, mock_live):
        from stores.auto_buy import set_auto_buy_entries

        set_auto_buy_entries(self.store, [('costway_au', None)])
        StoreVendorPriceSettings.objects.create(store=self.store, vendor=self.costway, marketplace_fees_percentage=15)
        pm = self._map('TFS-COW-1-New', vendor=self.costway, vendor_sku='KC59367NA')
        VendorPrice.objects.create(product=pm.product, price=Decimal('150.00'), stock=4)
        check = check_order(self._order([self.line('TFS-COW-1-New', 23049)]))
        mock_live.assert_not_called()
        ln = check.lines_json[0]
        self.assertEqual((ln['price_source'], ln['vendor_price_cents'], ln['vendor_stock']), ('feed', 15000, 4))
        self.assertEqual(check.status, 'buyable')

    @mock.patch(LIVE, side_effect=[live('10.00', 5), RuntimeError('browser crashed')])
    def test_worker_keeps_going_after_a_bad_order(self, _live):
        self._map('SKU-A')
        a = self._order([self.line('SKU-A', 5000)], key='A')
        b = self._order([self.line('SKU-A', 5000)], key='B')
        counts = run_purchase_checks(str(self.store.id), [a.id, b.id])
        self.assertEqual(counts, {'checked': 2, 'buyable': 1, 'blocked': 1})
        statuses = sorted(OrderPurchaseCheck.objects.filter(order__in=[a, b]).values_list('status', flat=True))
        self.assertEqual(statuses, ['blocked', 'buyable'])
        blocked = OrderPurchaseCheck.objects.get(order__in=[a, b], status='blocked')
        self.assertIn('browser crashed', blocked.reasons[0])

    # ------------------------------------------------------------------ API

    @mock.patch('listings.tasks.check_auto_purchase_orders.apply_async')
    def test_upload_queues_a_check_for_each_order(self, mock_async):
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.post(
                f'/api/v1/stores/{self.store.id}/orders/import-file/',
                {'file': SimpleUploadedFile('OpenOrders.xlsx', kogan_xlsx())}, format='multipart',
            )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIn('Checking stock and profit for 3 order(s)', res.data['message'])
        mock_async.assert_called_once()
        store_id, ids = mock_async.call_args.kwargs['args']
        self.assertEqual((store_id, len(ids)), (str(self.store.id), 3))
        self.assertEqual(OrderPurchaseCheck.objects.filter(store=self.store, status='checking').count(), 3)

        listing = self.client.get(f'/api/v1/stores/{self.store.id}/orders/')
        self.assertEqual({o['purchase_check']['status'] for o in listing.data['orders']}, {'checking'})

    @mock.patch('listings.tasks.check_auto_purchase_orders.apply_async')
    def test_recheck_one_and_all(self, mock_async):
        a = self._order([self.line('SKU-A', 5000)], key='A')
        self._order([self.line('SKU-A', 5000)], key='B')
        closed = self._order([self.line('SKU-A', 5000)], key='C')
        closed.status = OrderStatus.CANCELLED
        closed.save()
        with self.captureOnCommitCallbacks(execute=True):
            one = self.client.post(f'/api/v1/stores/{self.store.id}/orders/{a.id}/purchase-check/')
        self.assertEqual(one.data['queued'], 1)
        with self.captureOnCommitCallbacks(execute=True):
            every = self.client.post(f'/api/v1/stores/{self.store.id}/orders/purchase-check/')
        self.assertEqual(every.data['queued'], 2)  # the cancelled order is left alone
        self.assertEqual(mock_async.call_count, 2)

    @mock.patch('listings.tasks.check_auto_purchase_orders.apply_async', side_effect=OSError('no broker'))
    def test_queue_failure_tells_the_user(self, _async):
        a = self._order([self.line('SKU-A', 5000)], key='A')
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(f'/api/v1/stores/{self.store.id}/orders/{a.id}/purchase-check/')
        check = OrderPurchaseCheck.objects.get(order=a)
        self.assertEqual(check.status, 'blocked')
        self.assertIn('Could not start the check', check.reasons[0])

    def test_recheck_needs_auto_purchase_store(self):
        self.store.auto_buy_vendor = ''
        self.store.save(update_fields=['auto_buy_vendor'])
        res = self.client.post(f'/api/v1/stores/{self.store.id}/orders/purchase-check/')
        self.assertEqual(res.status_code, 400)

    def test_bigw_upload_check_routes_to_au_queue(self):
        from catalog.celery_routing import CatalogScrapeTaskRouter

        route = CatalogScrapeTaskRouter().route_for_task(
            'listings.check_auto_purchase_orders', args=(str(self.store.id), []),
        )
        self.assertEqual(route, {'queue': 'heavy-au'})
        us = Store.objects.create(
            user=self.user, name='US', region='USA', api_token='t', marketplace=self.kogan, auto_buy_vendor='amazon',
        )
        route = CatalogScrapeTaskRouter().route_for_task('listings.check_auto_purchase_orders', args=(str(us.id), []))
        self.assertEqual(route, {'queue': 'heavy-us'})

    @mock.patch(LIVE, return_value=live('70.00', 5))
    def test_real_bigw_file_end_to_end(self, _live):
        store = Store.objects.create(
            user=self.user, name='BW', region='AU', api_token='t', marketplace=self.mydeal, auto_buy_vendor='amazon_au',
        )
        self._map('PPCO-11360-New', store=store)
        with mock.patch('listings.tasks.check_auto_purchase_orders.apply_async') as mock_async:
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(
                    f'/api/v1/stores/{store.id}/orders/import-file/',
                    {'file': SimpleUploadedFile('p.csv', BIGW_CSV.encode())}, format='multipart',
                )
        store_id, ids = mock_async.call_args.kwargs['args']
        run_purchase_checks(store_id, ids)
        checks = {c.order.invoice_number: c for c in OrderPurchaseCheck.objects.filter(store=store).select_related('order')}
        self.assertEqual(checks['1023584140'].status, 'buyable')
        self.assertEqual(checks['1023584140'].earning_cents, 2138)
        self.assertEqual(checks['1023815960'].reasons, ['Not mapped: NPP-525247166740-New'])


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class MultiVendorCheckTests(PurchaseCheckTests):
    """Store buys from Amazon AU and eBay AU: lines from either pass; a mixed order is bought by hand."""

    def setUp(self):
        super().setUp()
        from stores.auto_buy import set_auto_buy_entries

        set_auto_buy_entries(self.store, [('amazon_au', None), ('ebay_au', None)])
        StoreVendorPriceSettings.objects.create(store=self.store, vendor=self.ebay, marketplace_fees_percentage=15)

    @mock.patch(LIVE, return_value=live('10.00', 5))
    def test_second_vendor_line_is_accepted(self, _live):
        self._map('SKU-E', vendor=self.ebay, vendor_sku='123456')
        check = check_order(self._order([self.line('SKU-E', 5000)]))
        self.assertEqual(check.status, 'buyable')
        self.assertEqual(check.lines_json[0]['auto_buy_vendor'], 'ebay_au')

    @mock.patch(LIVE, return_value=live('10.00', 5))
    def test_order_split_across_vendors_is_blocked(self, _live):
        self._map('SKU-A')
        self._map('SKU-E', vendor=self.ebay, vendor_sku='123456')
        check = check_order(self._order([self.line('SKU-A', 5000), self.line('SKU-E', 5000)]))
        self.assertEqual(check.status, 'blocked')
        self.assertIn('different vendors (Amazon AU, eBay AU)', check.reasons[0])

    def test_other_vendor_blocks_without_scraping(self):  # Costco is still not one of this store's vendors
        costco, _ = Vendor.objects.get_or_create(code='costcoau', defaults={'name': 'CostcoAU'})
        self._map('SKU-C', vendor=costco, vendor_sku='173734')
        with mock.patch(LIVE) as mock_live:
            check = check_order(self._order([self.line('SKU-C', 5000)]))
        self.assertTrue(check.reasons[0].startswith('Other vendor: SKU-C'))
        self.assertIn('Amazon AU, eBay AU', check.reasons[0])
        mock_live.assert_not_called()


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class KoganSkuPrefixTests(PurchaseCheckTests):
    """Kogan order SKUs start with TFS-; the catalog lookup ignores it without changing the order's SKU."""

    @mock.patch(LIVE, return_value=live('20.99', 5))
    def test_catalog_sku_without_tfs_matches_the_order_sku_with_it(self, _live):
        self._map('COW-18324956-HV10819WH-New')
        check = check_order(self._order([self.line('TFS-COW-18324956-HV10819WH-New', 7088)]))
        self.assertEqual(check.status, 'buyable', check.reasons)
        self.assertEqual(check.lines_json[0]['sku'], 'TFS-COW-18324956-HV10819WH-New')  # order SKU unchanged
        self.assertEqual(check.lines_json[0]['vendor_id'], 'B0TEST0001')

    @mock.patch(LIVE, return_value=live('20.99', 5))
    def test_exact_tfs_catalog_sku_still_wins(self, _live):
        exact = self._map('TFS-COW-1-New', vendor_sku='B0EXACT001')
        self._map('COW-1-New', vendor_sku='B0OTHER001')
        check = check_order(self._order([self.line('TFS-COW-1-New', 5000)]))
        self.assertEqual(check.lines_json[0]['mapping_id'], str(exact.id))

    def test_prefix_is_case_insensitive_and_only_leading(self):
        from listings.auto_purchase_check import catalog_sku_candidates

        self.assertEqual(catalog_sku_candidates(self.store, 'tfs-COW-1-New'), ['tfs-COW-1-New', 'COW-1-New'])
        self.assertEqual(catalog_sku_candidates(self.store, 'COW-TFS-1-New'), ['COW-TFS-1-New'])
        self.assertEqual(catalog_sku_candidates(self.store, 'TFS-'), ['TFS-'])

    @mock.patch(LIVE, return_value=live('20.99', 5))
    def test_other_marketplaces_do_not_ignore_the_prefix(self, _live):
        store = Store.objects.create(
            user=self.user, name='BW', region='AU', api_token='t', marketplace=self.mydeal, auto_buy_vendor='amazon_au',
        )
        self._map('COW-1-New', store=store)
        check = check_order(self._order([self.line('TFS-COW-1-New', 5000)], store=store))
        self.assertEqual(check.reasons, ['Not mapped: TFS-COW-1-New'])

    def test_still_not_mapped_when_neither_form_exists(self):
        check = check_order(self._order([self.line('TFS-NOPE-1-New', 5000)]))
        self.assertEqual(check.reasons, ['Not mapped: TFS-NOPE-1-New'])
