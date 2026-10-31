"""Bulk delete of MyDeal products that are on the marketplace but not in this app."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from catalog.models import MydealTemplateRow, ProductMapping
from marketplace.models import Marketplace
from products.models import Product
from stores.models import Store
from vendor.models import Vendor

from . import listing_service, views
from .errors import MarketplaceError
from .models import ListingUpload, StoreListing
from .mydeal import unmapped_delete
from .mydeal.client import MyDealResult


def _catalog_page():
    return MyDealResult(
        ok=True,
        status=200,
        response_status="Complete",
        data={
            "ResponseStatus": "Complete",
            "Data": [
                {
                    "ExternalProductId": "P1",
                    "ProductSKU": "P1",
                    "Title": "Parent one",
                    "BuyableProducts": [
                        {
                            "ExternalBuyableProductID": "P1-A", "SKU": "P1-A",
                            "ListingStatus": "Live", "Price": 10, "RRP": 12, "Quantity": 4,
                        },
                        {
                            "ExternalBuyableProductID": "P1-B", "SKU": "P1-B",
                            "ListingStatus": "Live", "Price": 11, "RRP": 13, "Quantity": 2,
                        },
                    ],
                },
                {
                    "ExternalProductId": "C1",
                    "ProductSKU": "C1",
                    "Title": "Standalone",
                    "BuyableProducts": [
                        {"ExternalBuyableProductID": "C1", "SKU": "C1", "ListingStatus": "NotLive"},
                    ],
                },
                {
                    "ExternalProductId": "P2",
                    "ProductSKU": "P2",
                    "Title": "Parent two",
                    "BuyableProducts": [
                        {"ExternalBuyableProductID": "P2-X", "SKU": "P2-X", "ListingStatus": "Live"},
                        {"ExternalBuyableProductID": "P2-Y", "SKU": "P2-Y", "ListingStatus": "Live"},
                    ],
                },
                {
                    "ExternalProductId": "CAT1",
                    "ProductSKU": "CAT1",
                    "Title": "Catalog mapped",
                    "BuyableProducts": [
                        {"ExternalBuyableProductID": "CAT1", "SKU": "CAT1", "ListingStatus": "Live"},
                    ],
                },
            ],
        },
    )


def _empty_page():
    return MyDealResult(ok=True, status=200, response_status="Complete", data={"ResponseStatus": "Complete", "Data": []})


def _list_products(*, page=1, limit=250, listing_status="", fields=""):
    if listing_status == "Live" and page == 1:
        return _catalog_page()
    return _empty_page()


class MydealUnmappedDeleteTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="mdun", email="mdun@example.com", password="pw")
        mydeal, _ = Marketplace.objects.get_or_create(code="mydeal", defaults={"name": "MyDeal"})
        self.store = Store.objects.create(
            user=self.user,
            name="MyDeal Unmapped",
            region="AU",
            api_token="",
            marketplace=mydeal,
            management_mode="full_store",
            mydeal_setup_method="api",
            mydeal_environment="production",
            mydeal_production_base_url="https://universal-api.mydeal.com.au",
            mydeal_production_client_id="cid",
            mydeal_production_client_secret="csecret",
            mydeal_production_seller_id="sid",
            mydeal_production_seller_token="stoken",
        )
        self.mapped_listing = StoreListing.objects.create(
            user=self.user,
            store=self.store,
            sku="P1-A",
            external_product_key="P1",
            external_variant_key="P1-A",
        )
        StoreListing.objects.create(
            user=self.user,
            store=self.store,
            sku="P2-X",
            external_product_key="P2-X",
            external_variant_key="P2-X",
        )
        vendor = Vendor.objects.get(code="amazonau")
        product = Product.objects.create(owner=self.user, vendor=vendor, vendor_sku="VSKU-CAT")
        ProductMapping.objects.create(
            store=self.store,
            product=product,
            marketplace_child_sku="CAT1",
            store_price=Decimal("10.00"),
            store_stock=3,
            is_active=True,
        )
        for number, sku in enumerate(("P1-A", "P1-B", "C1", "CAT1"), start=1):
            MydealTemplateRow.objects.create(
                store=self.store,
                kind=MydealTemplateRow.Kind.INVENTORY,
                row_number=number,
                sku=sku,
                discontinued="FALSE",
            )

    def _discontinued(self, sku):
        return MydealTemplateRow.objects.get(
            store=self.store, kind=MydealTemplateRow.Kind.INVENTORY, sku=sku,
        ).discontinued

    @patch("listings.mydeal.client.MyDealClient")
    def test_unmapped_skus_skip_app_listings_and_catalog_mappings(self, mock_client_cls):
        mock_client_cls.return_value.list_products.side_effect = _list_products
        self.assertEqual(unmapped_delete.unmapped_skus(self.store), ["P1-B", "C1", "P2-Y"])

    @patch("listings.mydeal.client.MyDealClient")
    def test_template_lists_unmapped_skus(self, mock_client_cls):
        mock_client_cls.return_value.list_products.side_effect = _list_products
        resp = views._template_response(self.store, "delete", [], "unmapped")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.content.decode().splitlines(),
            ["Action,SKU", "Delete,P1-B", "Delete,C1", "Delete,P2-Y"],
        )

    @patch("listings.mydeal.client.MyDealClient")
    def test_delete_unmapped_zeros_stock_sets_notlive_and_discontinued(self, mock_client_cls):
        client = mock_client_cls.return_value
        client.list_products.side_effect = _list_products
        client.update_quantity_price.return_value = MyDealResult(ok=True)
        client.end_listings.return_value = MyDealResult(ok=True)
        content = (
            "Action,SKU\n"
            "Delete,P1-B\n"
            "Delete,P1-A\n"
            "Delete,CAT1\n"
            "Delete,NOT-THERE\n"
            "Delete,C1\n"
        ).encode()

        result = listing_service.bulk_import(
            self.user, self.store, "del.csv", content, action="delete", delete_scope="unmapped",
        )

        self.assertEqual(result["imported"], 2)
        by_sku = {row["sku"]: row for row in result["rows"]}
        self.assertTrue(by_sku["P1-B"]["imported"])
        self.assertTrue(by_sku["C1"]["imported"])
        self.assertIn("mapped in this app", by_sku["P1-A"]["errors"][0])
        self.assertIn("mapped in this app", by_sku["CAT1"]["errors"][0])
        self.assertIn("not found on MyDeal", by_sku["NOT-THERE"]["errors"][0])

        groups = client.update_quantity_price.call_args[0][0]
        p1 = next(group for group in groups if group["ProductSKU"] == "P1")
        by_sku = {buyable["SKU"]: buyable for buyable in p1["BuyableProducts"]}
        self.assertEqual(set(by_sku), {"P1-A", "P1-B"})
        self.assertEqual(by_sku["P1-B"]["Quantity"], 0)
        self.assertEqual(by_sku["P1-B"]["Price"], 11)
        self.assertEqual(by_sku["P1-B"]["RRP"], 13)
        self.assertEqual(by_sku["P1-A"]["Quantity"], 4)
        self.assertFalse(by_sku["P1-A"]["ProductUnlimited"])
        end_items = client.end_listings.call_args[0][0]
        self.assertEqual([item["buyable_sku"] for item in end_items], ["P1-B", "C1"])

        self.assertEqual(self._discontinued("P1-B"), "TRUE")
        self.assertEqual(self._discontinued("C1"), "TRUE")
        self.assertEqual(self._discontinued("P1-A"), "FALSE")
        self.assertEqual(self._discontinued("CAT1"), "FALSE")
        self.assertTrue(StoreListing.objects.filter(pk=self.mapped_listing.pk).exists())

    @patch("listings.mydeal.client.MyDealClient")
    def test_parent_with_mapped_variant_is_rejected(self, mock_client_cls):
        client = mock_client_cls.return_value
        client.list_products.side_effect = _list_products
        result = listing_service.bulk_import(
            self.user, self.store, "del.csv", b"Action,SKU\nDelete,P1\nDelete,P2\n",
            action="delete", delete_scope="unmapped",
        )
        self.assertEqual(result["imported"], 0)
        self.assertIn('"P1" is mapped in this app', result["rows"][0]["errors"][0])
        self.assertIn("variants mapped in this app (P2-X)", result["rows"][1]["errors"][0])
        client.update_quantity_price.assert_not_called()
        client.end_listings.assert_not_called()

    @patch("listings.mydeal.client.MyDealClient")
    def test_listing_status_error_keeps_discontinued_false(self, mock_client_cls):
        client = mock_client_cls.return_value
        client.list_products.side_effect = _list_products
        client.end_listings.return_value = MyDealResult(ok=False, message="MyDeal is down")
        result = listing_service.bulk_import(
            self.user, self.store, "del.csv", b"Action,SKU\nDelete,P1-B\n",
            action="delete", delete_scope="unmapped",
        )
        self.assertEqual(result["imported"], 0)
        self.assertEqual(result["rows"][0]["errors"], ["MyDeal is down"])
        client.update_quantity_price.assert_not_called()
        self.assertEqual(self._discontinued("P1-B"), "FALSE")

    @patch("listings.mydeal.client.MyDealClient")
    def test_stock_error_still_sets_notlive(self, mock_client_cls):
        client = mock_client_cls.return_value
        client.list_products.side_effect = _list_products
        client.end_listings.return_value = MyDealResult(ok=True)
        client.update_quantity_price.return_value = MyDealResult(ok=False, message="Price is required")
        result = listing_service.bulk_import(
            self.user, self.store, "del.csv", b"Action,SKU\nDelete,P1-B\n",
            action="delete", delete_scope="unmapped",
        )
        self.assertEqual(result["imported"], 1)
        upload = ListingUpload.objects.order_by("-created_at").first()
        self.assertIn("Stock was not changed", upload.message)
        self.assertIn("Price is required", upload.message)
        self.assertEqual(self._discontinued("P1-B"), "TRUE")

    @patch("listings.mydeal.client.MyDealClient")
    def test_listing_status_is_split_under_the_mydeal_limit(self, mock_client_cls):
        client = mock_client_cls.return_value
        entries = []
        for number in range(81):
            sku = f"U-{number}"
            entries.append({
                "sku": sku,
                "external_product_id": sku,
                "buyable_sku": sku,
                "external_buyable_id": sku,
                "group_keys": {sku.casefold()},
                "price": 5,
                "rrp": 6,
                "quantity": 1,
                "unlimited": False,
            })
        client.end_listings.return_value = MyDealResult(ok=True)
        client.update_quantity_price.return_value = MyDealResult(ok=True)
        rows = [{"row_number": number + 2, "sku": f"U-{number}"} for number in range(81)]
        with patch.object(unmapped_delete, "_catalog_entries", return_value=entries):
            preview, deleted, _message = unmapped_delete.delete_unmapped_rows(self.store, rows)
        self.assertEqual(deleted, 81)
        self.assertEqual(client.end_listings.call_count, 2)
        self.assertEqual(len(client.end_listings.call_args_list[0][0][0]), 80)
        self.assertEqual(len(client.end_listings.call_args_list[1][0][0]), 1)
        self.assertTrue(all(row["imported"] for row in preview))

    def test_upload_mode_store_marks_inventory_template_only(self):
        self.store.mydeal_setup_method = "upload"
        self.store.save(update_fields=["mydeal_setup_method"])
        with patch("listings.mydeal.client.MyDealClient") as mock_client_cls:
            self.assertEqual(unmapped_delete.unmapped_skus(self.store), ["P1-B", "C1"])
            result = listing_service.bulk_import(
                self.user, self.store, "del.csv", b"Action,SKU\nDelete,C1\nDelete,ZZZ\n",
                action="delete", delete_scope="unmapped",
            )
        mock_client_cls.assert_not_called()
        self.assertEqual(result["imported"], 1)
        self.assertIn("not on the MyDeal inventory template", result["rows"][1]["errors"][0])
        self.assertEqual(self._discontinued("C1"), "TRUE")

    def test_unmapped_scope_needs_delete_from_marketplace(self):
        with self.assertRaises(MarketplaceError):
            listing_service.bulk_import(
                self.user, self.store, "del.csv", b"Action,SKU\nDelete from system,C1\n",
                action="delete_system", delete_scope="unmapped",
            )

    def test_mapped_scope_keeps_existing_delete(self):
        with patch.object(listing_service, "_delete_listings_marketplace", return_value=1) as mock_md:
            result = listing_service.bulk_import(
                self.user, self.store, "del.csv", b"Action,SKU\nDelete,P1-A\nDelete,C1\n",
                action="delete", delete_scope="mapped",
            )
        mock_md.assert_called_once()
        self.assertEqual(result["imported"], 1)
        self.assertFalse(StoreListing.objects.filter(pk=self.mapped_listing.pk).exists())
        self.assertIn("Not mapped (on MyDeal only)", result["rows"][1]["errors"][0])

    @patch("listings.tasks.ingest_listing_bulk_upload.delay")
    def test_queue_passes_scope_to_worker(self, mock_delay):
        queued = listing_service.queue_bulk_import(
            self.user, self.store, "del.csv", b"Action,SKU\nDelete,C1\n",
            action="delete", delete_scope="unmapped",
        )
        mock_delay.assert_called_once_with(queued["upload_id"], "delete", "unmapped")
        self.assertEqual(ListingUpload.objects.get(pk=queued["upload_id"]).action, "delete")
