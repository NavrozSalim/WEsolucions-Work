"""Tests for lasoo_check_variant helpers."""
from django.test import SimpleTestCase

from listings.management.commands.lasoo_check_variant import (
    _collect_result_rows,
    _row_keys,
)


class LasooCheckVariantHelpersTests(SimpleTestCase):
    def test_collect_rows_from_results_variants(self):
        body = {
            "results": {
                "variants": [
                    {"externalProductKey": "P1", "externalVariantKey": "V1"},
                ]
            }
        }
        rows = _collect_result_rows(body)
        self.assertEqual(len(rows), 1)
        self.assertEqual(_row_keys(rows[0]), ("P1", "V1"))

    def test_collect_empty(self):
        self.assertEqual(_collect_result_rows({"results": {"variants": []}}), [])
        self.assertEqual(_collect_result_rows(None), [])
