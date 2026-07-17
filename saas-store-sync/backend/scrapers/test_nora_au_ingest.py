"""Unit tests for Nora AU inventory Excel pivot / Vendor ID cleaning."""
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from scrapers.nora_au_ingest import (
    build_nora_stock_map_from_rows,
    load_nora_stock_map_from_path,
    normalize_nora_vendor_id,
)


class NoraNormalizeTests(unittest.TestCase):
    def test_no_suffix_unchanged(self):
        self.assertEqual(normalize_nora_vendor_id("5GJJ07"), "5GJJ07")

    def test_g_suffixes_become_g1(self):
        self.assertEqual(normalize_nora_vendor_id("8FNZ100-DL-G1"), "8FNZ100-DL-G1")
        self.assertEqual(normalize_nora_vendor_id("8FNZ100-DL-G2"), "8FNZ100-DL-G1")
        self.assertEqual(normalize_nora_vendor_id("8FNZ100-DL-G3"), "8FNZ100-DL-G1")

    def test_v_suffixes_become_g1(self):
        self.assertEqual(normalize_nora_vendor_id("ABC-V1"), "ABC-G1")
        self.assertEqual(normalize_nora_vendor_id("ABC-V2"), "ABC-G1")
        self.assertEqual(normalize_nora_vendor_id("ABC-V3"), "ABC-G1")

    def test_blank(self):
        self.assertEqual(normalize_nora_vendor_id(""), "")
        self.assertEqual(normalize_nora_vendor_id("  "), "")


class NoraPivotTests(unittest.TestCase):
    def test_exact_duplicates_summed_then_suffix_merged(self):
        # Header + rows mimicking Export inventory (col 0 barcode, col 10 stock)
        def row(barcode, stock):
            cells = [None] * 11
            cells[0] = barcode
            cells[10] = stock
            return cells

        rows = [
            row("8FNZ100-DL-G1", 0),
            row("8FNZ100-DL-G1", 0),
            row("8FNZ100-DL-G2", 0),
            row("8FNZ100-DL-G3", 0),
            row("8FNZ100-DL-G3", 0),
            row("8FNZ100-DL-G3", 15),
            row("5GJJ07", 3),
            row("5GJJ07", 2),
        ]
        result = build_nora_stock_map_from_rows(rows)
        self.assertEqual(result["8FNZ100-DL-G1"], 15)
        self.assertEqual(result["5GJJ07"], 5)
        self.assertNotIn("8FNZ100-DL-G2", result)
        self.assertNotIn("8FNZ100-DL-G3", result)

    def test_load_from_xlsx_export_inventory_sheet(self):
        wb = Workbook()
        # First sheet unused
        ws1 = wb.active
        ws1.title = "Sheet1"
        ws = wb.create_sheet("Export inventory")
        header = [""] * 13
        header[0] = "BarCode"
        header[1] = "Product Title"
        header[10] = "Available inventory"
        ws.append(header)
        ws.append(["ABC-G2", "t", 0, 0, 0, 0, 0, 0, 0, 0, 4])
        ws.append(["ABC-G3", "t", 0, 0, 0, 0, 0, 0, 0, 0, 6])
        ws.append(["PLAIN", "t", 0, 0, 0, 0, 0, 0, 0, 0, 1])

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nora.xlsx"
            wb.save(path)
            result = load_nora_stock_map_from_path(path)

        self.assertEqual(result["ABC-G1"], 10)
        self.assertEqual(result["PLAIN"], 1)


if __name__ == "__main__":
    unittest.main()
