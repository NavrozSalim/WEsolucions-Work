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


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class AutoBuyAccountTests(TestCase):
    """The Auto Purchasing step also picks (or creates) the purchase account Process buys with."""

    def setUp(self):
        from stores.models import PurchaseAccount

        self.PA = PurchaseAccount
        self.user = User.objects.create_user(username='aba', email='aba@example.com', password='pass12345')
        self.mp, _ = Marketplace.objects.get_or_create(code='kogan', defaults={'name': 'Kogan'})
        self.store = Store.objects.create(user=self.user, name='Kogan-TFS', region='AU', api_token='t',
                                          marketplace=self.mp, auto_buy_vendor='amazon_au')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def patch(self, data):
        return self.client.patch(f'/api/v1/stores/{self.store.id}/', data, format='json')

    def test_new_account_from_the_step_is_created_and_linked(self):
        res = self.patch({'auto_buy_vendor': 'costco_au',
                          'auto_buy_account_new': {'chrome_profile': 'Profile 3', 'email': 'buyer@example.com'}})
        self.assertEqual(res.status_code, 200, res.data)
        acct = self.PA.objects.get(user=self.user)
        self.assertEqual((acct.name, acct.vendor, acct.region, acct.chrome_profile, acct.email),
                         ('Costco AU', 'costco_au', 'AU', 'Profile 3', 'buyer@example.com'))
        self.assertEqual(res.data['auto_buy_account'], acct.id)
        self.assertEqual(res.data['auto_buy_account_detail']['chrome_profile'], 'Profile 3')

        # Same profile again (e.g. a second store) reuses the account instead of making another.
        again = self.patch({'auto_buy_account_new': {'chrome_profile': 'Profile 3'}})
        self.assertEqual(again.status_code, 200, again.data)
        self.assertEqual(self.PA.objects.filter(user=self.user).count(), 1)

    def test_existing_account_can_be_picked(self):
        acct = self.PA.objects.create(user=self.user, name='Amazon AU', vendor='amazon_au', region='AU',
                                      chrome_profile='Default')
        res = self.patch({'auto_buy_account': str(acct.id)})
        self.assertEqual(res.status_code, 200, res.data)
        self.store.refresh_from_db()
        self.assertEqual(self.store.auto_buy_account_id, acct.id)

    def test_wrong_vendor_or_other_users_account_refused(self):
        ebay = self.PA.objects.create(user=self.user, name='eBay AU', vendor='ebay_au', region='AU', chrome_profile='Profile 2')
        self.assertEqual(self.patch({'auto_buy_account': str(ebay.id)}).status_code, 400)
        other = User.objects.create_user(username='o', email='o@example.com', password='pass12345')
        theirs = self.PA.objects.create(user=other, name='X', vendor='amazon_au', region='AU', chrome_profile='Default')
        res = self.patch({'auto_buy_account': str(theirs.id)})
        self.assertEqual(res.status_code, 400)
        self.assertIn('your own', str(res.data['auto_buy_account']))

    def test_new_account_needs_a_profile(self):
        res = self.patch({'auto_buy_account_new': {'chrome_profile': ' '}})
        self.assertEqual(res.status_code, 400)

    def test_clearing_vendor_clears_account_and_vendor_change_drops_it(self):
        acct = self.PA.objects.create(user=self.user, name='Amazon AU', vendor='amazon_au', region='AU', chrome_profile='Default')
        self.store.auto_buy_account = acct
        self.store.save()
        self.assertEqual(self.patch({'auto_buy_vendor': 'ebay_au'}).status_code, 200)
        self.store.refresh_from_db()
        self.assertIsNone(self.store.auto_buy_account_id)
        self.store.auto_buy_account = acct
        self.store.auto_buy_vendor = 'amazon_au'
        self.store.save()
        self.patch({'auto_buy_vendor': ''})
        self.store.refresh_from_db()
        self.assertIsNone(self.store.auto_buy_account_id)

    def test_process_uses_the_linked_account(self):
        from listings.auto_purchase_jobs import purchase_account_for

        first = self.PA.objects.create(user=self.user, name='A1', vendor='amazon_au', region='AU', chrome_profile='P1')
        chosen = self.PA.objects.create(user=self.user, name='A2', vendor='amazon_au', region='AU', chrome_profile='Default')
        self.assertEqual(purchase_account_for(self.store), first)
        self.store.auto_buy_account = chosen
        self.store.save()
        self.assertEqual(purchase_account_for(self.store), chosen)

    def test_my_accounts_list_for_the_wizard(self):
        self.PA.objects.create(user=self.user, name='Amazon AU', vendor='amazon_au', region='AU', chrome_profile='Default')
        self.PA.objects.create(user=self.user, name='Amazon US', vendor='amazon', region='USA', chrome_profile='Profile 7')
        res = self.client.get('/api/v1/purchase-accounts/', {'region': 'AU', 'vendor': 'amazon_au'})
        self.assertEqual([a['name'] for a in res.data['accounts']], ['Amazon AU'])


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class AutoBuyVendorListTests(TestCase):
    """Several Auto buying vendors per store, each with its own purchase account."""

    def setUp(self):
        from stores.models import PurchaseAccount

        self.PA = PurchaseAccount
        self.user = User.objects.create_user(username='abl', email='abl@example.com', password='pass12345')
        mp, _ = Marketplace.objects.get_or_create(code='kogan', defaults={'name': 'Kogan'})
        self.store = Store.objects.create(user=self.user, name='Kogan-TFS', region='AU', api_token='t', marketplace=mp)
        self.amazon = self.PA.objects.create(user=self.user, name='Amazon AU', vendor='amazon_au', region='AU',
                                             chrome_profile='Default')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def patch(self, rows):
        return self.client.patch(f'/api/v1/stores/{self.store.id}/', {'auto_buy_vendors': rows}, format='json')

    def get(self):
        return self.client.get(f'/api/v1/stores/{self.store.id}/').data

    def test_two_vendors_saved_and_shown_again(self):
        res = self.patch([
            {'vendor': 'amazon_au', 'account': str(self.amazon.id)},
            {'vendor': 'costco_au', 'account_new': {'chrome_profile': 'Profile 3', 'email': 'c@example.com'}},
        ])
        self.assertEqual(res.status_code, 200, res.data)
        rows = self.get()['auto_buy_vendors']
        self.assertEqual([(r['vendor'], r['account']['chrome_profile']) for r in rows],
                         [('amazon_au', 'Default'), ('costco_au', 'Profile 3')])
        self.store.refresh_from_db()
        self.assertEqual((self.store.auto_buy_vendor, self.store.auto_buy_account_id), ('amazon_au', self.amazon.id))
        self.assertTrue(self.store.is_auto_purchase)

        listed = self.client.get('/api/v1/catalog/stores/').data
        row = next(r for r in listed if r['name'] == 'Kogan-TFS')
        self.assertEqual(row['auto_buy_vendors'], ['amazon_au', 'costco_au'])

    def test_removing_a_row_and_clearing_all(self):
        self.patch([{'vendor': 'amazon_au', 'account': str(self.amazon.id)}, {'vendor': 'ebay_au'}])
        self.patch([{'vendor': 'ebay_au'}])
        self.assertEqual([r['vendor'] for r in self.get()['auto_buy_vendors']], ['ebay_au'])
        self.patch([])
        self.store.refresh_from_db()
        self.assertEqual(self.get()['auto_buy_vendors'], [])
        self.assertFalse(self.store.is_auto_purchase)

    def test_duplicate_or_wrong_region_vendor_refused(self):
        self.assertEqual(self.patch([{'vendor': 'amazon_au'}, {'vendor': 'amazon_au'}]).status_code, 400)
        self.assertEqual(self.patch([{'vendor': 'amazon'}]).status_code, 400)
        self.assertEqual(self.patch([{'vendor': 'costco_au', 'account': str(self.amazon.id)}]).status_code, 400)

    def test_old_single_vendor_save_becomes_one_row(self):
        res = self.client.patch(f'/api/v1/stores/{self.store.id}/', {'auto_buy_vendor': 'costway_au'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual([r['vendor'] for r in self.get()['auto_buy_vendors']], ['costway_au'])

    def test_process_account_follows_the_vendor(self):
        from listings.auto_purchase_jobs import purchase_account_for

        self.patch([
            {'vendor': 'amazon_au', 'account': str(self.amazon.id)},
            {'vendor': 'costco_au', 'account_new': {'chrome_profile': 'Profile 3'}},
        ])
        self.store.refresh_from_db()
        self.assertEqual(purchase_account_for(self.store, 'amazon_au'), self.amazon)
        self.assertEqual(purchase_account_for(self.store, 'costco_au').chrome_profile, 'Profile 3')
