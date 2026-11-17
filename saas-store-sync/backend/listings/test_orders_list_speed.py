"""The orders list reads the store's listings once per response, not once per order."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from marketplace.models import Marketplace
from stores.models import Store

from . import order_service
from .models import MarketplaceOrder, OrderStatus, StoreListing
from .serializers import MarketplaceOrderSerializer


class OrdersListListingLookupTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username="speed", email="speed@example.com", password="pw",
        )
        lasoo, _ = Marketplace.objects.get_or_create(code="lasoo", defaults={"name": "Lasoo"})
        self.store = Store.objects.create(
            user=user, name="Speed", region="AU", api_token="", marketplace=lasoo,
            management_mode="full_store", lasoo_environment="staging",
        )
        StoreListing.objects.create(
            user=user, store=self.store, external_product_key="TEE-1",
            external_variant_key="TEE-1", title="Black Tee", sku="MKT-TEE-1",
            vendor_url="https://vendor.example.com/tee-1", environment="staging",
        )
        for n in range(5):
            MarketplaceOrder.objects.create(
                user=user, store=self.store, external_order_key=f"90{n}",
                invoice_number=f"90{n}", status=OrderStatus.PAID, environment="staging",
                line_items_json=[{"title": "Black Tee", "externalVariantKey": "TEE-1", "quantity": 1}],
            )

    def test_listing_indexes_built_once_for_many_orders(self):
        orders = list(MarketplaceOrder.objects.filter(store=self.store).select_related("store"))
        real = order_service._listing_lookup_indexes
        with patch.object(order_service, "_listing_lookup_indexes", side_effect=real) as spy:
            data = MarketplaceOrderSerializer(orders, many=True, context={}).data
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(len(data), 5)
        for row in data:
            item = row["details"]["lineItems"][0]
            self.assertEqual(item["marketplaceSku"], "MKT-TEE-1")
            self.assertEqual(row["details"]["sourceLinks"], ["https://vendor.example.com/tee-1"])
