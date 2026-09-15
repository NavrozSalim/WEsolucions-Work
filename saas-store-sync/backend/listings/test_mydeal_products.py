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
