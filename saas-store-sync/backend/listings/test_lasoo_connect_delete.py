"""Tests for Lasoo Connect delete (BulkDelete + Search verify)."""
from unittest.mock import MagicMock

from django.test import SimpleTestCase

from listings.errors import MarketplaceError
from listings.lasoo.client import LasooResult
from listings.lasoo.connect_delete import delete_connect_variants


def _search_body(found: bool, key: str = "HW-ZZ122-G2") -> dict:
    variants = (
        [{"externalProductKey": key, "externalVariantKey": key, "sku": key}]
        if found else []
    )
    return {"results": {"success": True, "variants": variants, "count": len(variants)}}


class ConnectDeleteTests(SimpleTestCase):
    def test_skips_bulk_delete_when_search_empty(self):
        client = MagicMock()
        client.auth_key = "key"
        client.send.return_value = LasooResult(ok=True, data=_search_body(False))
        removed = delete_connect_variants(
            client,
            [{"product_key": "HW-ZZ122-G2", "variant_key": "HW-ZZ122-G2", "sku": "HW-ZZ122-G2"}],
        )
        self.assertEqual(removed, 0)
        self.assertTrue(all(c[0][0] == "variants_search" for c in client.send.call_args_list))

    def test_search_keys_shape_then_gone_is_success(self):
        client = MagicMock()
        client.auth_key = "key"
        catalog = {"HW-ZZ122-G2"}

        def send(endpoint, payload=None, *args, **kwargs):
            data = (payload or {}).get("data") or {}
            if endpoint == "variants_search":
                key = data.get("externalVariantKey")
                return LasooResult(ok=True, data=_search_body(key in catalog, key or "HW-ZZ122-G2"))
            catalog.clear()
            return LasooResult(ok=True, data={"success": True})

        client.send.side_effect = send
        removed = delete_connect_variants(
            client,
            [{"product_key": "HW-ZZ122-G2", "variant_key": "HW-ZZ122-G2", "sku": "HW-ZZ122-G2"}],
        )
        self.assertEqual(removed, 1)
        delete_calls = [c for c in client.send.call_args_list if c[0][0] == "bulk_delete"]
        self.assertEqual(len(delete_calls), 1)
        data = delete_calls[0][0][1]["data"]
        self.assertEqual(data["externalProductKey"], "HW-ZZ122-G2")
        self.assertEqual(data["externalVariantKey"], "HW-ZZ122-G2")

    def test_later_shape_used_when_first_crashes(self):
        client = MagicMock()
        client.auth_key = "key"
        catalog = {"HW-ZZ122-G2"}

        def send(endpoint, payload=None, *args, **kwargs):
            data = (payload or {}).get("data") or {}
            if endpoint == "variants_search":
                key = data.get("externalVariantKey") or "HW-ZZ122-G2"
                return LasooResult(ok=True, data=_search_body(key in catalog, key))
            if isinstance(data.get("variants"), list) and data["variants"] and isinstance(data["variants"][0], dict):
                catalog.clear()
                return LasooResult(ok=True, data={"success": True})
            return LasooResult(
                ok=False,
                message="Cannot read properties of undefined (reading 'map')",
                data={},
            )

        client.send.side_effect = send
        removed = delete_connect_variants(
            client,
            [{"product_key": "HW-ZZ122-G2", "variant_key": "HW-ZZ122-G2", "sku": "HW-ZZ122-G2"}],
        )
        self.assertEqual(removed, 1)
        delete_calls = [c for c in client.send.call_args_list if c[0][0] == "bulk_delete"]
        self.assertGreaterEqual(len(delete_calls), 2)

    def test_raises_when_connect_still_has_sku(self):
        client = MagicMock()
        client.auth_key = "key"
        client.send.side_effect = lambda endpoint, payload=None, *args, **kwargs: (
            LasooResult(ok=True, data=_search_body(True))
            if endpoint == "variants_search"
            else LasooResult(
                ok=False,
                message="Cannot read properties of undefined (reading 'map')",
                data={},
            )
        )
        with self.assertRaises(MarketplaceError) as ctx:
            delete_connect_variants(
                client,
                [{"product_key": "HW-ZZ122-G2", "variant_key": "HW-ZZ122-G2", "sku": "HW-ZZ122-G2"}],
            )
        self.assertIn("still has SKU", str(ctx.exception))
        self.assertIn("HW-ZZ122-G2", str(ctx.exception))

    def test_raises_when_search_fails(self):
        client = MagicMock()
        client.auth_key = "key"
        client.send.return_value = LasooResult(ok=False, message="timeout", data={})
        with self.assertRaises(MarketplaceError) as ctx:
            delete_connect_variants(
                client,
                [{"product_key": "X", "variant_key": "X", "sku": "X"}],
            )
        self.assertIn("Could not verify SKU", str(ctx.exception))
