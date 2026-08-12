"""Tests for store orphan / reclaim / purge retention."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from listings.models import MarketplaceOrder, StoreListing
from marketplace.models import Marketplace
from stores.models import Store
from stores.orphan import (
    find_orphaned_store,
    orphan_stores_for_user,
    purge_expired_orphaned_stores,
    reclaim_store,
    store_credential_fingerprint,
)

User = get_user_model()


class StoreOrphanTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username='usera', email='a@example.com', password='x')
        self.user_b = User.objects.create_user(username='userb', email='b@example.com', password='x')
        self.mkt, _ = Marketplace.objects.get_or_create(
            code='lasoo',
            defaults={'name': 'Lasoo'},
        )
        self.store = Store.objects.create(
            user=self.user_a,
            name='Lasoo Shop',
            region='AU',
            management_mode='full_store',
            marketplace=self.mkt,
            api_token='',
            lasoo_staging_auth_key='staging-key-abc',
            lasoo_production_auth_key='prod-key-xyz',
        )
        self.listing = StoreListing.objects.create(
            user=self.user_a,
            store=self.store,
            external_product_key='P1',
            external_variant_key='V1',
            title='Mirror',
            sku='SKU-1',
            vendor_url='https://vendor.example.com/mirror',
            environment='production',
        )
        self.order = MarketplaceOrder.objects.create(
            user=self.user_a,
            store=self.store,
            external_order_key='1031397',
            invoice_number='1031397',
            environment='production',
        )

    def test_user_delete_orphans_store_and_keeps_listings(self):
        store_id = self.store.id
        listing_id = self.listing.id
        order_id = self.order.id
        self.user_a.delete()

        store = Store.objects.get(pk=store_id)
        self.assertIsNone(store.user_id)
        self.assertIsNotNone(store.orphaned_at)
        self.assertFalse(store.is_active)
        self.assertTrue(store.credential_fingerprint)

        listing = StoreListing.objects.get(pk=listing_id)
        self.assertEqual(listing.store_id, store_id)
        self.assertEqual(listing.vendor_url, 'https://vendor.example.com/mirror')

        order = MarketplaceOrder.objects.get(pk=order_id)
        self.assertEqual(order.store_id, store_id)

    def test_reclaim_restores_owner_and_listing_user(self):
        orphan_stores_for_user(self.user_a)
        self.store.refresh_from_db()
        fp = store_credential_fingerprint(self.store)
        found = find_orphaned_store(marketplace=self.mkt, fingerprint=fp)
        self.assertEqual(found.pk, self.store.pk)

        reclaim_store(
            self.store,
            self.user_b,
            store_data={
                'name': 'Lasoo Shop',
                'lasoo_staging_auth_key': 'staging-key-abc',
                'lasoo_production_auth_key': 'prod-key-xyz',
            },
        )
        self.store.refresh_from_db()
        self.assertEqual(self.store.user_id, self.user_b.id)
        self.assertIsNone(self.store.orphaned_at)
        self.assertTrue(self.store.is_active)

        self.listing.refresh_from_db()
        self.assertEqual(self.listing.user_id, self.user_b.id)
        self.assertEqual(self.listing.vendor_url, 'https://vendor.example.com/mirror')

    @override_settings(STORE_ORPHAN_RETENTION_DAYS=30)
    def test_purge_deletes_expired_orphans_only(self):
        orphan_stores_for_user(self.user_a)
        self.store.refresh_from_db()
        Store.objects.filter(pk=self.store.pk).update(
            orphaned_at=timezone.now() - timedelta(days=31),
        )
        fresh = Store.objects.create(
            user=self.user_b,
            name='Other',
            region='AU',
            marketplace=self.mkt,
            api_token='tok',
        )
        orphan_stores_for_user(self.user_b)
        result = purge_expired_orphaned_stores()
        self.assertEqual(result['deleted'], 1)
        self.assertFalse(Store.objects.filter(pk=self.store.pk).exists())
        self.assertTrue(Store.objects.filter(pk=fresh.pk).exists())
