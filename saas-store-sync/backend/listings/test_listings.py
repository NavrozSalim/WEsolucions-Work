"""Unit tests for the listings app (run: python manage.py test listings -v 2).

Covers the Lasoo mapper/validator port, CSV/XLSX bulk import parsing, and the
listing service validation flow (no network calls).
"""
import io

from django.contrib.auth import get_user_model
from django.test import TestCase

from marketplace.models import Marketplace
from stores.models import Store

from . import csv_import, listing_service, order_service
from .errors import MarketplaceError
from .lasoo import mapper, validator
from .models import ListingStatus, StoreListing

VALID_DATA = {
    "product_key": "TSHIRT-001",
    "variant_key": "TSHIRT-001-BLACK-M",
    "title": "Black T-Shirt (M)",
    "description": "Soft cotton tee.",
    "brand": "MyBrand",
    "category": "Apparel",
    "sku": "TSHIRT-001-BLACK-M",
    "barcode": "",
    "image_urls": "https://img.example.com/a.jpg|https://img.example.com/b.jpg",
    "inventory": 10,
    "infinite_quantity": False,
    "original_price": "29.99",
    "sale_price": "24.99",
}


class MapperTests(TestCase):
    def test_dollars_to_cents(self):
        self.assertEqual(mapper.dollars_to_cents("29.99"), 2999)
        self.assertEqual(mapper.dollars_to_cents(0), 0)
        with self.assertRaises(ValueError):
            mapper.dollars_to_cents("not-a-price")

    def test_normalize_image_urls_accepts_mixed_separators(self):
        raw = "https://a.jpg, https://b.jpg;https://c.jpg\nhttps://d.jpg"
        self.assertEqual(
            mapper.normalize_image_urls(raw),
            "https://a.jpg|https://b.jpg|https://c.jpg|https://d.jpg",
        )

    def test_keys_fall_back_to_sku(self):
        product_key, variant_key = mapper.resolve_keys({"sku": "ABC-1"})
        self.assertEqual((product_key, variant_key), ("ABC-1", "ABC-1"))

    def test_build_variant_prices_in_cents(self):
        variant = mapper.build_variant(VALID_DATA)
        self.assertEqual(variant["variantOriginalPriceCents"], 2999)
        self.assertEqual(variant["variantSalePriceCents"], 2499)
        self.assertEqual(variant["externalVariantKey"], "TSHIRT-001-BLACK-M")
        self.assertEqual(variant["externalDataFormat"], "JSON")

    def test_bulk_upsert_payload_shape(self):
        payload = mapper.build_bulk_upsert_payload([VALID_DATA], auth_key="secret")
        self.assertEqual(payload["query"], "Variants_BulkUpsert")
        self.assertEqual(payload["auth"], "secret")
        self.assertEqual(len(payload["data"]["variants"]), 1)


class ValidatorTests(TestCase):
    def test_valid_listing_has_no_errors(self):
        self.assertEqual(validator.validate_listing(VALID_DATA), [])

    def test_missing_fields_reported(self):
        errors = validator.validate_listing({"sku": "X1", "original_price": "10", "sale_price": "5"})
        joined = " ".join(errors)
        self.assertIn("Title is required", joined)
        self.assertIn("Image URLs are required", joined)

    def test_sale_price_must_not_exceed_original(self):
        data = {**VALID_DATA, "sale_price": "39.99"}
        errors = validator.validate_listing(data)
        self.assertTrue(any("lower than or equal" in e for e in errors))


class CsvImportTests(TestCase):
    def test_parse_csv_template(self):
        content = csv_import.build_template_csv("create").encode()
        rows = csv_import.parse_upload("listings.csv", content)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sku"], "TSHIRT-001-BLACK-M")
        self.assertEqual(rows[0]["action"], "create")
        self.assertEqual(rows[0]["row_number"], 2)
        self.assertFalse(rows[0]["infinite_quantity"])

    def test_parse_xlsx(self):
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.append(["SKU", "Title", "Original Price", "Sale Price", "Inventory"])
        ws.append(["ABC-1", "Test product", 19.99, 15.5, 3])
        buf = io.BytesIO()
        wb.save(buf)
        rows = csv_import.parse_upload("listings.xlsx", buf.getvalue())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sku"], "ABC-1")
        self.assertEqual(rows[0]["original_price"], "19.99")
        self.assertEqual(rows[0]["inventory"], "3")


class ListingServiceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="tester", email="t@example.com", password="pw")
        lasoo, _ = Marketplace.objects.get_or_create(code="lasoo", defaults={"name": "Lasoo"})
        self.store = Store.objects.create(
            user=self.user,
            name="Lasoo Test Store",
            region="AU",
            api_token="",
            marketplace=lasoo,
            management_mode="full_store",
            lasoo_environment="staging",
            lasoo_staging_auth_key="test-key",
        )

    def test_create_valid_listing_is_ready(self):
        listing = listing_service.create(self.user, self.store, dict(VALID_DATA))
        self.assertEqual(listing.status, ListingStatus.READY)
        self.assertEqual(listing.original_price_cents, 2999)
        self.assertEqual(listing.environment, "staging")

    def test_create_invalid_listing_flags_validation(self):
        data = {**VALID_DATA, "title": "", "image_urls": ""}
        listing = listing_service.create(self.user, self.store, data)
        self.assertEqual(listing.status, ListingStatus.VALIDATION_FAILED)
        self.assertTrue(listing.validation_errors_json)

    def test_duplicate_variant_key_rejected(self):
        listing_service.create(self.user, self.store, dict(VALID_DATA))
        with self.assertRaises(MarketplaceError):
            listing_service.create(self.user, self.store, dict(VALID_DATA))

    def test_update_revalidates(self):
        listing = listing_service.create(self.user, self.store, dict(VALID_DATA))
        listing = listing_service.update(listing, {**VALID_DATA, "sale_price": "99.99"})
        self.assertEqual(listing.status, ListingStatus.VALIDATION_FAILED)

    def test_bulk_import_create_action(self):
        content = csv_import.build_template_csv("create").encode()
        result = listing_service.bulk_import(self.user, self.store, "l.csv", content, action="create")
        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["action"], "create")
        listing = StoreListing.objects.get(store=self.store)
        self.assertEqual(listing.status, ListingStatus.READY)
        self.assertEqual(listing.action, "create")

    def test_bulk_import_rejects_duplicate_on_create(self):
        content = csv_import.build_template_csv("create").encode()
        listing_service.bulk_import(self.user, self.store, "l.csv", content, action="create")
        result2 = listing_service.bulk_import(self.user, self.store, "l.csv", content, action="create")
        self.assertEqual(result2["imported"], 0)
        self.assertEqual(StoreListing.objects.filter(store=self.store).count(), 1)

    def test_bulk_import_mapped_sets_uploaded_status(self):
        content = csv_import.build_template_csv("mapped").encode()
        result = listing_service.bulk_import(self.user, self.store, "m.csv", content, action="mapped")
        self.assertEqual(result["imported"], 1)
        listing = StoreListing.objects.get(store=self.store)
        self.assertEqual(listing.action, "mapped")
        self.assertEqual(listing.status, ListingStatus.UPLOADED_STAGING)

    def test_delete_template_only_needs_sku(self):
        content = csv_import.build_template_csv("delete").encode()
        self.assertIn("SKU", content.decode())
        rows = csv_import.parse_upload("d.csv", content)
        self.assertEqual(rows[0]["action"], "delete")

    def test_publish_requires_lasoo_marketplace(self):
        reverb, _ = Marketplace.objects.get_or_create(code="reverb", defaults={"name": "Reverb"})
        store2 = Store.objects.create(
            user=self.user, name="Reverb Store", region="USA",
            api_token="tok", marketplace=reverb, management_mode="full_store",
        )
        listing_service.create(self.user, store2, dict(VALID_DATA))
        with self.assertRaises(MarketplaceError):
            listing_service.publish(self.user, store2)


class OrderNormalizeTests(TestCase):
    def test_build_order_details_from_lasoo_shape(self):
        raw = {
            "id": 99,
            "invoiceNumber": "INV-99",
            "status": "paid",
            "createdAt": "2026-07-01T10:00:00Z",
            "totalCents": 5499,
            "subtotalCents": 5000,
            "shippingCents": 499,
            "taxCents": 0,
            "customer": {
                "firstName": "Jane",
                "lastName": "Doe",
                "email": "jane@example.com",
                "phone": "0400000000",
                "shippingAddress": {
                    "line1": "1 Test St",
                    "city": "Sydney",
                    "state": "NSW",
                    "postcode": "2000",
                    "country": "AU",
                },
            },
            "lineItems": [
                {
                    "title": "Black Tee",
                    "sku": "TEE-1",
                    "externalVariantKey": "TEE-1",
                    "quantity": 2,
                    "priceCents": 2500,
                }
            ],
            "shipping": {"status": "pending", "method": "Standard"},
        }
        details = order_service.build_order_details(raw)
        self.assertEqual(details["customer"]["name"], "Jane Doe")
        self.assertEqual(details["customer"]["email"], "jane@example.com")
        self.assertEqual(details["shippingAddress"]["city"], "Sydney")
        self.assertEqual(details["lineItems"][0]["title"], "Black Tee")
        self.assertEqual(details["lineItems"][0]["quantity"], 2)
        self.assertEqual(details["totals"]["totalCents"], 5499)
        self.assertEqual(details["totals"]["shippingCents"], 499)
        self.assertEqual(details["shipping"]["method"], "Standard")
        self.assertEqual(details["dates"]["orderedAt"], "2026-07-01T10:00:00Z")

    def test_upsert_persists_normalized_fields(self):
        User = get_user_model()
        user = User.objects.create_user(username="ord", email="o@example.com", password="pw")
        lasoo, _ = Marketplace.objects.get_or_create(code="lasoo", defaults={"name": "Lasoo"})
        store = Store.objects.create(
            user=user, name="O", region="AU", api_token="", marketplace=lasoo,
            management_mode="full_store", lasoo_environment="staging",
        )
        raw = {
            "id": "42",
            "invoiceNumber": "INV-42",
            "status": "Paid",
            "totalCents": 1000,
            "customer": {"firstName": "A", "lastName": "B", "email": "a@b.com"},
            "lineItems": [{"title": "Item", "sku": "S1", "quantity": 1, "priceCents": 1000}],
        }
        order = order_service._upsert_order(user, store, "staging", raw)
        self.assertEqual(order.invoice_number, "INV-42")
        self.assertEqual(order.customer_info_json["email"], "a@b.com")
        self.assertEqual(order.line_items_json[0]["title"], "Item")
        self.assertEqual(order.total_amount_cents, 1000)
        self.assertEqual(order.status, "paid")
        self.assertIsNotNone(order.raw_response_json)
