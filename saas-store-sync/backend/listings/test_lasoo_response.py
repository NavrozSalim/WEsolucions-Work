"""Tests for Lasoo Connect BulkUpsert / Search response parsing."""
from django.test import SimpleTestCase

from listings.lasoo.client import LasooResult
from listings.lasoo.queries import extract_lasoo_failure_message
from listings.lasoo.response import (
    advertised_from_row,
    collect_mapping_errors,
    collect_variant_rows,
    interpret_bulk_upsert,
    lookup_message,
    normalize_variant_hit,
)


class LasooResponseTests(SimpleTestCase):
    def test_clean_upsert_is_ok(self):
        result = LasooResult(
            ok=True,
            data={"success": True, "message": "No message", "results": {"success": True}},
            message="Success.",
        )
        ok, message, errors = interpret_bulk_upsert(result)
        self.assertTrue(ok)
        self.assertEqual(errors, [])
        self.assertIn("Success", message)

    def test_mapping_errors_fail_upsert(self):
        result = LasooResult(
            ok=True,
            data={
                "success": True,
                "results": {
                    "success": True,
                    "dataMappingErrors": [
                        {
                            "externalVariantKey": "SKU-1",
                            "errors": ["Category could not be mapped"],
                        }
                    ],
                },
            },
            message="ok",
        )
        ok, message, errors = interpret_bulk_upsert(result)
        self.assertFalse(ok)
        self.assertTrue(any("Category" in e for e in errors))
        self.assertIn("data mapping failed", message.lower())

    def test_boolean_mapping_flag_fails_upsert(self):
        result = LasooResult(
            ok=True,
            data={"results": {"success": True, "dataMappingErrors": True}},
            message="ok",
        )
        ok, _, errors = interpret_bulk_upsert(result)
        self.assertFalse(ok)
        self.assertTrue(errors)

    def test_results_success_false_fails_upsert(self):
        result = LasooResult(
            ok=True,
            data={"success": True, "results": {"success": False, "message": "Rejected"}},
            message="ok",
        )
        ok, message, _ = interpret_bulk_upsert(result)
        self.assertFalse(ok)
        self.assertIn("Rejected", message)

    def test_http_failure_still_fails(self):
        result = LasooResult(ok=False, message="Could not reach Lasoo.", data=None)
        ok, message, _ = interpret_bulk_upsert(result)
        self.assertFalse(ok)
        self.assertIn("Could not reach", message)

    def test_empty_mapping_errors_are_ok(self):
        result = LasooResult(
            ok=True,
            data={"results": {"success": True, "dataMappingErrors": []}},
            message="ok",
        )
        ok, _, errors = interpret_bulk_upsert(result)
        self.assertTrue(ok)
        self.assertEqual(errors, [])

    def test_collect_rows(self):
        rows = collect_variant_rows({
            "results": {"variants": [{"externalVariantKey": "V1"}]}
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["externalVariantKey"], "V1")

    def test_normalize_unknown_is_not_advertised(self):
        hit = normalize_variant_hit({
            "externalVariantKey": "SKU-1",
            "createdAt": "2026-08-28T18:02:00Z",
        })
        self.assertIsNone(hit["advertised"])
        self.assertEqual(hit["status"], "in seller catalog")

    def test_normalize_active_is_advertised(self):
        hit = normalize_variant_hit({
            "externalVariantKey": "SKU-1",
            "status": "Active",
        })
        self.assertTrue(hit["advertised"])
        self.assertEqual(hit["status"], "Active")

    def test_advertised_false_from_flag(self):
        self.assertFalse(advertised_from_row({"advertised": False}))
        self.assertTrue(advertised_from_row({"published": True}))

    def test_lookup_message_explains_seller_catalog(self):
        msg = lookup_message(found=True, advertised=None, mapping_errors=[])
        self.assertIn("seller inventory", msg.lower())
        self.assertIn("lasoo.com.au", msg.lower())

    def test_mapping_info_without_errors_is_clean(self):
        errors = collect_mapping_errors({
            "results": {
                "mappingInfo": {"mappedCount": 1, "sku": "SKU-1"},
                "variants": [{"externalVariantKey": "SKU-1", "sku": "SKU-1"}],
            }
        })
        self.assertEqual(errors, [])

    def test_extract_failure_from_results_success_false(self):
        msg = extract_lasoo_failure_message({
            "success": True,
            "results": {"success": False, "message": "Rejected by mapper"},
        })
        self.assertEqual(msg, "Rejected by mapper")

    def test_extract_failure_ignores_success_true(self):
        self.assertIsNone(extract_lasoo_failure_message({
            "success": True,
            "message": "No message",
            "results": {"success": True},
        }))
