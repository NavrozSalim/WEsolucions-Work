"""Auto Purchasing step: the store's Auto buying vendor (Phase 1)."""
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from marketplace.models import Marketplace
from stores.models import Store

User = get_user_model()


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class AutoBuyVendorTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='auto_buy_u', email='auto_buy@example.com', password='pass12345',
        )
        self.mp, _ = Marketplace.objects.get_or_create(code='kogan', defaults={'name': 'Kogan'})
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _store(self, region='AU', **kw):
        return Store.objects.create(
            user=self.user, name=f'Kogan-TFS {region}', region=region,
            api_token='tok', marketplace=self.mp, **kw,
        )

    def _patch(self, store, data):
        return self.client.patch(f'/api/v1/stores/{store.id}/', data, format='json')

    def test_au_store_accepts_au_vendor(self):
        store = self._store('AU')
        res = self._patch(store, {'auto_buy_vendor': 'costco_au'})
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['auto_buy_vendor'], 'costco_au')
        store.refresh_from_db()
        self.assertEqual(store.auto_buy_vendor, 'costco_au')
        self.assertTrue(store.is_auto_purchase)

    def test_au_store_rejects_us_vendor(self):
        store = self._store('AU')
        res = self._patch(store, {'auto_buy_vendor': 'amazon'})
        self.assertEqual(res.status_code, 400)
        self.assertIn('auto_buy_vendor', res.data)
        store.refresh_from_db()
        self.assertEqual(store.auto_buy_vendor, '')

    def test_us_store_rejects_au_vendor(self):
        store = self._store('USA')
        res = self._patch(store, {'auto_buy_vendor': 'costway_au'})
        self.assertEqual(res.status_code, 400)
        self.assertIn('Amazon US, eBay US', str(res.data['auto_buy_vendor']))

    def test_unknown_vendor_rejected(self):
        store = self._store('AU')
        res = self._patch(store, {'auto_buy_vendor': 'vevor_au'})
        self.assertEqual(res.status_code, 400)

    def test_blank_turns_auto_purchasing_off(self):
        store = self._store('AU', auto_buy_vendor='amazon_au')
        res = self._patch(store, {'auto_buy_vendor': ''})
        self.assertEqual(res.status_code, 200, res.data)
        store.refresh_from_db()
        self.assertEqual(store.auto_buy_vendor, '')
        self.assertFalse(store.is_auto_purchase)

    def test_other_edits_keep_the_vendor(self):
        store = self._store('AU', auto_buy_vendor='ebay_au')
        res = self._patch(store, {'name': 'Kogan-TFS renamed'})
        self.assertEqual(res.status_code, 200, res.data)
        store.refresh_from_db()
        self.assertEqual(store.auto_buy_vendor, 'ebay_au')

    def test_catalog_store_list_includes_vendor_for_orders_page(self):
        self._store('AU', auto_buy_vendor='amazon_au')
        res = self.client.get('/api/v1/catalog/stores/')
        self.assertEqual(res.status_code, 200)
        row = next(r for r in res.data if r['name'] == 'Kogan-TFS AU')
        self.assertEqual(row['auto_buy_vendor'], 'amazon_au')
        self.assertEqual(row['management_mode'], 'inventory_only')

    def test_choices_match_purchase_account_vendors(self):
        """The bot buys with a PurchaseAccount of the same vendor code."""
        from stores.models import PurchaseAccount

        for region, vendors in Store.AUTO_BUY_VENDORS_BY_REGION.items():
            for v in vendors:
                self.assertIn(v, PurchaseAccount.REGION_VENDORS[region])
