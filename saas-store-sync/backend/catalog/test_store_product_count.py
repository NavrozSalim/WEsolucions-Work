"""Catalog store list product_count for managed vs inventory stores."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from marketplace.models import Marketplace
from stores.models import Store
from listings.models import StoreListing


class CatalogStoresProductCountTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="cnt", email="cnt@example.com", password="pw")
        lasoo, _ = Marketplace.objects.get_or_create(code="lasoo", defaults={"name": "Lasoo"})
        mydeal, _ = Marketplace.objects.get_or_create(code="mydeal", defaults={"name": "MyDeal"})
        self.managed = Store.objects.create(
            user=self.user,
            name="Lasoo - P&P",
            region="AU",
            api_token="",
            marketplace=lasoo,
            management_mode="full_store",
            lasoo_environment="staging",
            lasoo_staging_auth_key="k",
        )
        self.inventory = Store.objects.create(
            user=self.user,
            name="Mydeal Inventory",
            region="AU",
            api_token="tok",
            marketplace=mydeal,
            management_mode="inventory_only",
        )
        StoreListing.objects.create(
            user=self.user,
            store=self.managed,
            external_product_key="A",
            external_variant_key="A",
            sku="A",
            title="One",
            description="d",
            brand="b",
            image_urls="https://img.example.com/a.jpg",
            original_price="10",
            sale_price="9",
            action="create",
        )
        StoreListing.objects.create(
            user=self.user,
            store=self.managed,
            external_product_key="B",
            external_variant_key="B",
            sku="B",
            title="Two",
            description="d",
            brand="b",
            image_urls="https://img.example.com/b.jpg",
            original_price="10",
            sale_price="9",
            action="mapped",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_managed_store_counts_listings(self):
        res = self.client.get("/api/v1/catalog/stores/")
        self.assertEqual(res.status_code, 200)
        by_name = {row["name"]: row for row in res.data}
        self.assertEqual(by_name["Lasoo - P&P"]["product_count"], 2)
        self.assertEqual(by_name["Lasoo - P&P"]["management_mode"], "full_store")

    def test_delete_listing_reduces_count(self):
        StoreListing.objects.filter(store=self.managed, sku="A").delete()
        res = self.client.get("/api/v1/catalog/stores/")
        by_name = {row["name"]: row for row in res.data}
        self.assertEqual(by_name["Lasoo - P&P"]["product_count"], 1)
