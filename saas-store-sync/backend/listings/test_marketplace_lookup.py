"""Tests for managed-store marketplace SKU lookup."""
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from marketplace.models import Marketplace
from stores.models import Store

from . import marketplace_lookup
from .errors import MarketplaceError
from .lasoo.client import LasooResult
from .models import ListingStatus, StoreListing
from .mydeal.client import MyDealResult


class MarketplaceLookupTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="mplook", email="mpl@example.com", password="pw")

    def _lasoo_store(self):
        lasoo, _ = Marketplace.objects.get_or_create(code="lasoo", defaults={"name": "Lasoo"})
        return Store.objects.create(
            user=self.user,
            name="Lasoo Lookup",
            region="AU",
            api_token="",
            marketplace=lasoo,
            management_mode="full_store",
            lasoo_environment="staging",
            lasoo_staging_auth_key="test-key",
        )

    def _reverb_store(self):
        reverb, _ = Marketplace.objects.get_or_create(code="reverb", defaults={"name": "Reverb"})
        return Store.objects.create(
            user=self.user,
            name="Reverb Lookup",
            region="USA",
            api_token="reverb-token-1234567890",
            marketplace=reverb,
            management_mode="full_store",
        )

    def _mydeal_store(self):
        mydeal, _ = Marketplace.objects.get_or_create(code="mydeal", defaults={"name": "MyDeal"})
        return Store.objects.create(
            user=self.user,
            name="MyDeal Lookup",
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

    def _mydeal_product(self, *, listing_status="Pending", approved=False):
        return {
            "ResponseStatus": "Complete",
            "Data": {
                "ExternalProductId": "SHD-10001-01",
                "ProductSKU": "SHD-10001-01",
                "Title": "Azure Reverie canvas",
                "BuyableProducts": [
                    {
                        "ExternalBuyableProductID": "SHD-10001-01-01",
                        "SKU": "SHD-10001-01-01",
                        "Price": 108.99,
                        "Quantity": 2,
                        "ListingStatus": listing_status,
                        "Approved": approved,
                        "Active": True,
                    }
                ],
            },
        }

    @patch("listings.marketplace_lookup.LasooClient")
    def test_lasoo_lookup_found(self, mock_client_cls):
        store = self._lasoo_store()
        StoreListing.objects.create(
            user=self.user,
            store=store,
            external_product_key="SKU-1",
            external_variant_key="SKU-1",
            sku="SKU-1",
            title="Thing",
            description="d",
            brand="b",
            image_urls="https://img.example.com/a.jpg",
            original_price="10",
            sale_price="9",
            status=ListingStatus.READY,
        )
        mock_client = MagicMock()
        mock_client.auth_key = "test-key"
        mock_client.environment = "staging"
        mock_client.send.return_value = LasooResult(
            ok=True,
            data={
                "results": {
                    "variants": [
                        {
                            "externalProductKey": "SKU-1",
                            "externalVariantKey": "SKU-1",
                            "status": "Active",
                            "createdAt": "2026-01-15T10:00:00Z",
                            "title": "Thing",
                        }
                    ]
                }
            },
            message="ok",
            status=200,
        )
        mock_client_cls.return_value = mock_client

        result = marketplace_lookup.lookup_sku(store, "SKU-1")
        self.assertTrue(result["ok"])
        self.assertTrue(result["found"])
        self.assertTrue(result["advertised"])
        self.assertEqual(result["marketplace"], "lasoo")
        self.assertEqual(result["results"][0]["status"], "Active")
        self.assertEqual(result["results"][0]["created_at"], "2026-01-15T10:00:00Z")
        self.assertIsNotNone(result["local_listing"])
        self.assertIn("advertised", result["message"].lower())

    @patch("listings.marketplace_lookup.LasooClient")
    def test_lasoo_lookup_not_found(self, mock_client_cls):
        store = self._lasoo_store()
        mock_client = MagicMock()
        mock_client.auth_key = "test-key"
        mock_client.environment = "staging"
        mock_client.send.return_value = LasooResult(
            ok=True,
            data={"results": {"variants": []}},
            message="ok",
            status=200,
        )
        mock_client_cls.return_value = mock_client

        result = marketplace_lookup.lookup_sku(store, "MISSING")
        self.assertTrue(result["ok"])
        self.assertFalse(result["found"])
        self.assertEqual(result["results"], [])

    @patch("listings.marketplace_lookup.LasooClient")
    def test_lasoo_lookup_ignores_unrelated_variant(self, mock_client_cls):
        store = self._lasoo_store()
        mock_client = MagicMock()
        mock_client.auth_key = "test-key"
        mock_client.environment = "staging"
        mock_client.send.return_value = LasooResult(
            ok=True,
            data={
                "results": {
                    "variants": [
                        {"externalProductKey": "OTHER", "externalVariantKey": "OTHER"},
                    ]
                }
            },
            message="ok",
            status=200,
        )
        mock_client_cls.return_value = mock_client
        result = marketplace_lookup.lookup_sku(store, "U-Z1618")
        self.assertTrue(result["ok"])
        self.assertFalse(result["found"])
        self.assertEqual(result["results"], [])

    @patch("listings.marketplace_lookup.LasooClient")
    def test_lasoo_lookup_found_but_not_advertised(self, mock_client_cls):
        store = self._lasoo_store()
        mock_client = MagicMock()
        mock_client.auth_key = "test-key"
        mock_client.environment = "production"
        mock_client.send.return_value = LasooResult(
            ok=True,
            data={
                "results": {
                    "variants": [
                        {
                            "externalProductKey": "LJR-1",
                            "externalVariantKey": "LJR-1",
                            "createdAt": "2026-08-28T18:02:00Z",
                        }
                    ]
                }
            },
            message="ok",
            status=200,
        )
        mock_client_cls.return_value = mock_client

        result = marketplace_lookup.lookup_sku(store, "LJR-1")
        self.assertTrue(result["found"])
        self.assertIsNone(result["advertised"])
        self.assertEqual(result["results"][0]["status"], "in seller catalog")
        self.assertIn("seller inventory", result["message"].lower())
        payload = mock_client.send.call_args[0][1]
        data = payload.get("data") or {}
        self.assertNotIn("dataMappingErrors", data)
        self.assertTrue(data.get("returnMappingInfo"))

    @patch("listings.marketplace_lookup.get_adapter")
    def test_reverb_lookup_found(self, mock_get_adapter):
        store = self._reverb_store()
        adapter = MagicMock()
        adapter.find_listings_by_sku.return_value = [
            {
                "id": "abc-123",
                "sku": "PEDAL-1",
                "title": "Pedal",
                "state": {"slug": "live"},
                "created_at": "2026-02-01T12:00:00Z",
                "published_at": "2026-02-01T13:00:00Z",
                "_links": {"web": {"href": "https://reverb.com/item/1"}},
            }
        ]
        mock_get_adapter.return_value = adapter

        result = marketplace_lookup.lookup_sku(store, "PEDAL-1")
        self.assertTrue(result["found"])
        self.assertEqual(result["marketplace"], "reverb")
        self.assertEqual(result["results"][0]["status"], "live")
        self.assertEqual(result["results"][0]["created_at"], "2026-02-01T12:00:00Z")
        self.assertEqual(result["results"][0]["published_at"], "2026-02-01T13:00:00Z")

    def test_store_listing_skus_uses_hub_rows(self):
        store = self._lasoo_store()
        StoreListing.objects.create(
            user=self.user,
            store=store,
            external_product_key="A-1",
            external_variant_key="A-1",
            sku="A-1",
            title="A",
            description="d",
            brand="b",
            image_urls="https://img.example.com/a.jpg",
            original_price="10",
            sale_price="9",
        )
        StoreListing.objects.create(
            user=self.user,
            store=store,
            external_product_key="B-1",
            external_variant_key="B-1",
            sku="B-1",
            title="B",
            description="d",
            brand="b",
            image_urls="https://img.example.com/a.jpg",
            original_price="10",
            sale_price="9",
        )
        self.assertEqual(marketplace_lookup.store_listing_skus(store), ["A-1", "B-1"])

    def test_rejects_blank_sku(self):
        store = self._lasoo_store()
        with self.assertRaises(MarketplaceError):
            marketplace_lookup.lookup_sku(store, "  ")

    def test_parse_sku_list_dedupes(self):
        skus = marketplace_lookup.parse_sku_list("A\nB, a;B\nC")
        self.assertEqual(skus, ["A", "B", "C"])

    def test_parse_skus_from_csv_with_header(self):
        content = b"SKU,Title\nAAA,One\nBBB,Two\n"
        skus = marketplace_lookup.parse_skus_from_file(content, filename="skus.csv")
        self.assertEqual(skus, ["AAA", "BBB"])

    def test_parse_skus_from_txt(self):
        content = b"X1\nX2\nX1\n"
        skus = marketplace_lookup.parse_skus_from_file(content, filename="list.txt")
        self.assertEqual(skus, ["X1", "X2"])

    @patch("listings.marketplace_lookup.lookup_sku")
    def test_bulk_lookup_summary_and_csv(self, mock_lookup):
        store = self._lasoo_store()

        def _side_effect(_store, sku):
            if sku == "FOUND":
                return {
                    "ok": True,
                    "found": True,
                    "marketplace": "lasoo",
                    "environment": "staging",
                    "message": "Found on Lasoo.",
                    "results": [
                        {
                            "sku": "FOUND",
                            "status": "Active",
                            "advertised": True,
                            "created_at": "2026-01-01T00:00:00Z",
                            "title": "Yes",
                            "marketplace_id": "1",
                            "url": "https://example.com/1",
                        }
                    ],
                    "local_listing": {"status": "ready", "created_at": "2026-01-02T00:00:00Z"},
                }
            if sku == "ERR":
                raise MarketplaceError("boom")
            return {
                "ok": True,
                "found": False,
                "marketplace": "lasoo",
                "environment": "staging",
                "message": "Not found.",
                "results": [],
                "local_listing": None,
            }

        mock_lookup.side_effect = _side_effect
        result = marketplace_lookup.lookup_skus_bulk(store, ["FOUND", "MISS", "ERR"])
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["found"], 1)
        self.assertEqual(result["not_found"], 1)
        self.assertEqual(result["errors"], 1)
        self.assertEqual(result["rows"][0]["found"], "Yes")
        self.assertEqual(result["rows"][1]["found"], "No")
        self.assertEqual(result["rows"][2]["found"], "Error")

        csv_bytes = marketplace_lookup.build_lookup_csv(result)
        text = csv_bytes.decode("utf-8-sig")
        self.assertIn("SKU,Found,Advertised / Live,Marketplace Status", text)
        self.assertIn("FOUND,Yes,Yes,Active", text)
        self.assertIn("MISS,No,", text)
        self.assertIn("ERR,Error,", text)

    def test_bulk_rejects_over_batch_limit(self):
        store = self._lasoo_store()
        too_many = [f"SKU-{i}" for i in range(marketplace_lookup.BULK_MAX_SKUS + 1)]
        with self.assertRaises(MarketplaceError):
            marketplace_lookup.lookup_skus_bulk(store, too_many)

    def test_bulk_rejects_empty(self):
        store = self._lasoo_store()
        with self.assertRaises(MarketplaceError):
            marketplace_lookup.lookup_skus_bulk(store, [])

    @patch("listings.marketplace_lookup.lookup_sku")
    def test_async_job_writes_progress_and_csv(self, mock_lookup):
        from . import marketplace_lookup_progress as prog

        store = self._lasoo_store()

        def _side_effect(_store, sku):
            return {
                "ok": True,
                "found": sku == "A",
                "marketplace": "lasoo",
                "environment": "staging",
                "message": "ok",
                "results": (
                    [{"sku": "A", "status": "Active", "advertised": True, "created_at": "2026-01-01T00:00:00Z", "title": "A"}]
                    if sku == "A"
                    else []
                ),
                "local_listing": None,
            }

        mock_lookup.side_effect = _side_effect
        prog.begin_lookup_progress(store.id, skus=["A", "B"])
        marketplace_lookup.run_marketplace_lookup_job(str(store.id), ["A", "B"])
        data = prog.get_lookup_progress(store.id)
        self.assertFalse(data["active"])
        self.assertEqual(data["status"], "done")
        self.assertEqual(data["found"], 1)
        self.assertEqual(data["not_found"], 1)
        self.assertTrue(prog.public_lookup_progress(store.id)["has_results"])
        csv_bytes = marketplace_lookup.build_lookup_csv({"rows": data["rows"]})
        self.assertIn(b"A,Yes,Yes,Active", csv_bytes)

    @patch("listings.marketplace_lookup.lookup_sku")
    def test_async_job_respects_cancel(self, mock_lookup):
        from . import marketplace_lookup_progress as prog

        store = self._lasoo_store()
        calls = {"n": 0}

        def _side_effect(_store, sku):
            calls["n"] += 1
            if calls["n"] == 1:
                prog.request_lookup_cancel(store.id)
            return {
                "ok": True,
                "found": False,
                "marketplace": "lasoo",
                "environment": "staging",
                "message": "nf",
                "results": [],
                "local_listing": None,
            }

        mock_lookup.side_effect = _side_effect
        prog.begin_lookup_progress(store.id, skus=["X", "Y", "Z"])
        marketplace_lookup.run_marketplace_lookup_job(str(store.id), ["X", "Y", "Z"])
        data = prog.get_lookup_progress(store.id)
        self.assertEqual(data["status"], "cancelled")
        self.assertFalse(data["active"])
        self.assertGreaterEqual(len(data["rows"]), 1)
        self.assertLess(len(data["rows"]), 3)

    def _seed_mydeal_listing(self, store):
        return StoreListing.objects.create(
            user=self.user,
            store=store,
            external_product_key="SHD-10001-01",
            external_variant_key="SHD-10001-01-01",
            sku="SHD-10001-01-01",
            title="Azure Reverie canvas",
            description="d",
            brand="Shemaya",
            image_urls="https://img.example.com/a.jpg",
            original_price="145.99",
            sale_price="108.99",
            status=ListingStatus.UPLOADED_PRODUCTION,
            environment="production",
        )

    @patch("listings.mydeal.client.MyDealClient")
    def test_mydeal_lookup_found_pending_not_live(self, mock_client_cls):
        store = self._mydeal_store()
        self._seed_mydeal_listing(store)
        mock_client = MagicMock()
        mock_client.environment = "production"
        mock_client.get_product.return_value = MyDealResult(
            ok=True,
            data=self._mydeal_product(listing_status="Pending", approved=False),
            message="MyDeal response: Complete.",
            status=200,
            response_status="Complete",
        )
        mock_client_cls.return_value = mock_client

        result = marketplace_lookup.lookup_sku(store, "SHD-10001-01-01")
        self.assertTrue(result["ok"])
        self.assertTrue(result["found"])
        self.assertFalse(result["advertised"])
        self.assertEqual(result["marketplace"], "mydeal")
        self.assertEqual(result["environment"], "production")
        self.assertEqual(result["results"][0]["status"], "Pending")
        self.assertEqual(result["results"][0]["sku"], "SHD-10001-01-01")
        self.assertEqual(result["results"][0]["product_key"], "SHD-10001-01")
        self.assertIn("pending", result["message"].lower())
        mock_client.get_product.assert_called_with("SHD-10001-01", by="sku")

    @patch("listings.mydeal.client.MyDealClient")
    def test_mydeal_lookup_found_live(self, mock_client_cls):
        store = self._mydeal_store()
        mock_client = MagicMock()
        mock_client.environment = "production"
        mock_client.get_product.return_value = MyDealResult(
            ok=True,
            data=self._mydeal_product(listing_status="Live", approved=True),
            message="ok",
            status=200,
            response_status="Complete",
        )
        mock_client_cls.return_value = mock_client

        result = marketplace_lookup.lookup_sku(store, "SHD-10001-01")
        self.assertTrue(result["found"])
        self.assertTrue(result["advertised"])
        self.assertEqual(result["results"][0]["status"], "Live")
        self.assertIn("live", result["message"].lower())

    @patch("listings.mydeal.client.MyDealClient")
    def test_mydeal_lookup_not_found(self, mock_client_cls):
        store = self._mydeal_store()
        mock_client = MagicMock()
        mock_client.environment = "production"
        mock_client.get_product.return_value = MyDealResult(
            ok=False,
            data={"ResponseStatus": "Failed", "Errors": [{"ID": "ProductNotFound", "Message": "Not found"}]},
            message="Product not found",
            status=200,
            response_status="Failed",
        )
        mock_client_cls.return_value = mock_client

        result = marketplace_lookup.lookup_sku(store, "MISSING-SKU")
        self.assertTrue(result["ok"])
        self.assertFalse(result["found"])
        self.assertEqual(result["results"], [])
        self.assertIn("not found", result["message"].lower())

    @patch("listings.mydeal.client.MyDealClient")
    def test_mydeal_lookup_complete_with_product_not_found_errors(self, mock_client_cls):
        store = self._mydeal_store()
        mock_client = MagicMock()
        mock_client.environment = "production"
        mock_client.get_product.return_value = MyDealResult(
            ok=False,
            data={
                "ResponseStatus": "Complete",
                "Errors": [{"ID": "ProductNotFound", "Code": "302", "Message": "Product(s) not found in marketplace. "}],
            },
            message="Product(s) not found in marketplace.",
            status=200,
            response_status="Complete",
        )
        mock_client_cls.return_value = mock_client
        result = marketplace_lookup.lookup_sku(store, "PD1-50X50")
        self.assertTrue(result["ok"])
        self.assertFalse(result["found"])

    def test_mydeal_groups_from_data_list(self):
        groups = marketplace_lookup._mydeal_groups(
            {
                "ResponseStatus": "Complete",
                "Data": [{"ProductSKU": "WK002", "Title": "Midnight Horizon"}],
            }
        )
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["ProductSKU"], "WK002")

    def test_mydeal_groups_ignores_empty_data_dict(self):
        self.assertEqual(
            marketplace_lookup._mydeal_groups({"ResponseStatus": "Complete", "Data": {}}),
            [],
        )

    def test_mydeal_lookup_missing_credentials(self):
        store = self._mydeal_store()
        store.mydeal_production_client_id = ""
        store.save(update_fields=["mydeal_production_client_id"])
        with self.assertRaises(MarketplaceError) as ctx:
            marketplace_lookup.lookup_sku(store, "SHD-10001-01")
        self.assertIn("ClientID", str(ctx.exception))

    def test_unsupported_kind_still_rejected(self):
        etsy, _ = Marketplace.objects.get_or_create(code="etsy", defaults={"name": "Etsy"})
        store = Store.objects.create(
            user=self.user,
            name="Etsy Lookup",
            region="USA",
            api_token="tok",
            marketplace=etsy,
            management_mode="full_store",
        )
        with self.assertRaises(MarketplaceError) as ctx:
            marketplace_lookup.lookup_sku(store, "SKU-1")
        self.assertIn("not supported", str(ctx.exception).lower())
