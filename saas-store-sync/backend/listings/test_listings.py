"""Unit tests for the listings app (run: python manage.py test listings -v 2).

Covers the Lasoo mapper/validator port, CSV/XLSX bulk import parsing, and the
listing service validation flow (no network calls).
"""
import io
import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from marketplace.models import Marketplace
from stores.models import Store

from . import csv_import, listing_service, order_service
from .errors import MarketplaceError
from .lasoo import mapper, validator
from .lasoo.client import LasooResult
from .models import ListingAction, ListingStatus, ListingUpload, MarketplaceOrder, OrderStatus, StoreListing
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
    "option_1_name": "Colour",
    "option_1_value": "Black",
    "option_2_name": "Size",
    "option_2_value": "M",
    "option_3_name": "",
    "option_3_value": "",
    "option_4_name": "",
    "option_4_value": "",
    "variation_image_url": "https://img.example.com/tshirt-black-m.jpg",
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

    def test_keys_treat_na_variant_as_blank(self):
        product_key, variant_key = mapper.resolve_keys({
            "sku": "JJ-XH1899BK-FCBY",
            "product_key": "JJ-XH1899BK-FCBY",
            "variant_key": "N/A",
        })
        self.assertEqual(product_key, "JJ-XH1899BK-FCBY")
        self.assertEqual(variant_key, "JJ-XH1899BK-FCBY")
        for placeholder in ("NA", "n/a", "None", "null", "-", ""):
            _, vk = mapper.resolve_keys({"sku": "SKU-1", "variant_key": placeholder})
            self.assertEqual(vk, "SKU-1", msg=f"placeholder={placeholder!r}")

    def test_build_variant_prices_in_cents(self):
        variant = mapper.build_variant(VALID_DATA)
        self.assertEqual(variant["variantOriginalPriceCents"], 2999)
        self.assertEqual(variant["variantSalePriceCents"], 2499)
        self.assertEqual(variant["externalVariantKey"], "TSHIRT-001-BLACK-M")
        self.assertEqual(variant["externalDataFormat"], "JSON")
        payload = json.loads(variant["externalDataObject"])
        self.assertEqual(payload.get("Option 1 Name"), "Colour")
        self.assertEqual(payload.get("Option 1 Value"), "Black")
        self.assertEqual(payload.get("Option 2 Name"), "Size")
        self.assertEqual(payload.get("Option 2 Value"), "M")
        self.assertEqual(payload.get("Options"), "Colour=Black; Size=M")
        self.assertEqual(payload.get("Variation Img URL"), "https://img.example.com/tshirt-black-m.jpg")
        self.assertEqual(payload.get("SKU"), "TSHIRT-001-BLACK-M")
        self.assertEqual(payload.get("Image URLS"), VALID_DATA["image_urls"])
        self.assertEqual(payload.get("Image URLs"), VALID_DATA["image_urls"])
        self.assertEqual(payload.get("images"), VALID_DATA["image_urls"])

    def test_options_required_when_product_and_variant_differ(self):
        data = {
            **VALID_DATA,
            "option_1_name": "",
            "option_1_value": "",
            "option_2_name": "",
            "option_2_value": "",
            "variation_image_url": "",
        }
        errors = validator.validate_listing(data)
        joined = " ".join(errors)
        self.assertIn("Option 1 Name and Option 1 Value are required", joined)
        self.assertIn("Variation Img URL is required", joined)

    def test_options_optional_for_single_sku_product(self):
        data = {
            **VALID_DATA,
            "product_key": "SOLO-1",
            "variant_key": "SOLO-1",
            "sku": "SOLO-1",
            "option_1_name": "",
            "option_1_value": "",
            "option_2_name": "",
            "option_2_value": "",
            "variation_image_url": "",
        }
        self.assertEqual(validator.validate_listing(data), [])

    def test_bulk_upsert_payload_shape(self):
        payload = mapper.build_bulk_upsert_payload([VALID_DATA], auth_key="secret")
        self.assertEqual(payload["query"], "Variants_BulkUpsert")
        self.assertEqual(payload["auth"], "secret")
        self.assertEqual(len(payload["data"]["variants"]), 1)

    def test_bulk_delete_payload_includes_product_and_variant_keys(self):
        payload = mapper.build_bulk_delete_payload(
            ["HW-ZZ122-G2"],
            auth_key="secret",
            product_keys=["HW-ZZ122-G2"],
        )
        self.assertEqual(payload["query"], "Variants_BulkDelete")
        row = payload["data"]["variants"][0]
        self.assertEqual(row["externalProductKey"], "HW-ZZ122-G2")
        self.assertEqual(row["externalVariantKey"], "HW-ZZ122-G2")
        self.assertEqual(row["sku"], "HW-ZZ122-G2")
        self.assertEqual(payload["results"], {"name": "results"})

    def test_bulk_delete_payload_shapes_start_with_search_keys(self):
        shapes = mapper.iter_bulk_delete_payloads(
            ["HW-ZZ122-G2"],
            auth_key="secret",
            product_keys=["HW-ZZ122-G2"],
        )
        names = [name for name, _payload in shapes]
        self.assertEqual(names[0], "search_keys")
        self.assertIn("variant_objects", names)
        first = shapes[0][1]["data"]
        self.assertEqual(first["externalProductKey"], "HW-ZZ122-G2")
        self.assertEqual(first["externalVariantKey"], "HW-ZZ122-G2")
        self.assertNotIn("variants", first)


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
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["sku"], "JDXTY-XL-B")
        self.assertEqual(rows[0]["product_key"], "JDXTY")
        self.assertEqual(rows[0]["variant_key"], "JDXTY-XL-B")
        self.assertEqual(rows[0]["option_1_name"], "Size")
        self.assertEqual(rows[0]["option_1_value"], "XL")
        self.assertEqual(rows[0]["option_2_name"], "Color")
        self.assertEqual(rows[0]["option_2_value"], "Blue")
        self.assertEqual(rows[0]["variation_image_url"], "https://img.example.com/jdxty-xl-blue.jpg")
        self.assertEqual(rows[1]["sku"], "JDXTY-S-R")
        self.assertEqual(rows[1]["option_1_value"], "S")
        self.assertEqual(rows[1]["option_2_value"], "Red")
        self.assertEqual(rows[0]["action"], "create")
        self.assertEqual(rows[0]["row_number"], 2)
        self.assertFalse(rows[0]["infinite_quantity"])
        self.assertEqual(rows[0]["vendor_name"], "Nora Inventory")
        self.assertEqual(rows[0]["marketplace_name"], "Lasoo")

    def test_lasoo_template_includes_options_column(self):
        header = csv_import.build_template_csv("create").splitlines()[0]
        cols = header.split(",")
        self.assertIn("Option 1 Name (Optional)", cols)
        self.assertIn("Option 1 Value (Optional)", cols)
        self.assertIn("Variation Img URL (Optional)", cols)
        self.assertIn("Product Key", cols)
        self.assertIn("Variant Key", cols)
        self.assertIn("SKU", cols)

    def test_lasoo_template_vendor_columns_follow_vendor_name(self):
        header = csv_import.build_template_csv("create").splitlines()[0]
        cols = header.split(",")
        self.assertEqual(cols[0], "Vendor Name (Optional)")
        self.assertEqual(cols[1], "Vendor URL (Optional)")
        self.assertEqual(cols[2], "Vendor ID (Optional)")

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

    def test_update_keeps_uploaded_status(self):
        listing = listing_service.create(self.user, self.store, dict(VALID_DATA))
        listing.status = ListingStatus.UPLOADED_STAGING
        listing.last_uploaded_at = timezone.now()
        listing.save(update_fields=["status", "last_uploaded_at"])
        with patch("listings.listing_service.LasooClient") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.auth_key = "key"

            def send(endpoint, payload=None, *args, **kwargs):
                if endpoint == "variants_search":
                    return LasooResult(
                        ok=True,
                        message="ok",
                        data={
                            "results": {
                                "variants": [{
                                    "externalProductKey": listing.external_product_key,
                                    "externalVariantKey": listing.external_variant_key,
                                }],
                            },
                        },
                    )
                return LasooResult(ok=True, message="ok", data={"success": True})

            mock_client.send.side_effect = send
            listing = listing_service.update(listing, {**VALID_DATA, "title": "Updated tee"})
        listing.refresh_from_db()
        self.assertEqual(listing.status, ListingStatus.UPLOADED_STAGING)
        self.assertEqual(listing.title, "Updated tee")

    @patch("listings.listing_service.LasooClient")
    def test_update_pushes_uploaded_listing_to_lasoo(self, mock_client_cls):
        listing = listing_service.create(self.user, self.store, dict(VALID_DATA))
        listing.status = ListingStatus.UPLOADED_STAGING
        listing.last_uploaded_at = timezone.now()
        listing.save(update_fields=["status", "last_uploaded_at"])

        mock_client = mock_client_cls.return_value
        mock_client.auth_key = "key"

        def send(endpoint, payload=None, *args, **kwargs):
            if endpoint == "variants_search":
                return LasooResult(
                    ok=True,
                    message="ok",
                    data={
                        "results": {
                            "variants": [{
                                "externalProductKey": listing.external_product_key,
                                "externalVariantKey": listing.external_variant_key,
                            }],
                        },
                    },
                )
            return LasooResult(ok=True, message="ok", data={"success": True})

        mock_client.send.side_effect = send

        listing_service.update(listing, {**VALID_DATA, "title": "Pushed title"})
        endpoints = [c[0][0] for c in mock_client.send.call_args_list]
        self.assertIn("variants_search", endpoints)
        self.assertIn("bulk_upsert", endpoints)
        upsert = next(c for c in mock_client.send.call_args_list if c[0][0] == "bulk_upsert")
        payload = upsert[0][1]
        variants = (payload.get("data") or payload).get("variants") or []
        self.assertEqual(len(variants), 1)
        body = json.loads(variants[0]["externalDataObject"])
        self.assertEqual(body.get("productName"), "Pushed title")

    @patch("listings.listing_service.LasooClient")
    def test_update_raises_when_lasoo_mapping_fails(self, mock_client_cls):
        listing = listing_service.create(self.user, self.store, dict(VALID_DATA))
        listing.status = ListingStatus.UPLOADED_STAGING
        listing.last_uploaded_at = timezone.now()
        listing.save(update_fields=["status", "last_uploaded_at"])

        mock_client = mock_client_cls.return_value
        mock_client.auth_key = "key"

        def send(endpoint, payload=None, *args, **kwargs):
            if endpoint == "variants_search":
                return LasooResult(
                    ok=True,
                    message="ok",
                    data={
                        "results": {
                            "variants": [{
                                "externalProductKey": listing.external_product_key,
                                "externalVariantKey": listing.external_variant_key,
                            }],
                        },
                    },
                )
            return LasooResult(
                ok=True,
                message="ok",
                data={
                    "success": True,
                    "results": {
                        "dataMappingErrors": [
                            {"externalVariantKey": listing.sku, "errors": ["Category could not be mapped"]},
                        ],
                    },
                },
            )

        mock_client.send.side_effect = send
        with self.assertRaises(MarketplaceError) as ctx:
            listing_service.update(listing, {**VALID_DATA, "title": "Broken map"})
        self.assertIn("mapping", str(ctx.exception).lower())
        listing.refresh_from_db()
        self.assertEqual(listing.status, ListingStatus.FAILED)
        self.assertEqual(listing.title, "Broken map")

    def test_update_uploaded_sale_above_original_still_pushes(self):
        listing = listing_service.create(self.user, self.store, dict(VALID_DATA))
        listing.status = ListingStatus.UPLOADED_STAGING
        listing.last_uploaded_at = timezone.now()
        listing.save(update_fields=["status", "last_uploaded_at"])
        with patch("listings.listing_service.LasooClient") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.auth_key = "key"

            def send(endpoint, payload=None, *args, **kwargs):
                if endpoint == "variants_search":
                    return LasooResult(
                        ok=True,
                        message="ok",
                        data={
                            "results": {
                                "variants": [{
                                    "externalProductKey": listing.external_product_key,
                                    "externalVariantKey": listing.external_variant_key,
                                }],
                            },
                        },
                    )
                return LasooResult(ok=True, message="ok", data={"success": True})

            mock_client.send.side_effect = send
            listing = listing_service.update(
                listing, {**VALID_DATA, "original_price": "10.00", "sale_price": "125.18"},
            )
        listing.refresh_from_db()
        self.assertEqual(listing.status, ListingStatus.UPLOADED_STAGING)
        self.assertEqual(listing.sale_price, Decimal("125.18"))
        self.assertEqual(listing.original_price, Decimal("125.18"))

    def test_update_uploaded_validation_error_does_not_drop_status(self):
        listing = listing_service.create(self.user, self.store, dict(VALID_DATA))
        listing.status = ListingStatus.UPLOADED_STAGING
        listing.last_uploaded_at = timezone.now()
        listing.save(update_fields=["status", "last_uploaded_at"])
        with self.assertRaises(MarketplaceError) as ctx:
            listing_service.update(listing, {**VALID_DATA, "title": ""})
        self.assertIn("Title is required", str(ctx.exception))
        listing.refresh_from_db()
        self.assertEqual(listing.status, ListingStatus.UPLOADED_STAGING)
        self.assertEqual(listing.title, VALID_DATA["title"])

    @patch("listings.listing_service.LasooClient")
    def test_delete_listing_sends_both_keys(self, mock_client_cls):
        listing = listing_service.create(self.user, self.store, dict(VALID_DATA))
        listing.status = ListingStatus.UPLOADED_STAGING
        listing.save(update_fields=["status"])
        mock_client = mock_client_cls.return_value
        mock_client.auth_key = "key"
        catalog = {listing.external_variant_key}

        def send(endpoint, payload=None, *args, **kwargs):
            data = (payload or {}).get("data") or {}
            if endpoint == "variants_search":
                key = data.get("externalVariantKey")
                found = key in catalog
                variants = (
                    [{"externalProductKey": key, "externalVariantKey": key}]
                    if found else []
                )
                return LasooResult(
                    ok=True, message="ok",
                    data={"results": {"variants": variants, "success": True}},
                )
            if endpoint == "bulk_delete":
                catalog.clear()
                return LasooResult(ok=True, message="ok", data={"success": True})
            return LasooResult(ok=True, message="ok", data={})

        mock_client.send.side_effect = send
        vk = listing.external_variant_key
        pk = listing.external_product_key
        listing_service.delete(self.user, self.store, listing)
        self.assertEqual(StoreListing.objects.filter(id=listing.id).count(), 0)
        delete_calls = [
            call for call in mock_client.send.call_args_list if call[0][0] == "bulk_delete"
        ]
        self.assertTrue(delete_calls)
        payload = delete_calls[0][0][1]
        self.assertEqual(payload["query"], "Variants_BulkDelete")
        data = payload["data"]
        self.assertEqual(data.get("externalVariantKey") or data["variants"][0]["externalVariantKey"], vk)
        self.assertEqual(data.get("externalProductKey") or data["variants"][0]["externalProductKey"], pk)

    @patch("listings.listing_service.LasooClient")
    def test_delete_listing_keeps_row_when_connect_still_has_sku(self, mock_client_cls):
        listing = listing_service.create(self.user, self.store, dict(VALID_DATA))
        listing.status = ListingStatus.UPLOADED_STAGING
        listing.save(update_fields=["status"])
        mock_client = mock_client_cls.return_value
        mock_client.auth_key = "key"
        key = listing.external_variant_key

        def send(endpoint, payload=None, *args, **kwargs):
            if endpoint == "variants_search":
                return LasooResult(
                    ok=True, message="ok",
                    data={"results": {"variants": [{
                        "externalProductKey": key,
                        "externalVariantKey": key,
                    }], "success": True}},
                )
            return LasooResult(
                ok=False,
                message="Cannot read properties of undefined (reading 'map')",
                data={},
            )

        mock_client.send.side_effect = send
        with self.assertRaises(MarketplaceError) as ctx:
            listing_service.delete(self.user, self.store, listing)
        self.assertIn("still has SKU", str(ctx.exception))
        self.assertEqual(StoreListing.objects.filter(id=listing.id).count(), 1)

    @patch("listings.listing_service.LasooClient")
    def test_delete_listing_skips_bulk_delete_when_not_on_connect(self, mock_client_cls):
        listing = listing_service.create(self.user, self.store, dict(VALID_DATA))
        listing.status = ListingStatus.UPLOADED_STAGING
        listing.save(update_fields=["status"])
        mock_client = mock_client_cls.return_value
        mock_client.auth_key = "key"
        mock_client.send.return_value = LasooResult(
            ok=True, message="ok",
            data={"results": {"variants": [], "success": True, "count": 0}},
        )
        listing_id = listing.id
        listing_service.delete(self.user, self.store, listing)
        self.assertEqual(StoreListing.objects.filter(id=listing_id).count(), 0)
        self.assertTrue(all(call[0][0] == "variants_search" for call in mock_client.send.call_args_list))

    def test_update_ready_listing_does_not_push(self):
        listing = listing_service.create(self.user, self.store, dict(VALID_DATA))
        self.assertEqual(listing.status, ListingStatus.READY)
        with patch("listings.listing_service.LasooClient") as mock_client_cls:
            listing_service.update(listing, {**VALID_DATA, "title": "Still local"})
            mock_client_cls.assert_not_called()
        listing.refresh_from_db()
        self.assertEqual(listing.status, ListingStatus.READY)
        self.assertEqual(listing.title, "Still local")

    @patch("listings.listing_service.LasooClient")
    def test_update_does_not_upsert_when_sku_missing_on_lasoo(self, mock_client_cls):
        listing = listing_service.create(self.user, self.store, dict(VALID_DATA))
        listing.action = ListingAction.MAPPED
        listing.status = ListingStatus.UPLOADED_STAGING
        listing.last_uploaded_at = timezone.now()
        listing.save(update_fields=["action", "status", "last_uploaded_at"])

        mock_client = mock_client_cls.return_value
        mock_client.auth_key = "key"
        mock_client.send.return_value = LasooResult(
            ok=True,
            message="ok",
            data={"results": {"success": True, "variants": [], "count": 0, "total": 0}},
        )
        with self.assertRaises(MarketplaceError) as ctx:
            listing_service.update(listing, {**VALID_DATA, "title": "No FIN"})
        self.assertIn("not found", str(ctx.exception).lower())
        endpoints = [c[0][0] for c in mock_client.send.call_args_list]
        self.assertEqual(endpoints, ["variants_search"])
        listing.refresh_from_db()
        self.assertEqual(listing.status, ListingStatus.VALIDATION_FAILED)
        self.assertEqual(listing.title, "No FIN")

    def test_bulk_import_create_action(self):
        content = csv_import.build_template_csv("create").encode()
        result = listing_service.bulk_import(self.user, self.store, "l.csv", content, action="create")
        self.assertEqual(result["imported"], 2)
        self.assertEqual(result["action"], "create")
        self.assertEqual(StoreListing.objects.filter(store=self.store).count(), 2)
        listing = StoreListing.objects.get(store=self.store, sku="JDXTY-XL-B")
        self.assertEqual(listing.status, ListingStatus.READY)
        self.assertEqual(listing.action, "create")
        self.assertEqual(listing.option_1_name, "Size")
        self.assertEqual(listing.option_1_value, "XL")
        self.assertEqual(listing.option_2_value, "Blue")
        self.assertTrue(listing.variation_image_url)

    def test_bulk_import_rejects_duplicate_on_create(self):
        content = csv_import.build_template_csv("create").encode()
        listing_service.bulk_import(self.user, self.store, "l.csv", content, action="create")
        result2 = listing_service.bulk_import(self.user, self.store, "l.csv", content, action="create")
        self.assertEqual(result2["imported"], 0)
        self.assertEqual(StoreListing.objects.filter(store=self.store).count(), 2)
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

    def test_upload_export_status_created_mapped_error(self):
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
        StoreListing.objects.create(
            user=self.user,
            store=self.store,
            external_product_key="MP-1",
            external_variant_key="MP-1",
            sku="MP-1",
            title="Mapped row",
            description="d",
            brand="b",
            image_urls="https://img.example.com/a.jpg",
            original_price="10",
            sale_price="9",
            status=ListingStatus.UPLOADED_PRODUCTION,
            action="mapped",
        )
        create_upload = ListingUpload.objects.create(
            user=self.user,
            store=self.store,
            filename="create.csv",
            source=ListingUpload.Source.FILE,
            action="create",
            status=ListingUpload.Status.COMPLETED,
            total_rows=2,
            success_rows=1,
            error_rows=1,
            rows_json=[
                {
                    "row_number": 2,
                    "sku": "CR-1",
                    "valid": True,
                    "imported": True,
                    "errors": [],
                    "fields": {
                        "vendor_name": "Nora Inventory",
                        "vendor_url": "",
                        "vendor_id": "VID-1",
                        "marketplace_name": "Lasoo",
                        "store_name": "Lasoo Test Store",
                        "action": "Create",
                        "product_key": "CR-1",
                        "variant_key": "CR-1",
                        "title": "Ready only",
                        "description": "d",
                        "brand": "b",
                        "category": "",
                        "sku": "CR-1",
                        "barcode": "",
                        "image_urls": "https://img.example.com/b.jpg",
                        "inventory": "1",
                        "infinite_quantity": "false",
                        "original_price": "10",
                        "sale_price": "9",
                    },
                },
                {
                    "row_number": 3,
                    "sku": "BAD-1",
                    "valid": False,
                    "imported": False,
                    "errors": ['A listing with SKU "NA" already exists. Use the Mapped action to update it.'],
                    "fields": {
                        "sku": "BAD-1",
                        "title": "Bad",
                        "action": "Create",
                        "product_key": "BAD-1",
                        "variant_key": "BAD-1",
                        "description": "d",
                        "brand": "b",
                        "image_urls": "https://img.example.com/c.jpg",
                        "inventory": "1",
                        "infinite_quantity": "false",
                        "original_price": "10",
                        "sale_price": "9",
                    },
                },
            ],
        )
        mapped_upload = ListingUpload.objects.create(
            user=self.user,
            store=self.store,
            filename="mapped.csv",
            source=ListingUpload.Source.FILE,
            action="mapped",
            status=ListingUpload.Status.COMPLETED,
            total_rows=1,
            success_rows=1,
            error_rows=0,
            rows_json=[
                {
                    "row_number": 2,
                    "sku": "MP-1",
                    "valid": True,
                    "imported": True,
                    "errors": [],
                    "fields": {
                        "sku": "MP-1",
                        "title": "Mapped row",
                        "action": "Mapped",
                        "product_key": "MP-1",
                        "variant_key": "MP-1",
                        "description": "d",
                        "brand": "b",
                        "image_urls": "https://img.example.com/a.jpg",
                        "inventory": "1",
                        "infinite_quantity": "false",
                        "original_price": "10",
                        "sale_price": "9",
                    },
                },
            ],
        )
        create_body = _listing_upload_csv_response(create_upload, errors_only=False).content.decode()
        self.assertIn("Vendor Name", create_body)
        self.assertIn(",Status", create_body)
        self.assertIn(",Created", create_body)
        self.assertIn("Error: A listing with SKU", create_body)
        self.assertNotIn("Uploaded on the marketplace", create_body)
        mapped_body = _listing_upload_csv_response(mapped_upload, errors_only=False).content.decode()
        self.assertIn(",Mapped", mapped_body)

    def test_upload_history_scope_excludes_publish_edit_push(self):
        ListingUpload.objects.create(
            user=self.user, store=self.store, filename="bulk.csv",
            source=ListingUpload.Source.FILE, action="create",
            status=ListingUpload.Status.COMPLETED, total_rows=1, success_rows=1,
        )
        ListingUpload.objects.create(
            user=self.user, store=self.store, filename="Single listing",
            source=ListingUpload.Source.SINGLE, action="create",
            status=ListingUpload.Status.COMPLETED, total_rows=1, success_rows=1,
        )
        ListingUpload.objects.create(
            user=self.user, store=self.store, filename="Delete SKU-1",
            source=ListingUpload.Source.SINGLE, action="delete",
            status=ListingUpload.Status.COMPLETED, total_rows=1, success_rows=1,
        )
        ListingUpload.objects.create(
            user=self.user, store=self.store, filename="Edit SKU-1",
            source=ListingUpload.Source.SINGLE, action="create",
            status=ListingUpload.Status.COMPLETED, total_rows=1, success_rows=1,
        )
        ListingUpload.objects.create(
            user=self.user, store=self.store, filename="Publish to marketplace",
            source=ListingUpload.Source.SINGLE, action="create",
            status=ListingUpload.Status.COMPLETED, total_rows=1, success_rows=1,
        )
        ListingUpload.objects.create(
            user=self.user, store=self.store, filename="Push inventory to marketplace",
            source=ListingUpload.Source.SINGLE, action="create",
            status=ListingUpload.Status.COMPLETED, total_rows=1, success_rows=1,
        )
        hist = listing_service.filter_listing_uploads(
            ListingUpload.objects.filter(store=self.store), scope="history",
        )
        logs = listing_service.filter_listing_uploads(
            ListingUpload.objects.filter(store=self.store), scope="logs",
        )
        hist_names = set(hist.values_list("filename", flat=True))
        log_names = set(logs.values_list("filename", flat=True))
        self.assertEqual(hist_names, {"bulk.csv", "Single listing", "Delete SKU-1"})
        self.assertEqual(
            log_names,
            {"Edit SKU-1", "Publish to marketplace", "Push inventory to marketplace"},
        )

    def test_bulk_import_stores_fields_for_export(self):
        content = csv_import.build_template_csv("create", store=self.store).encode()
        listing_service.bulk_import(self.user, self.store, "full.csv", content, action="create")
        upload = ListingUpload.objects.get(store=self.store, filename="full.csv")
        row = upload.rows_json[0]
        self.assertIn("fields", row)
        self.assertEqual(row["fields"].get("sku"), "JDXTY-XL-B")
        self.assertEqual(row["fields"].get("option_1_name"), "Size")
        self.assertEqual(row["fields"].get("option_1_value"), "XL")
        self.assertTrue(row["fields"].get("variation_image_url"))
        self.assertTrue(row["fields"].get("title"))
        resp = _listing_upload_csv_response(upload, errors_only=False)
        body = resp.content.decode()
        self.assertIn("Vendor Name", body)
        self.assertIn("JDXTY-XL-B", body)
        self.assertIn("Option 1 Name", body)
        self.assertIn("Created", body)

    def test_delete_upload_history_only(self):
        content = csv_import.build_template_csv("create").encode()
        listing_service.bulk_import(self.user, self.store, "keep.csv", content, action="create")
        upload = ListingUpload.objects.get(store=self.store, filename="keep.csv")
        result = listing_service.delete_upload(self.user, self.store, upload)
        self.assertTrue(result["ok"])
        self.assertEqual(result["listings_deleted"], 0)
        self.assertFalse(ListingUpload.objects.filter(pk=upload.id).exists())
        self.assertEqual(StoreListing.objects.filter(store=self.store).count(), 2)

    @patch("listings.listing_service.LasooClient")
    def test_delete_upload_with_system(self, mock_client_cls):
        content = csv_import.build_template_csv("create").encode()
        listing_service.bulk_import(self.user, self.store, "sys.csv", content, action="create")
        upload = ListingUpload.objects.get(store=self.store, filename="sys.csv")
        mock_client = mock_client_cls.return_value
        mock_client.auth_key = "key"
        mock_client.send.return_value = LasooResult(
            ok=True, message="ok",
            data={"results": {"variants": [], "success": True, "count": 0}},
        )
        result = listing_service.delete_upload(
            self.user, self.store, upload, delete_system=True,
        )
        self.assertEqual(result["listings_deleted"], 2)
        self.assertEqual(StoreListing.objects.filter(store=self.store).count(), 0)
        self.assertFalse(ListingUpload.objects.filter(pk=upload.id).exists())

    @patch("listings.listing_service.LasooClient")
    def test_delete_upload_with_marketplace(self, mock_client_cls):
        content = csv_import.build_template_csv("create").encode()
        listing_service.bulk_import(self.user, self.store, "mp.csv", content, action="create")
        listings = list(StoreListing.objects.filter(store=self.store))
        self.assertEqual(len(listings), 2)
        for listing in listings:
            listing.status = ListingStatus.UPLOADED_STAGING
            listing.save(update_fields=["status"])
        upload = ListingUpload.objects.get(store=self.store, filename="mp.csv")

        mock_client = mock_client_cls.return_value
        mock_client.auth_key = "key"
        catalog = {"JDXTY-XL-B", "JDXTY-S-R"}

        def send(endpoint, payload=None, *args, **kwargs):
            data = (payload or {}).get("data") or {}
            if endpoint == "variants_search":
                key = data.get("externalVariantKey")
                found = key in catalog
                variants = (
                    [{"externalProductKey": key, "externalVariantKey": key}]
                    if found else []
                )
                return LasooResult(
                    ok=True, message="ok",
                    data={"results": {"variants": variants, "success": True}},
                )
            if endpoint == "bulk_delete":
                catalog.clear()
                return LasooResult(ok=True, message="ok", data={"success": True})
            return LasooResult(ok=True, message="ok", data={})

        mock_client.send.side_effect = send

        result = listing_service.delete_upload(
            self.user, self.store, upload,
            delete_system=True, delete_marketplace=True,
        )
        self.assertEqual(result["listings_deleted"], 2)
        self.assertEqual(result["marketplace_deleted"], 2)
        self.assertTrue(
            any(call[0][0] == "bulk_delete" for call in mock_client.send.call_args_list)
        )
        self.assertEqual(StoreListing.objects.filter(store=self.store).count(), 0)

    @patch("listings.listing_service.LasooClient")
    def test_bulk_import_mapped_sets_uploaded_status(self, mock_client_cls):
        mock_client = mock_client_cls.return_value
        mock_client.auth_key = "key"

        def send(endpoint, payload=None, *args, **kwargs):
            data = (payload or {}).get("data") or {}
            key = data.get("externalVariantKey") or data.get("externalProductKey") or "x"
            return LasooResult(
                ok=True,
                message="ok",
                data={
                    "results": {
                        "variants": [
                            {"externalProductKey": key, "externalVariantKey": key},
                        ],
                    },
                },
            )

        mock_client.send.side_effect = send
        content = csv_import.build_template_csv("mapped").encode()
        result = listing_service.bulk_import(self.user, self.store, "m.csv", content, action="mapped")
        self.assertEqual(result["imported"], 2)
        listing = StoreListing.objects.get(store=self.store, sku="JDXTY-XL-B")
        self.assertEqual(listing.action, "mapped")
        self.assertEqual(listing.status, ListingStatus.UPLOADED_STAGING)
        self.assertEqual(listing.source_vendor_code, "noraau")
        self.assertTrue(all(c[0][0] == "variants_search" for c in mock_client.send.call_args_list))

    @patch("listings.listing_service.LasooClient")
    def test_bulk_import_mapped_rejects_missing_lasoo_sku(self, mock_client_cls):
        mock_client = mock_client_cls.return_value
        mock_client.auth_key = "key"
        mock_client.send.return_value = LasooResult(
            ok=True,
            message="ok",
            data={"results": {"success": True, "variants": [], "count": 0, "total": 0}},
        )
        content = csv_import.build_template_csv("mapped").encode()
        result = listing_service.bulk_import(self.user, self.store, "m.csv", content, action="mapped")
        self.assertEqual(result["imported"], 0)
        listing = StoreListing.objects.get(store=self.store, sku="JDXTY-XL-B")
        self.assertEqual(listing.action, "mapped")
        self.assertEqual(listing.status, ListingStatus.VALIDATION_FAILED)
        self.assertTrue(any("not found" in e.lower() for e in (listing.validation_errors_json or [])))

    @patch("listings.listing_service.LasooClient")
    def test_push_inventory_skips_skus_missing_on_lasoo(self, mock_client_cls):
        present = listing_service.create(self.user, self.store, dict(VALID_DATA))
        present.status = ListingStatus.UPLOADED_STAGING
        present.save(update_fields=["status"])
        missing_data = dict(VALID_DATA)
        missing_data.update({
            "sku": "U-Z1618",
            "variant_key": "U-Z1618",
            "product_key": "U-Z1618",
        })
        missing = listing_service.create(self.user, self.store, missing_data)
        missing.status = ListingStatus.UPLOADED_STAGING
        missing.save(update_fields=["status"])

        mock_client = mock_client_cls.return_value
        mock_client.auth_key = "key"

        def send(endpoint, payload=None, *args, **kwargs):
            data = (payload or {}).get("data") or {}
            key = data.get("externalVariantKey") or data.get("externalProductKey") or ""
            if endpoint == "variants_search":
                if key == "U-Z1618":
                    return LasooResult(
                        ok=True,
                        message="ok",
                        data={"results": {"variants": [], "count": 0}},
                    )
                return LasooResult(
                    ok=True,
                    message="ok",
                    data={
                        "results": {
                            "variants": [
                                {"externalProductKey": key, "externalVariantKey": key},
                            ],
                        },
                    },
                )
            return LasooResult(ok=True, message="ok", data={"success": True})

        mock_client.send.side_effect = send
        result = listing_service.push_inventory(self.user, self.store)
        self.assertTrue(result["ok"])
        self.assertEqual(result["skipped"], 1)
        self.assertIn("U-Z1618", result["message"])
        upserts = [c for c in mock_client.send.call_args_list if c[0][0] == "bulk_upsert"]
        self.assertEqual(len(upserts), 1)
        variants = (upserts[0][0][1].get("data") or {}).get("variants") or []
        keys = {v.get("externalVariantKey") for v in variants}
        self.assertIn(present.sku, keys)
        self.assertNotIn("U-Z1618", keys)

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

    @patch("listings.listing_service.LasooClient")
    def test_publish_excludes_already_uploaded_listings(self, mock_client_cls):
        """Publish all must not re-send inventory rows (avoids marketplace duplicates)."""
        uploaded = listing_service.create(self.user, self.store, dict(VALID_DATA))
        uploaded.status = ListingStatus.UPLOADED_STAGING
        uploaded.save(update_fields=["status"])

        ready_data = dict(VALID_DATA)
        ready_data.update({
            "sku": "TSHIRT-002-BLACK-M",
            "variant_key": "TSHIRT-002-BLACK-M",
            "product_key": "TSHIRT-002",
        })
        ready = listing_service.create(self.user, self.store, ready_data)
        self.assertEqual(ready.status, ListingStatus.READY)

        mock_client = mock_client_cls.return_value
        mock_client.auth_key = "key"
        mock_client.send.return_value = LasooResult(ok=True, message="ok", data={})

        result = listing_service.publish(self.user, self.store)
        self.assertTrue(result["ok"])
        self.assertEqual(result["published"], 1)
        mock_client.send.assert_called_once()
        payload = mock_client.send.call_args[0][1]
        data = payload.get("data") or payload
        variants = data.get("variants") or []
        self.assertEqual(len(variants), 1)
        self.assertEqual(
            variants[0].get("externalVariantKey") or variants[0].get("sku"),
            ready.sku,
        )

        ready.refresh_from_db()
        uploaded.refresh_from_db()
        self.assertEqual(ready.status, ListingStatus.UPLOADED_STAGING)
        self.assertEqual(uploaded.status, ListingStatus.UPLOADED_STAGING)
        self.assertIsNotNone(ready.last_uploaded_at)
        self.assertIsNone(uploaded.last_uploaded_at)

    @patch("listings.listing_service.LasooClient")
    def test_publish_fails_when_lasoo_reports_mapping_errors(self, mock_client_cls):
        listing = listing_service.create(self.user, self.store, dict(VALID_DATA))
        mock_client = mock_client_cls.return_value
        mock_client.auth_key = "key"
        mock_client.send.return_value = LasooResult(
            ok=True,
            message="ok",
            data={
                "success": True,
                "results": {
                    "success": True,
                    "dataMappingErrors": [
                        {
                            "externalVariantKey": listing.sku,
                            "errors": ["Category could not be mapped"],
                        }
                    ],
                },
            },
        )
        result = listing_service.publish(self.user, self.store)
        self.assertFalse(result["ok"])
        self.assertEqual(result["published"], 0)
        self.assertIn("mapping", result["message"].lower())
        listing.refresh_from_db()
        self.assertEqual(listing.status, ListingStatus.FAILED)
        self.assertTrue(listing.validation_errors_json)

    def test_reverb_template_headers(self):
        reverb, _ = Marketplace.objects.get_or_create(code="reverb", defaults={"name": "Reverb"})
        store2 = Store.objects.create(
            user=self.user, name="Reverb Store 2", region="USA",
            api_token="tok", marketplace=reverb, management_mode="full_store",
        )
        csv_text = csv_import.build_template_csv("create", store=store2)
        cols = csv_text.splitlines()[0].split(",")
        self.assertEqual(cols[0], "Vendor Name (Optional)")
        self.assertEqual(cols[1], "Vendor URL (Optional)")
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

    def test_mydeal_template_headers(self):
        mydeal, _ = Marketplace.objects.get_or_create(code="mydeal", defaults={"name": "MyDeal"})
        store_md = Store.objects.create(
            user=self.user, name="MyDeal Store", region="AU",
            api_token="tok", marketplace=mydeal, management_mode="full_store",
        )
        csv_text = csv_import.build_template_csv("create", store=store_md)
        cols = csv_text.splitlines()[0].split(",")
        self.assertEqual(cols[0], "Vendor Name (Optional)")
        self.assertIn("Shipping Cost Category", csv_text)
        self.assertIn("Product Key (Optional)", csv_text)
        self.assertIn("Is Direct Import", csv_text)
        self.assertIn("GTIN (Optional)", csv_text)
        self.assertIn("RRP (Optional)", csv_text)
        rows = csv_import.parse_upload("mydeal.csv", csv_text.encode())
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["sku"], "MD-EXAMPLE-001-S")
        self.assertEqual(rows[0]["product_key"], "MD-EXAMPLE-001")
        self.assertEqual(rows[1]["sku"], "MD-EXAMPLE-001-M")
        self.assertEqual(rows[1]["product_key"], "MD-EXAMPLE-001")
        self.assertFalse(rows[0]["is_direct_import"])
        self.assertEqual(rows[0]["shipping_cost_category"], "Flat")


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

    def test_build_order_details_from_marketplacer_buyer_fields(self):
        """Lasoo/Marketplacer invoices expose buyer* + nested state/country objects."""
        raw = {
            "id": 1024157,
            "invoiceNumber": "1024157",
            "status": "PAID",
            "createdAt": "2026-07-22T03:33:00Z",
            "totalCents": 2699,
            "buyerFirstName": "Taylor",
            "buyerSurname": "Nguyen",
            "buyerEmailAddress": "taylor@example.com",
            "buyerPhone": "0412345678",
            "companyName": "Nguyen Co",
            "shippingAddress": {
                "address": "42 Harbour Rd",
                "subaddress": "Unit 3",
                "city": "Sydney",
                "postcode": "2000",
                "state": {"name": "New South Wales", "short": "NSW"},
                "country": {"code": "AU", "name": "Australia"},
            },
            "buyerBillingAddress": {
                "address": "42 Harbour Rd",
                "city": "Sydney",
                "postcode": "2000",
                "state": {"short": "NSW"},
                "country": {"code": "AU"},
            },
            "lineItems": [
                {
                    "title": "Plush Dog Crate Bed",
                    "sku": "P-XD1836-M-BG",
                    "quantity": 1,
                    "priceCents": 2699,
                }
            ],
        }
        details = order_service.build_order_details(raw)
        self.assertEqual(details["customer"]["firstName"], "Taylor")
        self.assertEqual(details["customer"]["lastName"], "Nguyen")
        self.assertEqual(details["customer"]["name"], "Taylor Nguyen")
        self.assertEqual(details["customer"]["email"], "taylor@example.com")
        self.assertEqual(details["customer"]["phone"], "0412345678")
        self.assertEqual(details["shippingAddress"]["line1"], "42 Harbour Rd")
        self.assertEqual(details["shippingAddress"]["line2"], "Unit 3")
        self.assertEqual(details["shippingAddress"]["city"], "Sydney")
        self.assertEqual(details["shippingAddress"]["state"], "New South Wales")
        self.assertEqual(details["shippingAddress"]["postcode"], "2000")
        self.assertEqual(details["shippingAddress"]["country"], "Australia")
        self.assertEqual(details["billingAddress"]["state"], "NSW")
        self.assertEqual(details["billingAddress"]["country"], "AU")

    def test_build_order_details_from_legacy_customer_include(self):
        """Legacy REST include=customer puts address fields flat on the customer blob."""
        details = order_service.build_order_details({
            "invoiceNumber": "INV-LEGACY",
            "customer": {
                "first_name": "Test",
                "surname": "User",
                "email_address": "test@example.com",
                "phone": "0491570156",
                "address": "1 Example Road",
                "city": "Melbourne",
                "state": "Victoria",
                "postcode": "3000",
                "country": "Australia",
            },
            "lineItems": [{"title": "Item", "quantity": 1, "priceCents": 1000}],
        })
        self.assertEqual(details["customer"]["name"], "Test User")
        self.assertEqual(details["customer"]["email"], "test@example.com")
        self.assertEqual(details["shippingAddress"]["line1"], "1 Example Road")
        self.assertEqual(details["shippingAddress"]["city"], "Melbourne")
        self.assertEqual(details["shippingAddress"]["postcode"], "3000")

    def test_build_order_details_from_lasoo_connect_order_blob(self):
        """Lasoo Connect: flat buyer+address directly on ``order``."""
        raw = {
            "id": 1024157,
            "status": "PAID",
            "totalCents": 2699,
            "lineItems": [
                {
                    "title": "Plush Dog Crate Bed with Non Slip Base and Removable Cover",
                    "sku": "P-XD1836-M-BG",
                    "quantity": 1,
                    "priceCents": 2699,
                }
            ],
            "shipment": [],
            "order": {
                "city": "Kellyville",
                "phone": "0435713191",
                "state": "NSW",
                "address": "46 stringer road",
                "country": "AU",
                "orderId": None,
                "surname": "Goplani",
                "postcode": "2155",
                "firstName": "Piyush",
                "emailAddress": "ca.goplanipiyush@gmail.com",
                "billingOrderId": 5020885,
            },
        }
        details = order_service.build_order_details(raw)
        self.assertEqual(details["customer"]["firstName"], "Piyush")
        self.assertEqual(details["customer"]["lastName"], "Goplani")
        self.assertEqual(details["customer"]["name"], "Piyush Goplani")
        self.assertEqual(details["customer"]["email"], "ca.goplanipiyush@gmail.com")
        self.assertEqual(details["customer"]["phone"], "0435713191")
        self.assertEqual(details["shippingAddress"]["line1"], "46 stringer road")
        self.assertEqual(details["shippingAddress"]["city"], "Kellyville")
        self.assertEqual(details["shippingAddress"]["state"], "NSW")
        self.assertEqual(details["shippingAddress"]["postcode"], "2155")
        self.assertEqual(details["shippingAddress"]["country"], "AU")

    def test_build_order_details_from_lasoo_connect_order_customer(self):
        """Production Lasoo Connect: buyer lives under order.customer / billingCustomer."""
        raw = {
            "id": 1024157,
            "status": "PAID",
            "totalCents": 2699,
            "createdAt": "2026-07-22T03:33:00Z",
            "lineItems": [
                {
                    "title": "Plush Dog Crate Bed with Non Slip Base and Removable Cover",
                    "sku": "P-XD1836-M-BG",
                    "quantity": 1,
                    "priceCents": 2699,
                }
            ],
            "shipment": [],
            "order": {
                "customer": {
                    "id": 47127,
                    "city": "Kellyville",
                    "phone": "0435713191",
                    "state": "NSW",
                    "address": "46 stringer road",
                    "country": "AU",
                    "orderId": 5020885,
                    "surname": "Goplani",
                    "postcode": "2155",
                    "firstName": "Piyush",
                    "emailAddress": "ca.goplanipiyush@gmail.com",
                    "billingOrderId": None,
                },
                "billingCustomer": {
                    "id": 47128,
                    "city": "Kellyville",
                    "phone": "0435713191",
                    "state": "NSW",
                    "address": "46 stringer road",
                    "country": "AU",
                    "orderId": None,
                    "surname": "Goplani",
                    "postcode": "2155",
                    "firstName": "Piyush",
                    "emailAddress": "ca.goplanipiyush@gmail.com",
                    "billingOrderId": 5020885,
                },
            },
        }
        details = order_service.build_order_details(raw)
        self.assertEqual(details["customer"]["firstName"], "Piyush")
        self.assertEqual(details["customer"]["lastName"], "Goplani")
        self.assertEqual(details["customer"]["name"], "Piyush Goplani")
        self.assertEqual(details["customer"]["email"], "ca.goplanipiyush@gmail.com")
        self.assertEqual(details["customer"]["phone"], "0435713191")
        self.assertEqual(details["shippingAddress"]["line1"], "46 stringer road")
        self.assertEqual(details["shippingAddress"]["city"], "Kellyville")
        self.assertEqual(details["shippingAddress"]["state"], "NSW")
        self.assertEqual(details["shippingAddress"]["postcode"], "2155")
        self.assertEqual(details["shippingAddress"]["country"], "AU")
        self.assertEqual(details["billingAddress"]["line1"], "46 stringer road")
        self.assertEqual(details["billingAddress"]["postcode"], "2155")

        # Empty stored customer_info must still resolve from raw (Orders API path).
        details2 = order_service.build_order_details(raw, customer_info=None)
        self.assertEqual(details2["customer"]["name"], "Piyush Goplani")
        self.assertEqual(details2["shippingAddress"]["city"], "Kellyville")

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

    def test_cancel_reasons_reverb_returns_api_codes(self):
        User = get_user_model()
        user = User.objects.create_user(username="rvcancel", email="rvc@example.com", password="pw")
        reverb, _ = Marketplace.objects.get_or_create(code="reverb", defaults={"name": "Reverb"})
        store = Store.objects.create(
            user=user, name="Reverb Cancel Store", region="USA",
            api_token="reverb-token-1234567890", marketplace=reverb,
            management_mode="full_store",
        )
        result = order_service.cancel_reasons(store)
        values = [r["value"] for r in result["reasons"]]
        labels = [r["label"] for r in result["reasons"]]
        self.assertTrue(result["ok"])
        self.assertEqual(result["marketplace"], "reverb")
        self.assertIn("sold_elsewhere", values)
        self.assertIn("accidental_order", values)
        self.assertTrue(any("Sold elsewhere" in lbl for lbl in labels))
        self.assertNotIn("Other", values)

    @patch("listings.reverb.orders.get_adapter")
    def test_cancel_reverb_issues_refund_request(self, mock_get_adapter):
        User = get_user_model()
        user = User.objects.create_user(username="rvcancel2", email="rvc2@example.com", password="pw")
        reverb, _ = Marketplace.objects.get_or_create(code="reverb", defaults={"name": "Reverb"})
        store = Store.objects.create(
            user=user, name="Reverb Cancel Store 2", region="USA",
            api_token="reverb-token-1234567890", marketplace=reverb,
            management_mode="full_store",
        )
        order = MarketplaceOrder.objects.create(
            user=user,
            store=store,
            external_order_key="25137835",
            invoice_number="25137835",
            status=OrderStatus.PAID,
            total_amount_cents=13500,
            raw_response_json={"currency": "USD", "_reverb": {"total": {"amount": "135.00", "amount_cents": 13500, "currency": "USD"}}},
            environment="production",
        )
        adapter = MagicMock()
        adapter.create_seller_refund_request.return_value = {"id": 99, "state": "approved"}
        mock_get_adapter.return_value = adapter

        result = order_service.cancel(order, reason="sold_elsewhere")
        order.refresh_from_db()
        self.assertTrue(result["ok"])
        self.assertTrue(result["marketplace_ok"])
        self.assertEqual(order.status, OrderStatus.CANCELLED)
        adapter.create_seller_refund_request.assert_called_once_with(
            "25137835",
            reason="sold_elsewhere",
            amount="135.00",
            currency="USD",
            state="approved",
            note_to_buyer="sold_elsewhere",
        )

    @patch("listings.reverb.orders.get_adapter")
    def test_cancel_reverb_still_marks_local_on_api_error(self, mock_get_adapter):
        from store_adapters.reverb_adapter import ReverbAPIError

        User = get_user_model()
        user = User.objects.create_user(username="rvcancel3", email="rvc3@example.com", password="pw")
        reverb, _ = Marketplace.objects.get_or_create(code="reverb", defaults={"name": "Reverb"})
        store = Store.objects.create(
            user=user, name="Reverb Cancel Store 3", region="USA",
            api_token="reverb-token-1234567890", marketplace=reverb,
            management_mode="full_store",
        )
        order = MarketplaceOrder.objects.create(
            user=user,
            store=store,
            external_order_key="25137836",
            invoice_number="25137836",
            status=OrderStatus.PAID,
            total_amount_cents=5000,
            raw_response_json={"currency": "USD"},
            environment="production",
        )
        adapter = MagicMock()
        adapter.create_seller_refund_request.side_effect = ReverbAPIError("Reverb API POST: 422 — unpaid")
        mock_get_adapter.return_value = adapter

        result = order_service.cancel(order, reason="Out of stock")
        order.refresh_from_db()
        self.assertTrue(result["ok"])
        self.assertFalse(result["marketplace_ok"])
        self.assertEqual(order.status, OrderStatus.CANCELLED)
        self.assertEqual(order.raw_response_json["_local_cancel"]["reason"], "sold_elsewhere")


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
