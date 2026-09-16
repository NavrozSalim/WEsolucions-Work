from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from listings.models import ListingStatus
from listings.mydeal import products as mydeal_products
from listings.mydeal.client import MyDealResult


def _listing(**overrides):
    base = dict(
        sku="POLO-SMALL",
        external_product_key="POLO-SHIRT",
        external_variant_key="POLO-SMALL",
        title="Sample Polo",
        description="A polo shirt",
        brand="ExampleBrand",
        category="3213",
        image_urls="https://example.com/polo.jpg",
        variation_image_url="",
        sale_price="29.99",
        original_price="39.99",
        sale_price_cents=2999,
        original_price_cents=3999,
        inventory=10,
        infinite_quantity=False,
        option_1_name="Size",
        option_1_value="Small",
        option_2_name="",
        option_2_value="",
        option_3_name="",
        option_3_value="",
        external_data_object_json={
            "marketplace": "mydeal",
            "condition": "New",
            "shipping_cost_category": "Flat",
            "shipping_cost_standard": "0",
            "is_direct_import": False,
            "max_days_for_delivery": "10",
            "delivery_time": "5-10 business days",
        },
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class MyDealProductGroupTests(SimpleTestCase):
    def test_standalone_uses_sku_as_parent(self):
        listing = _listing(
            sku="MIXER-1",
            external_product_key="",
            external_variant_key="",
            option_1_name="",
            option_1_value="",
        )
        group = mydeal_products.listing_to_product_group(listing)
        self.assertEqual(group["ExternalProductID"], "MIXER-1")
        self.assertEqual(group["ProductSKU"], "MIXER-1")
        self.assertEqual(len(group["BuyableProducts"]), 1)
        self.assertEqual(group["BuyableProducts"][0]["SKU"], "MIXER-1")

    def test_validate_variations_require_shared_product_key(self):
        data = {
            "sku": "POLO-SMALL",
            "title": "Polo",
            "description": "Shirt",
            "category": "3213",
            "image_urls": "https://example.com/a.jpg",
            "sale_price": "10",
            "option_1_name": "Size",
            "option_1_value": "Small",
        }
        errors = " ".join(mydeal_products.validate_listing(data))
        self.assertIn("Parent SKU", errors)
        data["product_key"] = "POLO-SMALL"
        errors = " ".join(mydeal_products.validate_listing(data))
        self.assertIn("differ from SKU", errors)
        data["product_key"] = "POLO-SHIRT"
        self.assertEqual(mydeal_products.validate_listing(data), [])

    def test_group_variants_share_one_product(self):
        small = _listing()
        medium = _listing(
            sku="POLO-MEDIUM",
            external_variant_key="POLO-MEDIUM",
            option_1_value="Medium",
            inventory=4,
        )
        packed = mydeal_products.listings_to_product_groups([small, medium])
        self.assertEqual(len(packed), 1)
        group, members = packed[0]
        self.assertEqual(len(members), 2)
        self.assertEqual(group["ExternalProductID"], "POLO-SHIRT")
        self.assertEqual(group["ProductSKU"], "POLO-SHIRT")
        skus = [b["SKU"] for b in group["BuyableProducts"]]
        self.assertEqual(skus, ["POLO-SMALL", "POLO-MEDIUM"])
        self.assertEqual(group["BuyableProducts"][0]["Options"][0]["OptionValue"], "Small")
        self.assertEqual(group["BuyableProducts"][1]["Options"][0]["OptionValue"], "Medium")

    def test_different_product_keys_stay_separate(self):
        a = _listing(external_product_key="A", sku="A-1", external_variant_key="A-1")
        b = _listing(external_product_key="B", sku="B-1", external_variant_key="B-1")
        packed = mydeal_products.listings_to_product_groups([a, b])
        self.assertEqual(len(packed), 2)

    def test_standalone_parent_matches_sku(self):
        row = mydeal_products.normalize_row_keys({"sku": "MIXER-1"})
        self.assertEqual(row["product_key"], "MIXER-1")
        self.assertEqual(row["sku"], "MIXER-1")

    def test_prepare_import_copies_parent_fields_onto_variants(self):
        rows = mydeal_products.prepare_import_rows(
            [
                {
                    "product_key": "WK0132",
                    "sku": "WK0132-FF-BBE-4D26DCM",
                    "title": "Coral Sea Fan",
                    "description": "Canvas wall art",
                    "category": "3213",
                    "image_urls": "https://example.com/a.jpg",
                    "sale_price": "19.99",
                    "option_1_name": "Colour",
                    "option_1_value": "Beige",
                },
                {
                    "product_key": "WK0132",
                    "sku": "WK0132-FF-WHT-4D26DCM",
                    "option_1_name": "Colour",
                    "option_1_value": "White",
                },
            ]
        )
        child = rows[1]
        self.assertEqual(child["title"], "Coral Sea Fan")
        self.assertEqual(child["description"], "Canvas wall art")
        self.assertEqual(child["category"], "3213")
        self.assertEqual(child["image_urls"], "https://example.com/a.jpg")
        self.assertEqual(child["sale_price"], "19.99")
        self.assertEqual(child["sku"], "WK0132-FF-WHT-4D26DCM")
        self.assertEqual(child["product_key"], "WK0132")
        self.assertEqual(mydeal_products.validate_listing(child), [])

    def test_prepare_import_uses_parent_only_row(self):
        rows = mydeal_products.prepare_import_rows(
            [
                {
                    "product_key": "WK0132",
                    "sku": "WK0132",
                    "title": "Coral Sea Fan",
                    "description": "Canvas wall art",
                    "category": "3213",
                    "image_urls": "https://example.com/a.jpg",
                    "sale_price": "19.99",
                },
                {
                    "product_key": "WK0132",
                    "sku": "WK0132-FF-BLK-4D26DCM",
                    "option_1_name": "Colour",
                    "option_1_value": "Black",
                    "sale_price": "21.50",
                },
            ]
        )
        child = rows[1]
        self.assertEqual(child["title"], "Coral Sea Fan")
        self.assertEqual(child["sale_price"], "21.50")
        self.assertEqual(child["product_key"], "WK0132")
        self.assertEqual(child["sku"], "WK0132-FF-BLK-4D26DCM")
        self.assertEqual(mydeal_products.validate_listing(child), [])

    def test_group_uses_listing_with_title(self):
        empty = _listing(title="", description="", category="", image_urls="")
        titled = _listing(
            sku="POLO-MEDIUM",
            external_variant_key="POLO-MEDIUM",
            option_1_value="Medium",
        )
        packed = mydeal_products.listings_to_product_groups([empty, titled])
        group, _members = packed[0]
        self.assertEqual(group["Title"], "Sample Polo")
        self.assertEqual(len(group["BuyableProducts"]), 2)

    def test_content_only_parent_row_is_not_a_buyable(self):
        parent = _listing(
            sku="WK0132",
            external_product_key="WK0132",
            external_variant_key="WK0132",
            option_1_name="",
            option_1_value="",
        )
        child = _listing(
            sku="WK0132-FF-BLK-4D26DCM",
            external_product_key="WK0132",
            external_variant_key="WK0132-FF-BLK-4D26DCM",
            title="",
            option_1_name="Colour",
            option_1_value="Black",
        )
        packed = mydeal_products.listings_to_product_groups([parent, child])
        self.assertEqual(len(packed), 1)
        group, members = packed[0]
        self.assertEqual(group["ProductSKU"], "WK0132")
        self.assertEqual(group["Title"], "Sample Polo")
        self.assertEqual([b["SKU"] for b in group["BuyableProducts"]], ["WK0132-FF-BLK-4D26DCM"])
        self.assertEqual(len(members), 2)


def _saveable_listing(**overrides):
    listing = _listing(**overrides)
    listing.status = ListingStatus.READY
    listing.validation_errors_json = None
    listing.marketplace_request_json = None
    listing.marketplace_response_json = None
    listing.last_uploaded_at = None
    listing.updated_at = None
    listing.save = MagicMock()
    return listing


class MyDealPublishTests(SimpleTestCase):
    def _store(self):
        return SimpleNamespace(name="Shemaya", mydeal_setup_method="api")

    def _listings(self, n):
        rows = []
        for i in range(n):
            sku = f"SKU-{i}"
            rows.append(
                _saveable_listing(
                    sku=sku,
                    external_product_key="",
                    external_variant_key=sku,
                    option_1_name="",
                    option_1_value="",
                )
            )
        return rows

    @patch("listings.mydeal.products.MyDealClient")
    def test_publish_chunks_product_groups(self, mock_client_cls):
        client = mock_client_cls.return_value
        client.environment = "sandbox"
        client.upsert_products.return_value = MyDealResult(ok=True, data={"ResponseStatus": "Success"})
        store = self._store()
        listings = self._listings(5)
        with patch.object(mydeal_products, "PUBLISH_GROUP_CHUNK", 2):
            out = mydeal_products.publish_listings(None, store, listings)
        self.assertEqual(client.upsert_products.call_count, 3)
        client.get_pending_response.assert_not_called()
        self.assertTrue(out["ok"])
        self.assertEqual(out["uploaded"], 5)
        self.assertEqual(out["failed"], 0)
        self.assertEqual(listings[0].status, ListingStatus.UPLOADED_STAGING)

    @patch("listings.mydeal.products.MyDealClient")
    def test_publish_failure_sets_visible_error(self, mock_client_cls):
        client = mock_client_cls.return_value
        client.environment = "sandbox"
        client.upsert_products.return_value = MyDealResult(
            ok=False,
            message="Could not reach MyDeal. Please try again.",
            data=None,
            status=0,
        )
        listings = self._listings(2)
        out = mydeal_products.publish_listings(None, self._store(), listings)
        self.assertFalse(out["ok"])
        self.assertEqual(out["failed"], 2)
        self.assertEqual(listings[0].status, ListingStatus.FAILED)
        self.assertEqual(
            listings[0].validation_errors_json,
            ["Could not reach MyDeal. Please try again."],
        )
        self.assertEqual(
            listings[0].marketplace_response_json["error"],
            "Could not reach MyDeal. Please try again.",
        )

    @patch("listings.mydeal.products.MyDealClient")
    def test_auth_failure_stops_remaining_chunks(self, mock_client_cls):
        client = mock_client_cls.return_value
        client.environment = "sandbox"
        client.upsert_products.return_value = MyDealResult(
            ok=False,
            message="No production MyDeal ClientID configured for store 'Shemaya'.",
            data=None,
            status=0,
        )
        listings = self._listings(5)
        with patch.object(mydeal_products, "PUBLISH_GROUP_CHUNK", 2):
            out = mydeal_products.publish_listings(None, self._store(), listings)
        self.assertEqual(client.upsert_products.call_count, 1)
        self.assertEqual(out["failed"], 5)
        self.assertTrue(all(row.status == ListingStatus.FAILED for row in listings))

    @patch("listings.mydeal.products.time.sleep")
    @patch("listings.mydeal.products.MyDealClient")
    def test_async_pending_polls_then_marks_uploaded(self, mock_client_cls, _sleep):
        client = mock_client_cls.return_value
        client.environment = "sandbox"
        client.upsert_products.return_value = MyDealResult(
            ok=True,
            data={
                "ResponseStatus": "AsyncResponsePending",
                "PendingUri": "/pending-responses?workItemId=16320398",
                "WorkItemId": "16320398",
                "Data": None,
            },
            response_status="AsyncResponsePending",
        )
        client.get_pending_response.return_value = MyDealResult(
            ok=True,
            data={"ResponseStatus": "Success", "Data": [{"ProductSKU": "SKU-0"}]},
            response_status="Success",
        )
        listings = self._listings(1)
        out = mydeal_products.publish_listings(None, self._store(), listings)
        client.get_pending_response.assert_called_once_with("16320398")
        self.assertTrue(out["ok"])
        self.assertEqual(out["uploaded"], 1)
        self.assertEqual(listings[0].status, ListingStatus.UPLOADED_STAGING)

    @patch("listings.mydeal.products.time.sleep")
    @patch("listings.mydeal.products.MyDealClient")
    def test_async_pending_poll_failure_marks_failed(self, mock_client_cls, _sleep):
        client = mock_client_cls.return_value
        client.environment = "sandbox"
        client.upsert_products.return_value = MyDealResult(
            ok=True,
            data={
                "ResponseStatus": "AsyncResponsePending",
                "WorkItemId": "99",
                "Data": None,
            },
            response_status="AsyncResponsePending",
        )
        client.get_pending_response.return_value = MyDealResult(
            ok=False,
            message="Title is required.",
            data={"ResponseStatus": "Failed"},
            status=200,
            response_status="Failed",
        )
        listings = self._listings(1)
        out = mydeal_products.publish_listings(None, self._store(), listings)
        self.assertFalse(out["ok"])
        self.assertEqual(out["failed"], 1)
        self.assertEqual(listings[0].status, ListingStatus.FAILED)
        self.assertIn("Title is required.", listings[0].validation_errors_json[0])

    @patch("listings.mydeal.products.time.sleep")
    @patch("listings.mydeal.products.MyDealClient")
    def test_async_pending_timeout_does_not_mark_uploaded(self, mock_client_cls, _sleep):
        client = mock_client_cls.return_value
        client.environment = "sandbox"
        pending = MyDealResult(
            ok=True,
            data={"ResponseStatus": "AsyncResponsePending", "WorkItemId": "77", "Data": None},
            response_status="AsyncResponsePending",
        )
        client.upsert_products.return_value = pending
        client.get_pending_response.return_value = pending
        listings = self._listings(1)
        with patch.object(mydeal_products, "PENDING_POLL_ATTEMPTS", 2):
            with patch.object(mydeal_products, "PENDING_POLL_SECONDS", 0):
                out = mydeal_products.publish_listings(None, self._store(), listings)
        self.assertEqual(client.get_pending_response.call_count, 2)
        self.assertFalse(out["ok"])
        self.assertEqual(listings[0].status, ListingStatus.FAILED)
        self.assertNotEqual(listings[0].status, ListingStatus.UPLOADED_STAGING)

    def test_unconfirmed_upload_detects_async_pending(self):
        listing = _listing()
        listing.status = ListingStatus.UPLOADED_PRODUCTION
        listing.marketplace_response_json = {
            "ResponseStatus": "AsyncResponsePending",
            "WorkItemId": "16320398",
            "Data": None,
        }
        self.assertTrue(mydeal_products.is_unconfirmed_upload(listing))
        listing.marketplace_response_json = {"ResponseStatus": "Success", "Data": [{"ProductSKU": "X"}]}
        self.assertFalse(mydeal_products.is_unconfirmed_upload(listing))
