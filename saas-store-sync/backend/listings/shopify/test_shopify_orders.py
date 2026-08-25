"""Shopify order-mirror helpers and push behaviour."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from listings.models import MarketplaceOrder
from listings.shopify.client import normalize_location_id, normalize_shop_domain, shop_handle
from listings.shopify.orders import (
    admin_order_url,
    build_order_create_input,
    normalize_shopify_phone,
    push_new_order_to_shopify,
    tracking_tag,
)
from marketplace.models import Marketplace
from stores.models import Store


class ShopifyClientHelpersTests(TestCase):
    def test_normalize_shop_domain(self):
        self.assertEqual(normalize_shop_domain("t4nx6h-ds.myshopify.com"), "t4nx6h-ds.myshopify.com")
        self.assertEqual(normalize_shop_domain("https://t4nx6h-ds.myshopify.com/admin"), "t4nx6h-ds.myshopify.com")
        self.assertEqual(normalize_shop_domain("t4nx6h-ds"), "t4nx6h-ds.myshopify.com")
        self.assertEqual(
            normalize_shop_domain("admin.shopify.com/store/t4nx6h-ds"),
            "t4nx6h-ds.myshopify.com",
        )

    def test_location_id_from_url(self):
        self.assertEqual(normalize_location_id("123456789"), "123456789")
        self.assertEqual(
            normalize_location_id("https://admin.shopify.com/store/x/settings/locations/123456789"),
            "123456789",
        )
        self.assertEqual(normalize_location_id("gid://shopify/Location/123456789"), "123456789")

    def test_tracking_tag(self):
        self.assertEqual(tracking_tag("mydeal", "343544536"), "sp-mydeal-343544536")

    def test_normalize_shopify_phone(self):
        self.assertEqual(normalize_shopify_phone("0412 345 678", "AU"), "+61412345678")
        self.assertEqual(normalize_shopify_phone("+61 412 345 678", "AU"), "+61412345678")
        self.assertEqual(normalize_shopify_phone("+610412345678", "AU"), "+61412345678")
        self.assertEqual(normalize_shopify_phone("0400000000", "AU"), "")
        self.assertEqual(normalize_shopify_phone("0", "AU"), "")
        self.assertEqual(normalize_shopify_phone("n/a", "AU"), "")
        self.assertEqual(normalize_shopify_phone("202-555-0123", "US"), "+12025550123")


class ShopifyOrderPushTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="sp", email="sp@example.com", password="pw")
        self.mydeal, _ = Marketplace.objects.get_or_create(code="mydeal", defaults={"name": "MyDeal"})
        self.store = Store.objects.create(
            user=self.user,
            name="Shemaya MyDeal",
            region="AU",
            api_token="",
            marketplace=self.mydeal,
            management_mode="full_store",
            shopify_enabled=True,
            shopify_shop_domain="t4nx6h-ds.myshopify.com",
            shopify_client_id="cid",
            shopify_client_secret="csecret",
        )

    def _order(self, **kwargs):
        defaults = {
            "user": self.user,
            "store": self.store,
            "external_order_key": "343544536",
            "invoice_number": "343544536",
            "environment": "production",
            "total_amount_cents": 6000,
            "customer_info_json": {
                "firstName": "Sam",
                "lastName": "Seller",
                "email": "buyer@example.com",
                "phone": "0412 345 678",
                "shippingAddress": {
                    "line1": "1 Test St",
                    "city": "Sydney",
                    "state": "NSW",
                    "postcode": "2000",
                    "country": "AU",
                },
            },
            "line_items_json": [
                {"title": "Widget", "sku": "SKU-1", "quantity": 2, "priceCents": 2500},
            ],
            "raw_response_json": {"currency": "AUD"},
        }
        defaults.update(kwargs)
        return MarketplaceOrder.objects.create(**defaults)

    def test_skip_existing_local_order(self):
        order = self._order()
        with patch("listings.shopify.orders.graphql") as gql:
            push_new_order_to_shopify(order, self.store, created=False)
            gql.assert_not_called()
        order.refresh_from_db()
        self.assertEqual(order.shopify_order_id, "")

    def test_skip_when_shopify_disabled(self):
        self.store.shopify_enabled = False
        self.store.save(update_fields=["shopify_enabled"])
        order = self._order()
        with patch("listings.shopify.orders.graphql") as gql:
            push_new_order_to_shopify(order, self.store, created=True)
            gql.assert_not_called()

    def test_attach_existing_shopify_order(self):
        order = self._order()
        with patch("listings.shopify.orders.graphql") as gql:
            gql.return_value = {
                "orders": {
                    "nodes": [{
                        "id": "gid://shopify/Order/999",
                        "name": "#1042",
                        "tags": ["sp-mydeal-343544536"],
                    }],
                }
            }
            push_new_order_to_shopify(order, self.store, created=True)
        order.refresh_from_db()
        self.assertEqual(order.shopify_order_id, "999")
        self.assertEqual(order.shopify_order_name, "#1042")
        self.assertTrue(admin_order_url(self.store, order).endswith("/orders/999"))
        self.assertEqual(gql.call_count, 1)

    def test_create_shopify_order(self):
        order = self._order()
        with patch("listings.shopify.orders.graphql") as gql:
            def _side_effect(_store, query, variables=None):
                if "FindMarketplaceOrder" in query:
                    return {"orders": {"nodes": []}}
                if "VariantBySku" in query:
                    return {"productVariants": {"nodes": []}}
                if "OrderCreate" in query:
                    return {
                        "orderCreate": {
                            "order": {"id": "gid://shopify/Order/777", "name": "#1100"},
                            "userErrors": [],
                        }
                    }
                return {}
            gql.side_effect = _side_effect
            push_new_order_to_shopify(order, self.store, created=True)
        order.refresh_from_db()
        self.assertEqual(order.shopify_order_id, "777")
        self.assertEqual(order.shopify_order_name, "#1100")
        self.assertIn("/store/t4nx6h-ds/orders/777", admin_order_url(self.store, order))

    def test_build_payload_tags_and_paid(self):
        order = self._order()
        with patch("listings.shopify.orders.graphql", return_value={"productVariants": {"nodes": []}}):
            built = build_order_create_input(
                order, self.store, kind="mydeal", tag="sp-mydeal-343544536",
            )
        self.assertEqual(built["order"]["financialStatus"], "PAID")
        self.assertIn("mydeal", built["order"]["tags"])
        self.assertIn("sp-mydeal-343544536", built["order"]["tags"])
        self.assertEqual(built["order"]["lineItems"][0]["sku"], "SKU-1")
        self.assertEqual(built["order"]["lineItems"][0]["quantity"], 2)
        self.assertEqual(built["order"]["phone"], "+61412345678")
        self.assertEqual(built["order"]["shippingAddress"]["phone"], "+61412345678")
        attrs = {a["key"]: a["value"] for a in built["order"]["customAttributes"]}
        self.assertEqual(attrs["marketplace"], "mydeal")
        self.assertNotIn("order_source", attrs)

    def test_build_payload_includes_bigw_order_source(self):
        order = self._order(raw_response_json={
            "currency": "AUD",
            "orderSource": "BigW",
            "_mydeal": {"OrderSource": "BigW", "OrderId": 343544536},
        })
        with patch("listings.shopify.orders.graphql", return_value={"productVariants": {"nodes": []}}):
            built = build_order_create_input(
                order, self.store, kind="mydeal", tag="sp-mydeal-343544536",
            )
        attrs = {a["key"]: a["value"] for a in built["order"]["customAttributes"]}
        self.assertEqual(attrs["marketplace"], "mydeal")
        self.assertEqual(attrs["order_source"], "BigW")
        self.assertIn("bigw", built["order"]["tags"])
        self.assertIn("mydeal", built["order"]["tags"])
        self.assertIn("Mydeal / BigW order 343544536", built["order"]["note"])

    def test_build_payload_omits_invalid_phone(self):
        order = self._order(customer_info_json={
            "firstName": "Sam",
            "lastName": "Seller",
            "email": "buyer@example.com",
            "phone": "0400000000",
            "shippingAddress": {
                "line1": "1 Test St",
                "city": "Sydney",
                "state": "NSW",
                "postcode": "2000",
                "country": "AU",
                "phone": "0",
            },
        })
        with patch("listings.shopify.orders.graphql", return_value={"productVariants": {"nodes": []}}):
            built = build_order_create_input(
                order, self.store, kind="mydeal", tag="sp-mydeal-343544536",
            )
        self.assertNotIn("phone", built["order"])
        self.assertNotIn("phone", built["order"].get("shippingAddress") or {})

    def test_retry_after_sync_error(self):
        order = self._order(shopify_sync_error="Order Phone is invalid")
        with patch("listings.shopify.orders.graphql") as gql:
            def _side_effect(_store, query, variables=None):
                if "FindMarketplaceOrder" in query:
                    return {"orders": {"nodes": []}}
                if "VariantBySku" in query:
                    return {"productVariants": {"nodes": []}}
                if "OrderCreate" in query:
                    return {
                        "orderCreate": {
                            "order": {"id": "gid://shopify/Order/888", "name": "#1200"},
                            "userErrors": [],
                        }
                    }
                return {}
            gql.side_effect = _side_effect
            push_new_order_to_shopify(order, self.store, created=False)
        order.refresh_from_db()
        self.assertEqual(order.shopify_order_id, "888")
        self.assertEqual(order.shopify_sync_error, "")

    def test_mydeal_upsert_retries_failed_shopify_push(self):
        from listings.mydeal.orders import upsert_order

        raw = {
            "OrderId": 136554825,
            "OrderStatus": "SellerAcknowledged",
            "TotalPrice": 60.0,
            "Currency": "AUD",
            "CustomerEmail": "buyer@example.com",
            "ShippingAddress": {
                "FirstName": "Sam",
                "LastName": "Seller",
                "Address1": "1 Test St",
                "Suburb": "Sydney",
                "State": "NSW",
                "PostalCode": "2000",
                "CountryCode": "AU",
                "Phone": "0400000000",
            },
            "LineItems": [{"SKU": "SKU-1", "Quantity": 1, "UnitPrice": 60.0, "ProductTitle": "Widget"}],
        }
        with patch("listings.shopify.orders.graphql") as gql:
            gql.side_effect = [
                {"orders": {"nodes": []}},
                {"productVariants": {"nodes": []}},
                {"orderCreate": {"order": None, "userErrors": [{"field": ["phone"], "message": "Order Phone is invalid"}]}},
            ]
            first = upsert_order(self.user, self.store, raw)
        first.refresh_from_db()
        self.assertEqual(first.shopify_sync_error, "Order Phone is invalid")
        self.assertEqual(first.shopify_order_id, "")

        with patch("listings.shopify.orders.graphql") as gql:
            def _side_effect(_store, query, variables=None):
                if "FindMarketplaceOrder" in query:
                    return {"orders": {"nodes": []}}
                if "VariantBySku" in query:
                    return {"productVariants": {"nodes": []}}
                if "OrderCreate" in query:
                    payload = (variables or {}).get("order") or {}
                    self.assertNotIn("phone", payload)
                    self.assertNotIn("phone", payload.get("shippingAddress") or {})
                    return {
                        "orderCreate": {
                            "order": {"id": "gid://shopify/Order/42", "name": "#42"},
                            "userErrors": [],
                        }
                    }
                return {}
            gql.side_effect = _side_effect
            second = upsert_order(self.user, self.store, raw)
        self.assertEqual(first.id, second.id)
        second.refresh_from_db()
        self.assertEqual(second.shopify_order_id, "42")
        self.assertEqual(second.shopify_sync_error, "")

    def test_mydeal_upsert_only_pushes_on_create(self):
        from listings.mydeal.orders import upsert_order

        raw = {
            "OrderId": 343544536,
            "OrderStatus": "ReadytoFulfill",
            "TotalPrice": 60.0,
            "Currency": "AUD",
            "CustomerEmail": "buyer@example.com",
            "LineItems": [{"SKU": "SKU-1", "Quantity": 1, "UnitPrice": 60.0, "ProductTitle": "Widget"}],
        }
        with patch("listings.shopify.orders.graphql") as gql:
            gql.return_value = {"orders": {"nodes": []}, "orderCreate": {"order": {"id": "gid://shopify/Order/1", "name": "#1"}, "userErrors": []}}
            first = upsert_order(self.user, self.store, raw)
            second = upsert_order(self.user, self.store, raw)
        self.assertEqual(first.id, second.id)
        create_calls = [c for c in gql.call_args_list if "OrderCreate" in (c.args[1] if len(c.args) > 1 else "")]
        self.assertEqual(len(create_calls), 1)

    def test_shop_handle(self):
        self.assertEqual(shop_handle("t4nx6h-ds.myshopify.com"), "t4nx6h-ds")


class ShopifySerializerTests(TestCase):
    def test_rejects_shopify_on_inventory_only(self):
        from rest_framework.exceptions import ValidationError
        from stores.serializers import StoreSerializer

        mydeal, _ = Marketplace.objects.get_or_create(code="mydeal", defaults={"name": "MyDeal"})
        ser = StoreSerializer(context={"request": SimpleNamespace(data={
            "shopify_enabled": True,
            "shopify_shop_domain": "t4nx6h-ds.myshopify.com",
            "shopify_client_id": "cid",
            "shopify_client_secret": "sec",
        })})
        with self.assertRaises(ValidationError):
            ser._apply_shopify_fields(
                {},
                marketplace=mydeal,
                management_mode="inventory_only",
            )

    def test_domain_helper_on_simple_namespace(self):
        store = SimpleNamespace(shopify_shop_domain="t4nx6h-ds.myshopify.com")
        order = SimpleNamespace(shopify_order_id="12", shopify_order_gid="")
        self.assertEqual(
            admin_order_url(store, order),
            "https://admin.shopify.com/store/t4nx6h-ds/orders/12",
        )
