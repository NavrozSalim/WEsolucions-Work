"""Unit tests for the listings app (run: python manage.py test listings -v 2).

Covers the Lasoo mapper/validator port, CSV/XLSX bulk import parsing, and the
listing service validation flow (no network calls).
"""
import io
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from marketplace.models import Marketplace
from stores.models import Store

from . import csv_import, listing_service, order_service
from .errors import MarketplaceError
from .lasoo import mapper, validator
from .lasoo.client import LasooResult
from .models import ListingStatus, ListingUpload, MarketplaceOrder, OrderStatus, StoreListing
from .views import _listing_upload_csv_response

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
        self.assertEqual(rows[0]["vendor_name"], "Nora Inventory")
        self.assertEqual(rows[0]["marketplace_name"], "Lasoo")

    def test_lasoo_template_vendor_columns_follow_vendor_name(self):
        header = csv_import.build_template_csv("create").splitlines()[0]
        cols = header.split(",")
        self.assertEqual(cols[0], "Vendor Name")
        self.assertEqual(cols[1], "Vendor URL")
        self.assertEqual(cols[2], "Vendor ID")

    def test_parse_action_instructional_text(self):
        content = (
            "Action,SKU,Title,Original Price,Sale Price,Inventory,Image URLs,Brand,Description\n"
            "Create - This will Create Item On Marketplace,ABC-1,Tee,10,9,1,"
            "https://img.example.com/a.jpg,Brand,Desc\n"
        ).encode()
        rows = csv_import.parse_upload("listings.csv", content)
        self.assertEqual(rows[0]["action"], "create")

    def test_parse_xlsx_with_banner_header(self):
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.append(["System Required Headers", None, None, "Marketplace Required Headers"])
        ws.append(
            [
                "Vendor Name",
                "Marketplace Name",
                "Store Name",
                "Action",
                "SKU",
                "Title",
                "Description",
                "Brand",
                "Image URLs",
                "Inventory",
                "Original Price",
                "Sale Price",
            ]
        )
        ws.append(
            [
                "Amazon AU",
                "Lasoo",
                "KTFS",
                "Mapped - bring old items",
                "ABC-1",
                "Test product",
                "Desc",
                "Brand",
                "https://img.example.com/a.jpg",
                3,
                19.99,
                15.5,
            ]
        )
        buf = io.BytesIO()
        wb.save(buf)
        rows = csv_import.parse_upload("listings.xlsx", buf.getvalue())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sku"], "ABC-1")
        self.assertEqual(rows[0]["action"], "mapped")
        self.assertEqual(rows[0]["vendor_name"], "Amazon AU")
        self.assertEqual(rows[0]["store_name"], "KTFS")

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
        upload = ListingUpload.objects.filter(store=self.store).order_by("-created_at").first()
        self.assertEqual(upload.status, ListingUpload.Status.FAILED)
        self.assertGreater(upload.error_rows, 0)

    def test_upload_history_completed_when_all_ok(self):
        content = csv_import.build_template_csv("create").encode()
        listing_service.bulk_import(self.user, self.store, "ok.csv", content, action="create")
        upload = ListingUpload.objects.get(store=self.store, filename="ok.csv")
        self.assertEqual(upload.status, ListingUpload.Status.COMPLETED)
        self.assertEqual(upload.error_rows, 0)

    def test_upload_error_csv_contains_failed_rows(self):
        upload = ListingUpload.objects.create(
            user=self.user,
            store=self.store,
            filename="bad.csv",
            source=ListingUpload.Source.FILE,
            action="create",
            status=ListingUpload.Status.FAILED,
            total_rows=2,
            success_rows=1,
            error_rows=1,
            rows_json=[
                {"row_number": 2, "sku": "OK-1", "valid": True, "imported": True, "errors": []},
                {"row_number": 3, "sku": "BAD-1", "valid": False, "imported": False, "errors": ["Missing title"]},
            ],
        )
        resp = _listing_upload_csv_response(upload, errors_only=True)
        body = resp.content.decode()
        self.assertIn("BAD-1", body)
        self.assertIn("Missing title", body)
        self.assertNotIn("OK-1", body)
        self.assertIn("Error", body)

    def test_upload_export_shows_uploaded_vs_created(self):
        StoreListing.objects.create(
            user=self.user,
            store=self.store,
            external_product_key="UP-1",
            external_variant_key="UP-1",
            sku="UP-1",
            title="On marketplace",
            description="d",
            brand="b",
            image_urls="https://img.example.com/a.jpg",
            original_price="10",
            sale_price="9",
            status=ListingStatus.UPLOADED_PRODUCTION,
            action="create",
        )
        StoreListing.objects.create(
            user=self.user,
            store=self.store,
            external_product_key="CR-1",
            external_variant_key="CR-1",
            sku="CR-1",
            title="Ready only",
            description="d",
            brand="b",
            image_urls="https://img.example.com/b.jpg",
            original_price="10",
            sale_price="9",
            status=ListingStatus.READY,
            action="create",
        )
        upload = ListingUpload.objects.create(
            user=self.user,
            store=self.store,
            filename="mix.csv",
            source=ListingUpload.Source.FILE,
            action="create",
            status=ListingUpload.Status.COMPLETED,
            total_rows=3,
            success_rows=2,
            error_rows=1,
            rows_json=[
                {"row_number": 2, "sku": "UP-1", "valid": True, "imported": True, "errors": []},
                {"row_number": 3, "sku": "CR-1", "valid": True, "imported": True, "errors": []},
                {
                    "row_number": 4,
                    "sku": "BAD-1",
                    "valid": False,
                    "imported": False,
                    "errors": ['A listing with SKU "NA" already exists. Use the Mapped action to update it.'],
                },
            ],
        )
        resp = _listing_upload_csv_response(upload, errors_only=False)
        body = resp.content.decode()
        self.assertIn("Row,SKU,Status,Error Logs", body)
        self.assertIn("UP-1,Uploaded", body)
        self.assertIn("CR-1,Created", body)
        self.assertIn("BAD-1,Error", body)
        self.assertIn("already exists", body)

    def test_delete_upload_history_only(self):
        content = csv_import.build_template_csv("create").encode()
        listing_service.bulk_import(self.user, self.store, "keep.csv", content, action="create")
        upload = ListingUpload.objects.get(store=self.store, filename="keep.csv")
        result = listing_service.delete_upload(self.user, self.store, upload)
        self.assertTrue(result["ok"])
        self.assertEqual(result["listings_deleted"], 0)
        self.assertFalse(ListingUpload.objects.filter(pk=upload.id).exists())
        self.assertEqual(StoreListing.objects.filter(store=self.store).count(), 1)

    def test_delete_upload_with_system(self):
        content = csv_import.build_template_csv("create").encode()
        listing_service.bulk_import(self.user, self.store, "sys.csv", content, action="create")
        upload = ListingUpload.objects.get(store=self.store, filename="sys.csv")
        result = listing_service.delete_upload(
            self.user, self.store, upload, delete_system=True,
        )
        self.assertEqual(result["listings_deleted"], 1)
        self.assertEqual(StoreListing.objects.filter(store=self.store).count(), 0)
        self.assertFalse(ListingUpload.objects.filter(pk=upload.id).exists())

    @patch("listings.listing_service.LasooClient")
    def test_delete_upload_with_marketplace(self, mock_client_cls):
        content = csv_import.build_template_csv("create").encode()
        listing_service.bulk_import(self.user, self.store, "mp.csv", content, action="create")
        listing = StoreListing.objects.get(store=self.store)
        listing.status = ListingStatus.UPLOADED_STAGING
        listing.save(update_fields=["status"])
        upload = ListingUpload.objects.get(store=self.store, filename="mp.csv")

        mock_client = mock_client_cls.return_value
        mock_client.auth_key = "key"
        mock_client.send.return_value = LasooResult(ok=True, message="ok", data={})

        result = listing_service.delete_upload(
            self.user, self.store, upload,
            delete_system=True, delete_marketplace=True,
        )
        self.assertEqual(result["listings_deleted"], 1)
        self.assertEqual(result["marketplace_deleted"], 1)
        mock_client.send.assert_called_once()
        self.assertEqual(mock_client.send.call_args[0][0], "bulk_delete")
        self.assertEqual(StoreListing.objects.filter(store=self.store).count(), 0)

    def test_bulk_import_mapped_sets_uploaded_status(self):
        content = csv_import.build_template_csv("mapped").encode()
        result = listing_service.bulk_import(self.user, self.store, "m.csv", content, action="mapped")
        self.assertEqual(result["imported"], 1)
        listing = StoreListing.objects.get(store=self.store)
        self.assertEqual(listing.action, "mapped")
        self.assertEqual(listing.status, ListingStatus.UPLOADED_STAGING)
        self.assertEqual(listing.source_vendor_code, "noraau")

    def test_bulk_import_rejects_wrong_marketplace_name(self):
        content = (
            "Action,SKU,Title,Description,Brand,Image URLs,Inventory,Original Price,Sale Price,"
            "Marketplace Name\n"
            "Create,ABC-1,Tee,Desc,Brand,https://img.example.com/a.jpg,1,10,9,Reverb\n"
        ).encode()
        result = listing_service.bulk_import(self.user, self.store, "bad-mp.csv", content, action="create")
        self.assertEqual(result["imported"], 0)
        self.assertTrue(any("Marketplace Name" in e for e in result["rows"][0]["errors"]))

    def test_bulk_import_accepts_lesso_alias_and_store_name(self):
        content = (
            "Action,SKU,Title,Description,Brand,Image URLs,Inventory,Original Price,Sale Price,"
            "Marketplace Name,Store Name,Vendor Name,Vendor ID\n"
            "Create,ABC-2,Tee,Desc,Brand,https://img.example.com/a.jpg,1,10,9,"
            "Lesso,Lasoo Test Store,Nora Inventory,NORA-1\n"
        ).encode()
        result = listing_service.bulk_import(self.user, self.store, "ok-route.csv", content, action="create")
        self.assertEqual(result["imported"], 1)
        listing = StoreListing.objects.get(sku="ABC-2")
        self.assertEqual(listing.source_vendor_code, "noraau")
        self.assertEqual(listing.vendor_id, "NORA-1")

    def test_bulk_import_routes_to_named_store(self):
        other = Store.objects.create(
            user=self.user,
            name="KTFS (Vevor)",
            region="AU",
            api_token="",
            marketplace=self.store.marketplace,
            management_mode="full_store",
            lasoo_environment="staging",
            lasoo_staging_auth_key="test-key",
        )
        content = (
            "Action,SKU,Title,Description,Brand,Image URLs,Inventory,Original Price,Sale Price,"
            "Store Name,Marketplace Name\n"
            "Create,ROUTE-1,Tee,Desc,Brand,https://img.example.com/a.jpg,1,10,9,"
            "KTFS (Vevor),Lasoo\n"
        ).encode()
        result = listing_service.bulk_import(self.user, self.store, "route.csv", content, action="create")
        self.assertEqual(result["imported"], 1)
        self.assertEqual(StoreListing.objects.filter(store=other, sku="ROUTE-1").count(), 1)
        self.assertEqual(StoreListing.objects.filter(store=self.store, sku="ROUTE-1").count(), 0)

    def test_bulk_import_nora_requires_vendor_id(self):
        content = (
            "Action,SKU,Title,Description,Brand,Image URLs,Inventory,Original Price,Sale Price,"
            "Vendor Name\n"
            "Create,ABC-3,Tee,Desc,Brand,https://img.example.com/a.jpg,1,10,9,Nora Inventory\n"
        ).encode()
        result = listing_service.bulk_import(self.user, self.store, "nora.csv", content, action="create")
        self.assertEqual(result["imported"], 0)
        self.assertTrue(any("Vendor ID" in e for e in result["rows"][0]["errors"]))

    def test_delete_template_only_needs_sku(self):
        content = csv_import.build_template_csv("delete").encode()
        self.assertIn("SKU", content.decode())
        rows = csv_import.parse_upload("d.csv", content)
        self.assertEqual(rows[0]["action"], "delete")

    def test_publish_skips_invalid_reverb_listings(self):
        """Reverb stores can publish, but Lasoo-shaped rows fail Reverb validation."""
        reverb, _ = Marketplace.objects.get_or_create(code="reverb", defaults={"name": "Reverb"})
        store2 = Store.objects.create(
            user=self.user, name="Reverb Store", region="USA",
            api_token="tok", marketplace=reverb, management_mode="full_store",
        )
        listing_service.create(self.user, store2, dict(VALID_DATA))
        with self.assertRaises(MarketplaceError):
            listing_service.publish(self.user, store2)

    def test_reverb_template_headers(self):
        reverb, _ = Marketplace.objects.get_or_create(code="reverb", defaults={"name": "Reverb"})
        store2 = Store.objects.create(
            user=self.user, name="Reverb Store 2", region="USA",
            api_token="tok", marketplace=reverb, management_mode="full_store",
        )
        csv_text = csv_import.build_template_csv("create", store=store2)
        cols = csv_text.splitlines()[0].split(",")
        self.assertEqual(cols[0], "Vendor Name")
        self.assertEqual(cols[1], "Vendor URL")
        self.assertIn("Make", csv_text)
        self.assertIn("Category", csv_text)
        self.assertIn("status", csv_text)
        self.assertIn("free_shipping", csv_text)
        self.assertNotIn("Product Key", csv_text)
        self.assertNotIn("Variant Key", csv_text)
        rows = csv_import.parse_upload("reverb.csv", csv_text.encode())
        self.assertEqual(rows[0]["publish_status"], "draft")
        self.assertTrue(rows[0]["free_shipping"])
        self.assertTrue(rows[0].get("category") or rows[0].get("category_uuid"))


class ListingServiceVendorSelectTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="vsel", email="vsel@example.com", password="pw")
        lasoo, _ = Marketplace.objects.get_or_create(code="lasoo", defaults={"name": "Lasoo"})
        self.store = Store.objects.create(
            user=self.user,
            name="Vendor Select Store",
            region="AU",
            api_token="",
            marketplace=lasoo,
            management_mode="full_store",
            lasoo_environment="staging",
            lasoo_staging_auth_key="test-key",
        )
        from vendor.models import Vendor
        from stores.models import StoreVendorPriceSettings

        self.nora, _ = Vendor.objects.get_or_create(
            code="noraau", defaults={"name": "Nora Inventory"},
        )
        StoreVendorPriceSettings.objects.create(
            store=self.store,
            vendor=self.nora,
            purchase_tax_percentage=10,
            marketplace_fees_percentage=15,
        )

    def test_create_with_store_vendor_persists_source_code(self):
        data = {
            **VALID_DATA,
            "source_vendor_code": "noraau",
            "vendor_id": "8FNZ100-DL-G1",
            "vendor_url": "",
        }
        listing = listing_service.create(self.user, self.store, data)
        self.assertEqual(listing.source_vendor_code, "noraau")
        self.assertEqual(listing.vendor_id, "8FNZ100-DL-G1")
        self.assertEqual(listing.status, ListingStatus.READY)

    def test_create_rejects_vendor_not_on_store(self):
        data = {
            **VALID_DATA,
            "source_vendor_code": "amazonau",
            "vendor_url": "https://www.amazon.com.au/dp/B00TEST",
        }
        listing = listing_service.create(self.user, self.store, data)
        self.assertEqual(listing.status, ListingStatus.VALIDATION_FAILED)
        joined = " ".join(listing.validation_errors_json or [])
        self.assertIn("not configured", joined)


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

    def test_normalize_shipping_from_delivery_address(self):
        details = order_service.build_order_details({
            "invoiceNumber": "INV-SHIP",
            "deliveryAddress": {
                "address1": "12 Harbour St",
                "suburb": "Melbourne",
                "state": "VIC",
                "postcode": "3000",
                "country": "AU",
            },
            "customer": {"firstName": "Sam", "lastName": "Lee", "email": "sam@example.com"},
        })
        self.assertEqual(details["shippingAddress"]["city"], "Melbourne")
        self.assertEqual(details["shippingAddress"]["line1"], "12 Harbour St")
        self.assertEqual(details["customer"]["email"], "sam@example.com")

    def test_normalize_shipping_from_flat_prefixed_fields(self):
        details = order_service.build_order_details({
            "shippingLine1": "9 Test Rd",
            "shippingSuburb": "Brisbane",
            "shippingState": "QLD",
            "shippingPostcode": "4000",
            "shippingCountry": "AU",
            "customerName": "Alex Buyer",
        })
        self.assertEqual(details["shippingAddress"]["postcode"], "4000")
        self.assertEqual(details["customer"]["name"], "Alex Buyer")

    def test_enrich_order_line_items_from_store_listing(self):
        User = get_user_model()
        user = User.objects.create_user(username="enr", email="enr@example.com", password="pw")
        lasoo, _ = Marketplace.objects.get_or_create(code="lasoo", defaults={"name": "Lasoo"})
        store = Store.objects.create(
            user=user, name="Enrich", region="AU", api_token="", marketplace=lasoo,
            management_mode="full_store", lasoo_environment="staging",
        )
        StoreListing.objects.create(
            user=user,
            store=store,
            external_product_key="TEE-1",
            external_variant_key="TEE-1",
            title="Black Tee",
            sku="MKT-TEE-1",
            vendor_url="https://vendor.example.com/tee-1",
            environment="staging",
        )
        details = order_service.build_order_details({
            "lineItems": [
                {
                    "title": "Black Tee",
                    "externalVariantKey": "TEE-1",
                    "quantity": 1,
                    "priceCents": 2500,
                }
            ],
        })
        enriched = order_service.enrich_order_line_items(details, store)
        item = enriched["lineItems"][0]
        self.assertEqual(item["marketplaceSku"], "MKT-TEE-1")
        self.assertEqual(item["vendorUrl"], "https://vendor.example.com/tee-1")
        self.assertEqual(item["sku"], "MKT-TEE-1")
        self.assertEqual(enriched["sourceLinks"], ["https://vendor.example.com/tee-1"])

    def test_enrich_order_line_items_match_by_title(self):
        User = get_user_model()
        user = User.objects.create_user(username="enr2", email="enr2@example.com", password="pw")
        lasoo, _ = Marketplace.objects.get_or_create(code="lasoo", defaults={"name": "Lasoo"})
        store = Store.objects.create(
            user=user, name="Enrich2", region="AU", api_token="", marketplace=lasoo,
            management_mode="full_store", lasoo_environment="staging",
        )
        StoreListing.objects.create(
            user=user,
            store=store,
            external_product_key="GOLF-1",
            external_variant_key="GOLF-1",
            title="Golf Storage Garage Organizer",
            sku="GOLF-SKU",
            vendor_url="https://vendor.example.com/golf",
            environment="staging",
        )
        details = order_service.build_order_details({
            "lineItems": [
                {
                    "title": "Golf Storage Garage Organizer",
                    "quantity": 1,
                    "priceCents": 1000,
                }
            ],
        })
        enriched = order_service.enrich_order_line_items(details, store)
        self.assertEqual(enriched["lineItems"][0]["marketplaceSku"], "GOLF-SKU")
        self.assertEqual(enriched["sourceLinks"], ["https://vendor.example.com/golf"])

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

    def _make_cancel_order(self, suffix="1"):
        User = get_user_model()
        user = User.objects.create_user(
            username=f"cancelu{suffix}",
            email=f"c{suffix}@example.com",
            password="pw",
        )
        lasoo, _ = Marketplace.objects.get_or_create(code="lasoo", defaults={"name": "Lasoo"})
        store = Store.objects.create(
            user=user, name=f"Cancel Store {suffix}", region="AU", api_token="", marketplace=lasoo,
            management_mode="full_store", lasoo_environment="staging",
            lasoo_staging_auth_key="test-key",
        )
        order = MarketplaceOrder.objects.create(
            user=user,
            store=store,
            external_order_key=f"100099{suffix}",
            invoice_number=f"100099{suffix}",
            status=OrderStatus.PAID,
            total_amount_cents=5000,
            line_items_json=[
                {
                    "lineItemId": 55,
                    "title": "Thing",
                    "quantity": 2,
                    "priceCents": 2500,
                    "totalCents": 5000,
                }
            ],
            environment="staging",
        )
        return order

    @patch("listings.order_service.LasooClient")
    def test_cancel_reasons_includes_lasoo_pre_dispatch_list(self, mock_client_cls):
        order = self._make_cancel_order("3")
        mock_client = MagicMock()
        mock_client.auth_key = "test-key"
        mock_client.send.return_value = LasooResult(
            ok=True,
            data={
                "success": True,
                "results": {
                    "success": True,
                    "refunds": [{"refundReason": "Warehouse damage"}],
                },
            },
            message="Success.",
            status=200,
        )
        mock_client_cls.return_value = mock_client

        result = order_service.cancel_reasons(order.store)
        labels = [r["label"] for r in result["reasons"]]
        self.assertTrue(result["ok"])
        self.assertEqual(result["marketplace"], "lasoo")
        self.assertIn("Out of stock", labels)
        self.assertIn("Warehouse damage", labels)
        self.assertEqual(labels[-1], "Other")

    @patch("listings.order_service.LasooClient")
    def test_cancel_marks_local_and_reports_marketplace_ok(self, mock_client_cls):
        order = self._make_cancel_order("1")
        mock_client = MagicMock()
        mock_client.auth_key = "test-key"
        mock_client.send.return_value = LasooResult(
            ok=True,
            data={"success": True, "results": {"success": True}},
            message="Success.",
            status=200,
        )
        mock_client_cls.return_value = mock_client

        result = order_service.cancel(order, reason="Out of stock")
        order.refresh_from_db()
        self.assertTrue(result["ok"])
        self.assertTrue(result["marketplace_ok"])
        self.assertEqual(order.status, OrderStatus.CANCELLED)
        mock_client.send.assert_called_once()
        _, payload = mock_client.send.call_args[0]
        self.assertEqual(payload["query"], "Refunds_Create")
        self.assertEqual(payload["data"]["invoiceId"], int(order.external_order_key))
        self.assertEqual(payload["data"]["refundReason"], "Out of stock")
        self.assertEqual(payload["data"]["items"][0]["lineItemId"], 55)

    @patch("listings.order_service.LasooClient")
    def test_cancel_still_marks_local_when_lasoo_fails(self, mock_client_cls):
        order = self._make_cancel_order("2")
        mock_client = MagicMock()
        mock_client.auth_key = "test-key"
        mock_client.send.return_value = LasooResult(
            ok=True,
            data={
                "success": True,
                "results": {
                    "success": False,
                    "message": "Fatal error in Refunds_Create",
                },
            },
            message="Success.",
            status=200,
        )
        mock_client_cls.return_value = mock_client

        result = order_service.cancel(order, reason="Damaged")
        order.refresh_from_db()
        self.assertTrue(result["ok"])
        self.assertFalse(result["marketplace_ok"])
        self.assertEqual(order.status, OrderStatus.CANCELLED)


class ReverbOrderNormalizeTests(TestCase):
    def test_normalize_and_upsert_reverb_order(self):
        from listings.reverb import orders as reverb_orders

        User = get_user_model()
        user = User.objects.create_user(username="rv", email="rv@example.com", password="pw")
        reverb, _ = Marketplace.objects.get_or_create(code="reverb", defaults={"name": "Reverb"})
        store = Store.objects.create(
            user=user, name="Reverb Shop", region="USA",
            api_token="reverb-token-1234567890", marketplace=reverb,
            management_mode="full_store",
        )
        raw = {
            "order_number": "RV-10025",
            "order_bundle_id": "B-10025",
            "product_id": "P-7731",
            "sku": "PEDAL-BLUE-01",
            "title": "Example Effects Pedal",
            "quantity": 1,
            "order_type": "instant",
            "status": "paid",
            "buyer_id": 5012,
            "buyer_name": "Example Buyer",
            "buyer_first_name": "Example",
            "buyer_last_name": "Buyer",
            "created_at": "2026-07-14T18:10:00-05:00",
            "updated_at": "2026-07-14T18:15:00-05:00",
            "paid_at": "2026-07-14T18:15:00-05:00",
            "amount_product_subtotal": {
                "amount": "125.00",
                "amount_cents": 12500,
                "currency": "USD",
            },
            "shipping": {
                "amount": "10.00",
                "amount_cents": 1000,
                "currency": "USD",
            },
            "amount_tax": {
                "amount": "0.00",
                "amount_cents": 0,
                "currency": "USD",
                "display": "FREE",
            },
            "total": {
                "amount": "135.00",
                "amount_cents": 13500,
                "currency": "USD",
            },
            "shipping_method": "shipped",
            "local_pickup": False,
            "shipping_address": {
                "name": "Example Buyer",
                "street_address": "100 Example Street",
                "extended_address": None,
                "locality": "Austin",
                "region": "TX",
                "postal_code": "78701",
                "country_code": "US",
                "complete_shipping_address": True,
            },
            "_links": {
                "self": {"href": "https://api.reverb.com/api/my/orders/selling/RV-10025"},
                "web": {"href": "https://reverb.com/my/selling/orders/RV-10025"},
            },
        }
        order = reverb_orders.upsert_order(user, store, raw)
        self.assertIsNotNone(order)
        self.assertEqual(order.external_order_key, "RV-10025")
        self.assertEqual(order.invoice_number, "RV-10025")
        self.assertEqual(order.status, OrderStatus.PAID)
        self.assertEqual(order.total_amount_cents, 13500)
        self.assertEqual(order.environment, "production")
        self.assertEqual(order.customer_info_json["name"], "Example Buyer")
        self.assertEqual(order.customer_info_json["shippingAddress"]["city"], "Austin")
        self.assertEqual(order.line_items_json[0]["sku"], "PEDAL-BLUE-01")
        self.assertEqual(order.line_items_json[0]["priceCents"], 12500)

        details = order_service.build_order_details(
            order.raw_response_json,
            customer_info=order.customer_info_json,
            line_items=order.line_items_json,
            total_cents=order.total_amount_cents,
        )
        self.assertEqual(details["customer"]["name"], "Example Buyer")
        self.assertEqual(details["shippingAddress"]["city"], "Austin")
        self.assertEqual(details["lineItems"][0]["title"], "Example Effects Pedal")
        self.assertEqual(details["totals"]["totalCents"], 13500)

        # Idempotent upsert by order_number
        raw["status"] = "shipped"
        order2 = reverb_orders.upsert_order(user, store, raw)
        self.assertEqual(order2.id, order.id)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.SENT)
        self.assertEqual(MarketplaceOrder.objects.filter(store=store).count(), 1)

    def test_map_reverb_status_blocks_unpaid(self):
        from listings.reverb import orders as reverb_orders

        self.assertEqual(reverb_orders.map_reverb_status("unpaid"), OrderStatus.NEW)
        self.assertEqual(reverb_orders.map_reverb_status("payment_pending"), OrderStatus.NEW)
        self.assertEqual(reverb_orders.map_reverb_status("pending_review"), OrderStatus.NEW)
        self.assertEqual(reverb_orders.map_reverb_status("blocked"), OrderStatus.NEW)
        self.assertEqual(reverb_orders.map_reverb_status("paid"), OrderStatus.PAID)
        self.assertEqual(reverb_orders.map_reverb_status("cancelled"), OrderStatus.CANCELLED)

    @patch("listings.reverb.orders.get_adapter")
    def test_fetch_reverb_advances_sync_cursor(self, mock_get_adapter):
        from listings.reverb import orders as reverb_orders

        User = get_user_model()
        user = User.objects.create_user(username="rv2", email="rv2@example.com", password="pw")
        reverb, _ = Marketplace.objects.get_or_create(code="reverb", defaults={"name": "Reverb"})
        store = Store.objects.create(
            user=user, name="Reverb Shop 2", region="USA",
            api_token="reverb-token-1234567890", marketplace=reverb,
            management_mode="full_store",
        )
        adapter = MagicMock()
        adapter.iter_orders_selling_all.return_value = iter([
            {
                "order_number": "RV-1",
                "status": "paid",
                "sku": "A",
                "title": "Item A",
                "quantity": 1,
                "total": {"amount_cents": 1000, "currency": "USD"},
                "amount_product_subtotal": {"amount_cents": 1000, "currency": "USD"},
                "buyer_name": "Buyer",
                "shipping_address": {
                    "street_address": "1 St",
                    "locality": "Austin",
                    "region": "TX",
                    "postal_code": "78701",
                    "country_code": "US",
                },
            },
        ])
        mock_get_adapter.return_value = adapter

        result = reverb_orders.fetch(user, store)
        self.assertTrue(result["ok"])
        self.assertEqual(result["fetched"], 1)
        store.refresh_from_db()
        self.assertIsNotNone(store.reverb_last_order_sync_at)
        self.assertEqual(MarketplaceOrder.objects.filter(store=store).count(), 1)

    def test_create_test_order_rejected_for_reverb(self):
        User = get_user_model()
        user = User.objects.create_user(username="rv3", email="rv3@example.com", password="pw")
        reverb, _ = Marketplace.objects.get_or_create(code="reverb", defaults={"name": "Reverb"})
        store = Store.objects.create(
            user=user, name="Reverb Shop 3", region="USA",
            api_token="tok", marketplace=reverb, management_mode="full_store",
        )
        with self.assertRaises(MarketplaceError):
            order_service.create_test_order(user, store)
