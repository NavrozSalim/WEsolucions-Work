"""Unit tests for Wallkoala Excel/CSV ingest (SKU, Vendor Price, Vendor Inventory)."""
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from scrapers.wallkoala_ingest import (
    build_wallkoala_feed_from_rows,
    is_wallkoala_vendor_code,
    load_wallkoala_feed_from_path,
    lookup_wallkoala_entry,
)


class WallkoalaVendorCodeTests(unittest.TestCase):
    def test_codes(self):
        self.assertTrue(is_wallkoala_vendor_code("wallkoala"))
        self.assertTrue(is_wallkoala_vendor_code("Wall Koala"))
        self.assertTrue(is_wallkoala_vendor_code("wallkoalaau"))
        self.assertFalse(is_wallkoala_vendor_code("noraau"))
        self.assertFalse(is_wallkoala_vendor_code("wk"))
        self.assertFalse(is_wallkoala_vendor_code("costwayau"))


class WallkoalaFeedTests(unittest.TestCase):
    def test_headers_ignore_shipping(self):
        rows = [
            ["SKU", "Vendor Price", "Shipping Price", "Vendor Inventory"],
            ["WK022-ST-40X30CM", 10.50, 5.00, 12],
            ["WK099", "$8.00", "2", "3"],
        ]
        feed = build_wallkoala_feed_from_rows(rows)
        entry = lookup_wallkoala_entry(feed, "WK022-ST-40X30CM")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["price"], 10.50)
        self.assertEqual(entry["inventory"], 12)
        other = lookup_wallkoala_entry(feed, "wk099")
        self.assertEqual(other["price"], 8.0)
        self.assertEqual(other["inventory"], 3)

    def test_positional_fallback_skips_shipping_column(self):
        rows = [
            ["WK022-ST-40X30CM", 19.95, 4.50, 8],
            ["WK023", 7, 99, 1],
        ]
        feed = build_wallkoala_feed_from_rows(rows)
        entry = lookup_wallkoala_entry(feed, "WK022-ST-40X30CM")
        self.assertEqual(entry["price"], 19.95)
        self.assertEqual(entry["inventory"], 8)
        self.assertNotEqual(entry["price"], 19.95 + 4.50)

    def test_duplicate_sku_last_wins(self):
        rows = [
            ["SKU", "Vendor Price", "Shipping Price", "Vendor Inventory"],
            ["WK-1", 10, 1, 2],
            ["WK-1", 15, 1, 9],
        ]
        feed = build_wallkoala_feed_from_rows(rows)
        self.assertEqual(lookup_wallkoala_entry(feed, "WK-1")["price"], 15)
        self.assertEqual(lookup_wallkoala_entry(feed, "WK-1")["inventory"], 9)

    def test_load_xlsx(self):
        wb = Workbook()
        ws = wb.active
        ws.append(["SKU", "Vendor Price", "Shipping Price", "Vendor Inventory"])
        ws.append(["WK-X", 22.0, 3.0, 4])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wallkoala.xlsx"
            wb.save(path)
            feed = load_wallkoala_feed_from_path(path)
        self.assertEqual(lookup_wallkoala_entry(feed, "WK-X")["price"], 22.0)
        self.assertEqual(lookup_wallkoala_entry(feed, "WK-X")["inventory"], 4)

    def test_missing_sku_returns_none(self):
        feed = build_wallkoala_feed_from_rows([
            ["SKU", "Vendor Price", "Shipping Price", "Vendor Inventory"],
            ["WK-1", 1, 0, 1],
        ])
        self.assertIsNone(lookup_wallkoala_entry(feed, "NOPE"))
