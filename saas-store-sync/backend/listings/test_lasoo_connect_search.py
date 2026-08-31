"""Tests for Lasoo Connect Variants_Search matching (no false positives)."""
from unittest.mock import MagicMock

from django.test import SimpleTestCase

from listings.lasoo.client import LasooResult
from listings.lasoo.connect_search import keys_match, search_variant


class ConnectSearchTests(SimpleTestCase):
    def test_keys_match_is_case_insensitive(self):
        hit = {"product_key": "U-Z1618", "variant_key": "U-Z1618", "sku": "U-Z1618"}
        self.assertTrue(keys_match(hit, product_key="u-z1618", variant_key="u-z1618", sku="u-z1618"))

    def test_keys_do_not_match_other_sku(self):
        hit = {"product_key": "OTHER", "variant_key": "OTHER", "sku": "OTHER"}
        self.assertFalse(keys_match(hit, product_key="U-Z1618", variant_key="U-Z1618", sku="U-Z1618"))

    def test_empty_variants_is_not_found(self):
        client = MagicMock()
        client.auth_key = "key"
        client.send.return_value = LasooResult(
            ok=True,
            data={"complete": True, "success": True, "results": {"success": True, "variants": [], "count": 0}},
            message="ok",
        )
        result = search_variant(client, product_key="U-Z1618", variant_key="U-Z1618", sku="U-Z1618")
        self.assertTrue(result["ok"])
        self.assertFalse(result["found"])
        self.assertIsNone(result["hit"])

    def test_unrelated_row_is_not_found(self):
        client = MagicMock()
        client.auth_key = "key"
        client.send.return_value = LasooResult(
            ok=True,
            data={
                "results": {
                    "variants": [
                        {"externalProductKey": "OTHER", "externalVariantKey": "OTHER"},
                    ],
                },
            },
            message="ok",
        )
        result = search_variant(client, product_key="U-Z1618", variant_key="U-Z1618", sku="U-Z1618")
        self.assertTrue(result["ok"])
        self.assertFalse(result["found"])

    def test_matching_row_is_found(self):
        client = MagicMock()
        client.auth_key = "key"
        client.send.return_value = LasooResult(
            ok=True,
            data={
                "results": {
                    "variants": [
                        {
                            "externalProductKey": "U-Z1618",
                            "externalVariantKey": "U-Z1618",
                            "dataPublishedAt": None,
                            "dataMappingErrors": "No image URLs",
                        },
                    ],
                },
            },
            message="ok",
        )
        result = search_variant(client, product_key="U-Z1618", variant_key="U-Z1618", sku="U-Z1618")
        self.assertTrue(result["ok"])
        self.assertTrue(result["found"])
        self.assertFalse(result["advertised"])
        self.assertTrue(any("image" in e.lower() for e in result["mapping_errors"]))

    def test_api_failure_is_not_found(self):
        client = MagicMock()
        client.auth_key = "key"
        client.send.return_value = LasooResult(ok=False, message="Auth failed", data=None)
        result = search_variant(client, product_key="A", variant_key="A", sku="A")
        self.assertFalse(result["ok"])
        self.assertFalse(result["found"])
        self.assertIn("could not verify", result["message"].lower())
