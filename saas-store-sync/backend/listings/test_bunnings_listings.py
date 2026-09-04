"""Bunnings listing CSV/validate/publish and order status mapping tests."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from marketplace.models import Marketplace
from stores.models import Store

from listings import csv_import, listing_service, order_service
from listings.bunnings import orders as bunnings_orders
from listings.bunnings import products as bunnings_products
from listings.bunnings.client import BunningsResult, extract_import_id
from listings.errors import MarketplaceError
from listings.models import ListingStatus, OrderStatus

VALID_BUNNINGS = {
    "sku": "BN-1",
    "title": "Power Drill",
    "description": "18V cordless drill.",
    "brand": "ExampleBrand",
    "category": "DRILLS",
    "logistic_class": "SMALL",
    "leadtime_to_ship": "2",
    "gtin": "9300000000001",
    "image_urls": "https://example.com/a.jpg|https://example.com/b.jpg",
    "inventory": 5,
    "sale_price": "79.99",
    "original_price": "99.99",
}


def _listing_ns(**overrides):
    data = {**VALID_BUNNINGS, **overrides}
    return SimpleNamespace(
        sku=data["sku"],
        title=data["title"],
        description=data["description"],
        brand=data["brand"],
        category=data["category"],
        barcode=data.get("gtin") or "",
        image_urls=data["image_urls"],
        inventory=data["inventory"],
        infinite_quantity=bool(data.get("infinite_quantity")),
        sale_price=Decimal(str(data["sale_price"])),
        original_price=Decimal(str(data["original_price"])),
        sale_price_cents=7999,
        original_price_cents=9999,
        external_variant_key="",
        external_data_object_json=bunnings_products.build_extras(data),
    )


class BunningsProductsUnitTests(SimpleTestCase):
    def test_validate_requires_core_fields(self):
        errors = bunnings_products.validate_listing({})
        joined = " ".join(errors)
        self.assertIn("SKU", joined)
        self.assertIn("Title", joined)
        self.assertIn("Brand", joined)
        self.assertIn("category", joined.lower())
        self.assertIn("Logistic class", joined)
        self.assertIn("image", joined.lower())
        self.assertIn("Price", joined)

    def test_validate_accepts_complete_row(self):
        self.assertEqual(bunnings_products.validate_listing(VALID_BUNNINGS), [])

    def test_products_csv_uses_semicolon_and_sku(self):
        text = bunnings_products.products_csv([_listing_ns()])
        header = text.splitlines()[0]
        self.assertIn("category;product-id;product-id-type", header)
        self.assertIn("BN-1", text)
        self.assertIn("DRILLS", text)
        self.assertIn("9300000000001", text)
        self.assertIn("https://example.com/a.jpg", text)

    def test_offers_csv_price_qty_and_delete(self):
        text = bunnings_products.offers_csv([_listing_ns()])
        self.assertIn("sku;product-id", text.splitlines()[0])
        self.assertIn("79.99", text)
        self.assertIn("SMALL", text)
        self.assertIn(";update", text)
        deleted = bunnings_products.offers_csv([_listing_ns()], delete=True)
        self.assertIn(";delete", deleted)
        self.assertIn(";0;", deleted.replace("79.99", "PRICE"))

    def test_flatten_hierarchies_paths_and_search(self):
        payload = {
            "hierarchies": [
                {"code": "ROOT", "label": "Home", "parent_code": ""},
                {"code": "TOOLS", "label": "Tools", "parent_code": "ROOT"},
                {"code": "DRILLS", "label": "Drills", "parent_code": "TOOLS"},
            ]
        }
        rows = bunnings_products.flatten_hierarchies(payload)
        by_code = {r["code"]: r for r in rows}
        self.assertEqual(by_code["DRILLS"]["name"], "Home / Tools / Drills")
        self.assertTrue(by_code["DRILLS"]["leaf"])
        self.assertFalse(by_code["ROOT"]["leaf"])
        hits = bunnings_products.flatten_hierarchies(payload, q="drill")
        self.assertEqual([r["code"] for r in hits], ["DRILLS"])

    def test_flatten_logistic_classes(self):
        rows = bunnings_products.flatten_logistic_classes({
            "logistic_classes": [
                {"code": "SMALL", "label": "Small parcel"},
                {"code": "LARGE", "name": "Large"},
            ]
        })
        self.assertEqual(rows[0]["code"], "SMALL")
        self.assertEqual(rows[0]["name"], "Small parcel")

    def test_extract_import_id(self):
        self.assertEqual(extract_import_id({"import_id": 44}), "44")
        self.assertEqual(extract_import_id({"id": "abc"}), "abc")
        self.assertEqual(extract_import_id("nope"), "")


class BunningsOrdersUnitTests(SimpleTestCase):
    def test_map_order_status(self):
        self.assertEqual(bunnings_orders.map_order_status("WAITING_ACCEPTANCE"), OrderStatus.NEW)
        self.assertEqual(bunnings_orders.map_order_status("SHIPPING"), OrderStatus.PAID)
        self.assertEqual(bunnings_orders.map_order_status("SHIPPED"), OrderStatus.SENT)
        self.assertEqual(bunnings_orders.map_order_status("CLOSED"), OrderStatus.SHIPPING_COMPLETE)
        self.assertEqual(bunnings_orders.map_order_status("CANCELED"), OrderStatus.CANCELLED)

    def test_tracking_payload_maps_auspost(self):
        payload = bunnings_orders.build_tracking_payload(
            tracking_number="ABC123",
            carrier="Australia Post",
            tracking_url="https://auspost.com.au/track/ABC123",
        )
        self.assertEqual(payload["tracking_number"], "ABC123")
        self.assertEqual(payload["carrier_name"], "Australia Post")
        self.assertEqual(payload["carrier_code"], "AUSPOST")
        self.assertEqual(payload["carrier_url"], "https://auspost.com.au/track/ABC123")

    def test_to_ui_raw_shape(self):
        ui = bunnings_orders.to_ui_raw_shape({
            "order_id": "ORD-9",
            "order_state": "SHIPPING",
            "total_price": "12.50",
            "currency_iso_code": "AUD",
            "customer": {
                "firstname": "Sam",
                "lastname": "Buyer",
                "email": "sam@example.com",
                "shipping_address": {
                    "street_1": "1 Test St",
                    "city": "Sydney",
                    "state": "NSW",
                    "zip_code": "2000",
                    "country_iso_code": "AU",
                },
            },
            "order_lines": [
                {"offer_sku": "BN-1", "quantity": 2, "price": "6.25", "product_title": "Drill"},
            ],
        })
        self.assertEqual(ui["invoiceNumber"], "ORD-9")
        self.assertEqual(ui["totalCents"], 1250)
        self.assertEqual(ui["lineItems"][0]["sku"], "BN-1")
        self.assertEqual(ui["customer"]["shippingAddress"]["postcode"], "2000")


class BunningsListingServiceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="bn", email="bn@example.com", password="pw")
        bunnings, _ = Marketplace.objects.get_or_create(code="bunnings", defaults={"name": "Bunnings"})
        self.store = Store.objects.create(
            user=self.user,
            name="Bunnings Store",
            region="AU",
            marketplace=bunnings,
            management_mode="full_store",
            bunnings_environment="production",
            bunnings_production_base_url="https://bunnings-prod.mirakl.net",
            bunnings_production_shop_key="test-key",
        )

    def test_template_headers_and_parse(self):
        csv_text = csv_import.build_template_csv("create", store=self.store)
        self.assertIn("Logistic Class", csv_text)
        self.assertIn("Leadtime To Ship (Optional)", csv_text)
        self.assertIn("Category", csv_text)
        self.assertNotIn("Product Key", csv_text)
        rows = csv_import.parse_upload("bunnings.csv", csv_text.encode())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sku"], "BN-EXAMPLE-001")
        self.assertEqual(rows[0]["sale_price"], "79.99")
        self.assertEqual(rows[0]["logistic_class"], "SMALL")
        self.assertEqual(rows[0]["leadtime_to_ship"], "2")

    def test_create_ready_listing_keeps_extras(self):
        listing = listing_service.create(self.user, self.store, dict(VALID_BUNNINGS))
        self.assertEqual(listing.status, ListingStatus.READY)
        self.assertEqual(listing.sku, "BN-1")
        extras = bunnings_products.parse_extras(listing)
        self.assertEqual(extras.get("logistic_class"), "SMALL")
        self.assertEqual(extras.get("gtin"), "9300000000001")
        self.assertEqual(listing.barcode, "9300000000001")

    def test_create_invalid_keeps_extras(self):
        listing = listing_service.create(self.user, self.store, {**VALID_BUNNINGS, "title": ""})
        self.assertEqual(listing.status, ListingStatus.VALIDATION_FAILED)
        extras = bunnings_products.parse_extras(listing)
        self.assertEqual(extras.get("logistic_class"), "SMALL")

    @patch("listings.bunnings.products.BunningsClient")
    def test_publish_sends_product_then_offer(self, mock_cls):
        listing = listing_service.create(self.user, self.store, dict(VALID_BUNNINGS))
        client = mock_cls.return_value
        client.environment = "production"
        client.import_products.return_value = BunningsResult(ok=True, data={"import_id": "p1"})
        client.import_offers.return_value = BunningsResult(ok=True, data={"import_id": "o1"})
        client.poll_import.return_value = BunningsResult(
            ok=True, data={"import_status": "COMPLETE"}, message="COMPLETE"
        )
        result = listing_service.publish(self.user, self.store)
        self.assertTrue(result["ok"])
        self.assertEqual(result["published"], 1)
        listing.refresh_from_db()
        self.assertEqual(listing.status, ListingStatus.UPLOADED_PRODUCTION)
        client.import_products.assert_called_once()
        client.import_offers.assert_called_once()

    @patch("listings.bunnings.products.BunningsClient")
    def test_publish_marks_failed_when_p41_fails(self, mock_cls):
        listing = listing_service.create(self.user, self.store, dict(VALID_BUNNINGS))
        client = mock_cls.return_value
        client.environment = "production"
        client.import_products.return_value = BunningsResult(
            ok=False, data={}, message="P41 rejected", status=400
        )
        result = listing_service.publish(self.user, self.store)
        self.assertFalse(result["ok"])
        listing.refresh_from_db()
        self.assertEqual(listing.status, ListingStatus.FAILED)
        client.import_offers.assert_not_called()

    @patch("listings.bunnings.products.BunningsClient")
    def test_lookup_offer_raises_on_api_error(self, mock_cls):
        client = mock_cls.return_value
        client.list_offers.return_value = BunningsResult(ok=False, message="401", status=401)
        with self.assertRaises(MarketplaceError):
            bunnings_products.lookup_offer(self.store, "BN-1")

    def test_create_test_order_rejected(self):
        with self.assertRaises(MarketplaceError):
            order_service.create_test_order(self.user, self.store)
