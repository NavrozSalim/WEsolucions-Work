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
        self.assertEqual(result["marketplace"], "lasoo")
        self.assertEqual(result["results"][0]["status"], "Active")
        self.assertEqual(result["results"][0]["created_at"], "2026-01-15T10:00:00Z")
        self.assertIsNotNone(result["local_listing"])

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
        self.assertIn("SKU,Found,Marketplace Status", text)
        self.assertIn("FOUND,Yes,Active", text)
        self.assertIn("MISS,No,", text)
        self.assertIn("ERR,Error,", text)

    def test_bulk_rejects_empty(self):
        store = self._lasoo_store()
        with self.assertRaises(MarketplaceError):
            marketplace_lookup.lookup_skus_bulk(store, [])
