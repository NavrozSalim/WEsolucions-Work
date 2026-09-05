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
from listings.bunnings.client import (
    BunningsClient,
    BunningsResult,
    extract_import_id,
    import_has_line_errors,
    parse_mirakl_error_report,
)
from listings.errors import MarketplaceError
from listings.models import ListingStatus, MarketplaceOrder, OrderStatus, SupportTicket, TicketStatus
from listings.bunnings import tickets as bunnings_tickets

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
        external_product_key=data.get("product_key") or "",
        external_variant_key=data.get("variant_key") or "",
        option_1_name=data.get("option_1_name") or "",
        option_1_value=data.get("option_1_value") or "",
        option_2_name=data.get("option_2_name") or "",
        option_2_value=data.get("option_2_value") or "",
        option_3_name=data.get("option_3_name") or "",
        option_3_value=data.get("option_3_value") or "",
        option_4_name=data.get("option_4_name") or "",
        option_4_value=data.get("option_4_value") or "",
        variation_image_url=data.get("variation_image_url") or "",
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

    def test_validate_rejects_placeholder_category(self):
        errors = " ".join(
            bunnings_products.validate_listing(
                {**VALID_BUNNINGS, "category": "REPLACE_WITH_CATEGORY_CODE"}
            )
        )
        self.assertIn("placeholder", errors.lower())

    def test_parse_error_report_extracts_category_1004(self):
        csv_text = (
            'category;"product-id";"product-id-type";"variant-group-code";"title";'
            '"description";"brand";"ean";"image-1";"line number";"warnings";"errors"\n'
            'REPLACE_WITH_CATEGORY_CODE;"TEST-BN-001";"SHOP_SKU";"";"Test Power Drill";'
            '"desc";"REPLACE_WITH_APPROVED_BRAND";"";"https://x.jpg";"2";"";'
            '"1004|The category could not be identified"\n'
        )
        rows = parse_mirakl_error_report(csv_text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sku"], "TEST-BN-001")
        self.assertIn("1004", rows[0]["errors"])

    def test_import_has_line_errors_on_complete_with_errors(self):
        self.assertTrue(
            import_has_line_errors(
                {
                    "import_status": "COMPLETE",
                    "lines_in_error": 1,
                    "lines_in_success": 0,
                    "has_error_report": True,
                }
            )
        )
        self.assertFalse(
            import_has_line_errors(
                {"import_status": "COMPLETE", "lines_in_error": 0, "lines_in_success": 1}
            )
        )
        self.assertFalse(import_has_line_errors({"import_status": "SENT"}))

    def test_poll_import_fails_complete_with_line_errors(self):
        store = SimpleNamespace(
            name="t",
            bunnings_environment="production",
            bunnings_production_base_url="https://bunnings-prod.mirakl.net",
            bunnings_production_shop_key="key",
        )
        client = BunningsClient(store)
        client.product_import_status = lambda _id: BunningsResult(
            ok=True,
            data={
                "import_status": "COMPLETE",
                "lines_in_error": 1,
                "lines_in_success": 0,
                "has_error_report": True,
            },
        )
        client.import_error_report = lambda _kind, _id: BunningsResult(
            ok=True,
            data=(
                'category;product-id;errors\n'
                'BAD;"BN-1";"1004|The category could not be identified"\n'
            ),
        )
        result = client.poll_import("product", "9", attempts=1, interval=0)
        self.assertFalse(result.ok)
        self.assertIn("1004", result.message)
        self.assertEqual(result.data["line_errors"][0]["sku"], "BN-1")

    def test_poll_import_does_not_treat_sent_as_success(self):
        store = SimpleNamespace(
            name="t",
            bunnings_environment="production",
            bunnings_production_base_url="https://bunnings-prod.mirakl.net",
            bunnings_production_shop_key="key",
        )
        client = BunningsClient(store)
        client.product_import_status = lambda _id: BunningsResult(
            ok=True, data={"import_status": "SENT"}
        )
        result = client.poll_import("product", "9", attempts=2, interval=0)
        self.assertFalse(result.ok)
        self.assertIn("still", result.message.lower())

    def test_validate_variations_require_shared_product_key(self):
        data = {
            **VALID_BUNNINGS,
            "sku": "BN-1-M",
            "option_1_name": "Size",
            "option_1_value": "M",
        }
        errors = " ".join(bunnings_products.validate_listing(data))
        self.assertIn("Parent SKU", errors)
        data["product_key"] = "BN-1-M"
        errors = " ".join(bunnings_products.validate_listing(data))
        self.assertIn("differ from SKU", errors)
        data["product_key"] = "BN-1"
        self.assertEqual(bunnings_products.validate_listing(data), [])

    def test_products_csv_includes_variant_group_and_options(self):
        listing = _listing_ns(
            sku="BN-1-M",
            product_key="BN-1",
            option_1_name="Size",
            option_1_value="M",
            option_2_name="Colour",
            option_2_value="Red",
            variation_image_url="https://example.com/red-m.jpg",
        )
        text = bunnings_products.products_csv([listing])
        header = text.splitlines()[0]
        self.assertIn("variant-group-code", header)
        self.assertIn("size", header)
        self.assertIn("colour", header)
        self.assertIn("BN-1-M", text)
        self.assertIn("BN-1", text)
        self.assertIn("https://example.com/red-m.jpg", text)

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

    def test_flatten_product_attributes_required_and_skips_core(self):
        rows = bunnings_products.flatten_product_attributes({
            "attributes": [
                {"code": "title", "label": "Title", "required": True, "type": "TEXT"},
                {
                    "code": "attribute_pdb_assembly",
                    "label": "Assembly Required",
                    "requirement_level": "REQUIRED",
                    "type": "LIST",
                    "values": [{"code": "Yes", "label": "Yes"}, {"code": "No", "label": "No"}],
                },
                {
                    "code": "colour",
                    "label": "Colour",
                    "requirement_level": "RECOMMENDED",
                    "type": "TEXT",
                },
                {"code": "optional-foo", "label": "Foo", "requirement_level": "OPTIONAL", "type": "TEXT"},
            ]
        })
        codes = [r["code"] for r in rows]
        self.assertIn("attribute_pdb_assembly", codes)
        self.assertIn("colour", codes)
        self.assertNotIn("title", codes)
        self.assertNotIn("optional-foo", codes)
        assembly = next(r for r in rows if r["code"] == "attribute_pdb_assembly")
        self.assertTrue(assembly["required"])

    def test_template_attribute_columns_skips_core_and_formats_header(self):
        store = SimpleNamespace(id="s1")
        with patch(
            "listings.bunnings.products.load_category_attributes",
            return_value=[
                {"code": "title", "label": "Title", "required": True},
                {"code": "attribute_pdb_assembly", "label": "Assembly Required", "required": True},
                {"code": "colour", "label": "Colour", "required": False},
            ],
        ):
            cols = bunnings_products.template_attribute_columns(store, ["BEDSIDE"])
        codes = [c["code"] for c in cols]
        self.assertNotIn("title", codes)
        self.assertEqual(cols[0]["header"], "Assembly Required [attribute_pdb_assembly]")
        self.assertEqual(cols[1]["header"], "Colour (Optional) [colour]")

    def test_validate_requires_pm11_attribute(self):
        store = SimpleNamespace(id="store-1")
        with patch(
            "listings.bunnings.products.load_category_attributes",
            return_value=[{
                "code": "attribute_pdb_assembly",
                "label": "Assembly Required",
                "required": True,
            }],
        ):
            errors = " ".join(bunnings_products.validate_listing(VALID_BUNNINGS, store=store))
            self.assertIn("Assembly Required", errors)
            ok = {
                **VALID_BUNNINGS,
                "attributes": {"attribute_pdb_assembly": "Yes"},
            }
            self.assertEqual(bunnings_products.validate_listing(ok, store=store), [])

    def test_products_csv_includes_attributes_and_weight(self):
        listing = _listing_ns(
            weight="2.5",
            length="30",
            attributes={"attribute_pdb_assembly": "Yes"},
        )
        text = bunnings_products.products_csv([listing])
        header = text.splitlines()[0]
        self.assertIn("attribute_pdb_assembly", header)
        self.assertIn("Yes", text)
        self.assertIn("2.5", text)
        self.assertIn("30", text)


class BunningsOrdersUnitTests(SimpleTestCase):
    def test_map_order_status(self):
        self.assertEqual(bunnings_orders.map_order_status("WAITING_ACCEPTANCE"), OrderStatus.NEW)
        self.assertEqual(bunnings_orders.map_order_status("WAITING_DEBIT"), OrderStatus.PAID)
        self.assertEqual(bunnings_orders.map_order_status("WAITING_DEBIT_PAYMENT"), OrderStatus.PAID)
        self.assertEqual(bunnings_orders.map_order_status("SHIPPING"), OrderStatus.PAID)
        self.assertEqual(bunnings_orders.map_order_status("SHIPPED"), OrderStatus.SENT)
        self.assertEqual(bunnings_orders.map_order_status("CLOSED"), OrderStatus.SHIPPING_COMPLETE)
        self.assertEqual(bunnings_orders.map_order_status("CANCELED"), OrderStatus.CANCELLED)

    def test_accept_refetches_instead_of_faking_shipping(self):
        client = SimpleNamespace()
        client.accept_order = lambda *_a, **_k: BunningsResult(ok=True, data={})
        client.get_order = lambda _oid: BunningsResult(
            ok=True,
            data={
                "order_id": "X",
                "order_state": "WAITING_DEBIT",
                "order_lines": [{"order_line_id": "L1"}],
            },
        )
        raw = {
            "order_id": "X",
            "order_state": "WAITING_ACCEPTANCE",
            "order_lines": [{"order_line_id": "L1"}],
        }
        updated, did = bunnings_orders._accept_if_needed(client, raw)
        self.assertTrue(did)
        self.assertEqual(updated["order_state"], "WAITING_DEBIT")
        self.assertNotEqual(updated["order_state"], "SHIPPING")

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
        self._attr_patch = patch(
            "listings.bunnings.products.load_category_attributes",
            return_value=[],
        )
        self._attr_patch.start()
        self.addCleanup(self._attr_patch.stop)

    def test_template_headers_and_parse(self):
        csv_text = csv_import.build_template_csv("create", store=self.store)
        self.assertIn("Logistic Class", csv_text)
        self.assertIn("Leadtime To Ship (Optional)", csv_text)
        self.assertIn("Category", csv_text)
        self.assertIn("Parent SKU", csv_text)
        self.assertNotIn("Product Key (Optional)", csv_text)
        self.assertIn("Option 1 Name (Optional)", csv_text)
        self.assertIn("Variation Img URL (Optional)", csv_text)
        rows = csv_import.parse_upload("bunnings.csv", csv_text.encode())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sku"], "BN-EXAMPLE-001-M")
        self.assertEqual(rows[0]["product_key"], "BN-EXAMPLE-001")
        self.assertEqual(rows[0]["option_1_name"], "Size")
        self.assertEqual(rows[0]["option_1_value"], "M")
        self.assertEqual(rows[0]["sale_price"], "79.99")
        self.assertEqual(rows[0]["logistic_class"], "SMALL")
        self.assertEqual(rows[0]["leadtime_to_ship"], "2")
        self.assertIn("Category Attributes JSON (Optional)", csv_text)

        attr_csv = (
            "SKU,Title,Description,Brand,Category,Image URLs,Inventory,Price,Logistic Class,"
            "Category Attributes JSON (Optional),attribute_pdb_assembly\n"
            'BN-ATTR,T,D,B,DRILLS,https://example.com/a.jpg,1,9.99,SMALL,'
            '"{""foo"":""bar""}",Yes\n'
        )
        attr_rows = csv_import.parse_upload("bunnings.csv", attr_csv.encode())
        self.assertEqual(attr_rows[0]["sku"], "BN-ATTR")
        self.assertEqual(attr_rows[0]["attributes"]["foo"], "bar")
        self.assertEqual(attr_rows[0]["attributes"]["attribute_pdb_assembly"], "Yes")

        labeled = (
            "SKU,Title,Description,Brand,Category,Image URLs,Inventory,Price,Logistic Class,"
            "Assembly Required [attribute_pdb_assembly]\n"
            "BN-LAB,T,D,B,BEDSIDE,https://example.com/a.jpg,1,9.99,SMALL,Yes\n"
        )
        labeled_rows = csv_import.parse_upload("bunnings.csv", labeled.encode())
        self.assertEqual(labeled_rows[0]["attributes"]["attribute_pdb_assembly"], "Yes")

    def test_template_adds_columns_for_selected_categories(self):
        with patch(
            "listings.bunnings.products.load_category_attributes",
            return_value=[{
                "code": "attribute_pdb_assembly",
                "label": "Assembly Required",
                "required": True,
            }],
        ):
            csv_text = csv_import.build_template_csv(
                "create",
                store=self.store,
                hierarchies=["BEDSIDE", "DRILLS"],
            )
        self.assertIn("Assembly Required [attribute_pdb_assembly]", csv_text)
        self.assertNotIn("Category Attributes JSON", csv_text)
        self.assertIn("BEDSIDE", csv_text)
        self.assertIn("DRILLS", csv_text)
        rows = csv_import.parse_upload("bunnings.csv", csv_text.encode())
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["category"], "BEDSIDE")
        self.assertEqual(rows[1]["category"], "DRILLS")

    def test_create_variation_listing(self):
        listing = listing_service.create(
            self.user,
            self.store,
            {
                **VALID_BUNNINGS,
                "sku": "BN-1-M",
                "product_key": "BN-1",
                "option_1_name": "Size",
                "option_1_value": "M",
                "option_2_name": "Colour",
                "option_2_value": "Red",
            },
        )
        self.assertEqual(listing.status, ListingStatus.READY)
        self.assertEqual(listing.external_product_key, "BN-1")
        self.assertEqual(listing.sku, "BN-1-M")
        self.assertEqual(listing.option_1_name, "Size")
        self.assertEqual(listing.option_1_value, "M")

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
    def test_publish_skips_offer_when_product_has_line_errors(self, mock_cls):
        listing = listing_service.create(self.user, self.store, dict(VALID_BUNNINGS))
        client = mock_cls.return_value
        client.environment = "production"
        client.import_products.return_value = BunningsResult(ok=True, data={"import_id": "p1"})
        client.poll_import.return_value = BunningsResult(
            ok=False,
            data={
                "import_status": "COMPLETE",
                "lines_in_error": 1,
                "line_errors": [
                    {
                        "sku": "BN-1",
                        "errors": "1004|The category could not be identified",
                    }
                ],
            },
            message="Bunnings product import p1 COMPLETE with errors. BN-1: 1004|The category could not be identified",
        )
        result = listing_service.publish(self.user, self.store)
        self.assertFalse(result["ok"])
        listing.refresh_from_db()
        self.assertEqual(listing.status, ListingStatus.FAILED)
        self.assertTrue(listing.validation_errors_json)
        self.assertIn("1004", listing.validation_errors_json[0])
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

    def test_create_persists_category_attributes_and_weight(self):
        listing = listing_service.create(
            self.user,
            self.store,
            {
                **VALID_BUNNINGS,
                "attributes": {"attribute_pdb_assembly": "Yes"},
                "weight": "2.5",
            },
        )
        extras = bunnings_products.parse_extras(listing)
        self.assertEqual(extras.get("attributes", {}).get("attribute_pdb_assembly"), "Yes")
        self.assertEqual(extras.get("weight"), "2.5")

    @patch("listings.bunnings.products.BunningsClient")
    def test_publish_retries_false_uploaded_when_ids_given(self, mock_cls):
        listing = listing_service.create(self.user, self.store, dict(VALID_BUNNINGS))
        listing.status = ListingStatus.UPLOADED_PRODUCTION
        listing.save(update_fields=["status"])
        client = mock_cls.return_value
        client.environment = "production"
        client.import_products.return_value = BunningsResult(ok=True, data={"import_id": "p1"})
        client.import_offers.return_value = BunningsResult(ok=True, data={"import_id": "o1"})
        client.poll_import.return_value = BunningsResult(
            ok=True, data={"import_status": "COMPLETE"}, message="COMPLETE"
        )
        result = listing_service.publish(self.user, self.store, listing_ids=[listing.id])
        self.assertTrue(result["ok"])
        client.import_products.assert_called_once()

    @patch("listings.bunnings.orders.BunningsClient")
    def test_fetch_includes_debit_states(self, mock_cls):
        client = mock_cls.return_value

        def list_orders(**kwargs):
            states = kwargs.get("order_state_codes") or ""
            if "WAITING_DEBIT" in states:
                return BunningsResult(
                    ok=True,
                    data={
                        "orders": [
                            {
                                "order_id": "ORD-DEBIT",
                                "order_state": "WAITING_DEBIT",
                                "total_price": "10.00",
                                "order_lines": [],
                            }
                        ]
                    },
                )
            return BunningsResult(ok=True, data={"orders": []})

        client.list_orders.side_effect = lambda **kw: list_orders(**kw)
        result = bunnings_orders.fetch(self.user, self.store)
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(result["fetched"], 1)
        order = MarketplaceOrder.objects.get(store=self.store, external_order_key="ORD-DEBIT")
        self.assertEqual(order.status, OrderStatus.PAID)
        self.store.refresh_from_db()
        self.assertIsNotNone(self.store.bunnings_last_order_sync_at)
        state_calls = [
            c.kwargs.get("order_state_codes") or ""
            for c in client.list_orders.call_args_list
        ]
        self.assertTrue(any("WAITING_DEBIT_PAYMENT" in s for s in state_calls))

    @patch("listings.bunnings.orders.BunningsClient")
    def test_cancel_sends_reason_body(self, mock_cls):
        client = mock_cls.return_value
        client.cancel_order.return_value = BunningsResult(ok=True, data={})
        order = MarketplaceOrder.objects.create(
            user=self.user,
            store=self.store,
            external_order_key="ORD-9",
            invoice_number="ORD-9",
            status=OrderStatus.PAID,
            environment="production",
            raw_response_json={"status": "SHIPPING", "_bunnings": {"order_state": "SHIPPING"}},
        )
        result = bunnings_orders.cancel(order, reason="OUT_OF_STOCK")
        self.assertTrue(result["ok"])
        self.assertTrue(result["marketplace_ok"])
        client.cancel_order.assert_called_once()
        kwargs = client.cancel_order.call_args.kwargs
        self.assertEqual(kwargs["reason_code"], "OUT_OF_STOCK")
        self.assertTrue(kwargs["reason_label"])

    def test_upsert_inbox_thread(self):
        raw = {
            "id": "th-1",
            "topic": {"value": "Order question"},
            "entities": [{"type": "MMP_ORDER", "id": "ORD-1"}],
            "metadata": {"shop_reply_needed_since": "2026-01-01T00:00:00Z"},
            "messages": [
                {
                    "id": "m1",
                    "body": "Where is it?",
                    "date_created": "2026-01-01T00:00:00Z",
                    "from": {"type": "CUSTOMER", "display_name": "Sam"},
                }
            ],
        }
        ticket = bunnings_tickets.upsert_thread(self.user, self.store, raw)
        self.assertIsNotNone(ticket)
        self.assertEqual(ticket.external_ticket_key, "th-1")
        self.assertEqual(ticket.related_order_key, "ORD-1")
        self.assertEqual(ticket.status, TicketStatus.OPEN)
        self.assertEqual(ticket.messages.count(), 1)
        self.assertEqual(ticket.messages.first().body, "Where is it?")

    @patch("listings.bunnings.tickets.BunningsClient")
    def test_ticket_fetch_lists_threads(self, mock_cls):
        client = mock_cls.return_value
        client.list_threads.return_value = BunningsResult(
            ok=True,
            data={
                "data": [
                    {
                        "id": "th-2",
                        "topic": {"value": "Help"},
                        "messages": [
                            {
                                "id": "m2",
                                "body": "Hi",
                                "from": {"type": "CUSTOMER", "display_name": "Pat"},
                            }
                        ],
                    }
                ]
            },
        )
        result = bunnings_tickets.fetch(self.user, self.store)
        self.assertTrue(result["ok"])
        self.assertEqual(result["fetched"], 1)
        self.assertTrue(SupportTicket.objects.filter(external_ticket_key="th-2").exists())
