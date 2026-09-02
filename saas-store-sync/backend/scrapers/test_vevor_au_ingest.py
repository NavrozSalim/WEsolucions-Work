"""Unit tests for the Vevor AU feed parser (run: python -m unittest scrapers.test_vevor_au_ingest -v).

The live feed uses ``after coupon price`` (Excel col 35 / index 34) for cost,
with ``MAP (Minimum Advertised Price)`` beside it; stock is ``Inventory
quantity``. The legacy feed used A=SKU, G=Price, I=Inventory. Both layouts
must parse correctly.
"""
import os
import tempfile
import unittest

from scrapers.vevor_au_ingest import (
    LEGACY_INVENTORY_COL,
    LEGACY_PRICE_COL,
    LEGACY_SKU_COL,
    is_vevor_product_url,
    is_vevor_vendor_code,
    load_veror_via_excel_positions,
    lookup_sku,
    lookup_vevor_price_stock,
    normalize_vevor_product_url,
    resolve_vevor_feed_columns,
    vevor_identity_candidates,
)

# Exact header of the live vevor-563.xlsx feed as of July 2026.
CURRENT_FEED_HEADER = [
    'SKU', 'Country', 'Product title', 'Product description', 'Product link',
    'Product condition', 'Availability', 'Inventory quantity',
    'Product weight(KG)', 'Image link', 'Brand', 'Product type',
    'goods_original_picture', 'goods_main_original_picture',
    'Selling point 5', 'Selling point 4', 'Selling point 3',
    'Selling point 2', 'Selling point 1', 'attribute_name_2',
    'attribute_name_1', 'attribute_2', 'attribute_1', 'goods_description_ad',
    'description_html', 'goods_size_unit', 'High', 'Wide', 'Long',
    'goods_weight_unit', 'Goods Weight', 'Shipping Weight', 'image_link1',
    'goods_spu', 'after coupon price', 'MAP (Minimum Advertised Price)',
]


def _write_xlsx(rows) -> str:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    tmp = tempfile.NamedTemporaryFile(prefix='vevor_test_', suffix='.xlsx', delete=False)
    tmp.close()
    wb.save(tmp.name)
    return tmp.name


def _current_feed_row(sku, availability, inventory, weight_kg, after_coupon_price, map_price=None, product_link=''):
    """Build a full-width row matching CURRENT_FEED_HEADER."""
    row = [''] * len(CURRENT_FEED_HEADER)
    row[0] = sku            # SKU
    row[1] = 'AU'           # Country
    row[4] = product_link   # Product link
    row[6] = availability   # Availability  (legacy code misread this as price)
    row[7] = inventory      # Inventory quantity
    row[8] = weight_kg      # Product weight(KG)  (legacy code misread this as stock)
    row[34] = after_coupon_price  # after coupon price (Excel col 35)
    row[35] = map_price if map_price is not None else after_coupon_price  # MAP
    return row


class ResolveColumnsTests(unittest.TestCase):
    def test_current_feed_header_resolves_by_name(self):
        sku_i, price_i, inv_i, mode = resolve_vevor_feed_columns(CURRENT_FEED_HEADER)
        self.assertEqual(mode, 'header')
        self.assertEqual(sku_i, 0)
        self.assertEqual(price_i, 34)   # after coupon price
        self.assertEqual(inv_i, 7)      # Inventory quantity

    def test_prefers_after_coupon_over_map(self):
        header = [
            'SKU', 'after coupon price', 'MAP (Minimum Advertised Price)',
            'Inventory quantity',
        ]
        sku_i, price_i, inv_i, mode = resolve_vevor_feed_columns(header)
        self.assertEqual(mode, 'header')
        self.assertEqual((sku_i, price_i, inv_i), (0, 1, 3))

    def test_falls_back_to_map_when_after_coupon_missing(self):
        header = ['SKU', 'MAP (Minimum Advertised Price)', 'Inventory Quantity']
        sku_i, price_i, inv_i, mode = resolve_vevor_feed_columns(header)
        self.assertEqual(mode, 'header')
        self.assertEqual((sku_i, price_i, inv_i), (0, 1, 2))

    def test_header_matching_is_case_and_whitespace_tolerant(self):
        header = ['  sku ', 'x', 'After  Coupon Price', 'Inventory Quantity']
        sku_i, price_i, inv_i, mode = resolve_vevor_feed_columns(header)
        self.assertEqual(mode, 'header')
        self.assertEqual((sku_i, price_i, inv_i), (0, 2, 3))

    def test_unknown_header_falls_back_to_legacy_positions(self):
        sku_i, price_i, inv_i, mode = resolve_vevor_feed_columns(['a', 'b', 'c'])
        self.assertEqual(mode, 'legacy')
        self.assertEqual((sku_i, price_i, inv_i), (LEGACY_SKU_COL, LEGACY_PRICE_COL, LEGACY_INVENTORY_COL))

    def test_empty_header_falls_back_to_legacy_positions(self):
        for header in (None, [], ['', None]):
            _, _, _, mode = resolve_vevor_feed_columns(header)
            self.assertEqual(mode, 'legacy')

    def test_legacy_posted_headers_resolve_by_name(self):
        header = ['SKU', 'Posted Price', 'Posted Inventory']
        sku_i, price_i, inv_i, mode = resolve_vevor_feed_columns(header)
        self.assertEqual(mode, 'header')
        self.assertEqual((sku_i, price_i, inv_i), (0, 1, 2))


class LoadCurrentFeedTests(unittest.TestCase):
    """End-to-end parse of an XLSX in the current (2026) feed layout."""

    def setUp(self):
        self.path = _write_xlsx([
            CURRENT_FEED_HEADER,
            _current_feed_row('00PSIX5NJR2YV2MH3V0', 'in stock', '11', '11.20000', 178.90, 189.99),
            _current_feed_row('YMBYQ32YC4ZKTXLZPV0', 'in stock', 7, 3.5, '45.50', '55.00'),
            _current_feed_row('OUTOFSTOCKSKU00001', 'out of stock', 0, 2.0, 99.0, 110.0),
            _current_feed_row('NOPRICESKU00000001', 'in stock', 5, 1.0, '', 12.0),
        ])

    def tearDown(self):
        os.unlink(self.path)

    def test_price_comes_from_after_coupon_not_map_or_availability(self):
        lookup, _, rows = load_veror_via_excel_positions(self.path)
        self.assertEqual(rows, 4)
        entry = lookup['00PSIX5NJR2YV2MH3V0']
        # Availability (G) would yield 0; MAP is 189.99; after coupon is 178.90.
        self.assertEqual(entry['Posted Price'], 178.90)

    def test_stock_comes_from_inventory_quantity_not_weight(self):
        lookup, _, _ = load_veror_via_excel_positions(self.path)
        entry = lookup['00PSIX5NJR2YV2MH3V0']
        # Inventory quantity is 11; weight col would coincidentally parse as 11 too,
        # so assert on the second row where they differ (inventory 7 vs weight 3.5).
        self.assertEqual(entry['Posted Inventory'], 11)
        self.assertEqual(lookup['YMBYQ32YC4ZKTXLZPV0']['Posted Inventory'], 7)

    def test_string_price_parses(self):
        lookup, _, _ = load_veror_via_excel_positions(self.path)
        self.assertEqual(lookup['YMBYQ32YC4ZKTXLZPV0']['Posted Price'], 45.50)

    def test_zero_stock_and_missing_price_rows_still_indexed(self):
        lookup, _, _ = load_veror_via_excel_positions(self.path)
        self.assertEqual(lookup['OUTOFSTOCKSKU00001']['Posted Inventory'], 0)
        self.assertEqual(lookup['OUTOFSTOCKSKU00001']['Posted Price'], 99.0)
        # Empty after-coupon cell → 0 even if MAP has a value.
        self.assertEqual(lookup['NOPRICESKU00000001']['Posted Price'], 0.0)

    def test_compact_lookup_matches_fuzzy_sku(self):
        lookup, lookup_compact, _ = load_veror_via_excel_positions(self.path)
        hit = lookup_sku(lookup, lookup_compact, '00psix5njr2yv2mh3v0')
        self.assertIsNotNone(hit)
        self.assertEqual(hit['Posted Price'], 178.90)


class LoadLegacyFeedTests(unittest.TestCase):
    """The pre-2026 layout (A=SKU, G=Price, I=Inventory, no known header) must still parse."""

    def setUp(self):
        header = ['ID', 'col1', 'col2', 'col3', 'col4', 'col5', 'cost', 'col7', 'qty']
        self.path = _write_xlsx([
            header,
            ['LEGACYSKU000000001', '', '', '', '', '', 129.99, '', 4],
            ['LEGACYSKU000000002', '', '', '', '', '', '59.90', '', '12'],
        ])

    def tearDown(self):
        os.unlink(self.path)

    def test_legacy_positional_read(self):
        lookup, _, rows = load_veror_via_excel_positions(self.path)
        self.assertEqual(rows, 2)
        self.assertEqual(lookup['LEGACYSKU000000001'], {'Posted Price': 129.99, 'Posted Inventory': 4})
        self.assertEqual(lookup['LEGACYSKU000000002'], {'Posted Price': 59.90, 'Posted Inventory': 12})


class ShortRowTests(unittest.TestCase):
    """read_only worksheets omit trailing empty cells; rows shorter than the
    price column index must not crash and must yield price 0."""

    def test_row_shorter_than_price_column(self):
        rows = [
            CURRENT_FEED_HEADER,
            ['SHORTROWSKU0000001', 'AU', 'title', '', '', 'new', 'in stock', 3],
        ]
        path = _write_xlsx(rows)
        try:
            lookup, _, scanned = load_veror_via_excel_positions(path)
        finally:
            os.unlink(path)
        self.assertEqual(scanned, 1)
        self.assertEqual(lookup['SHORTROWSKU0000001'], {'Posted Price': 0.0, 'Posted Inventory': 3})


class IdentityAndUrlLookupTests(unittest.TestCase):
    def test_vendor_code_detection(self):
        self.assertTrue(is_vevor_vendor_code('vevorau'))
        self.assertTrue(is_vevor_vendor_code('Vevor AU'))
        self.assertTrue(is_vevor_vendor_code('vevor_au'))
        self.assertFalse(is_vevor_vendor_code('ebayau'))
        self.assertFalse(is_vevor_vendor_code('noraau'))

    def test_product_url_detection(self):
        self.assertTrue(is_vevor_product_url('https://www.vevor.com.au/winch-p_12345.html'))
        self.assertFalse(is_vevor_product_url('https://www.ebay.com.au/itm/1'))

    def test_identity_candidates_prefer_vendor_id_then_sku(self):
        keys = vevor_identity_candidates(
            vendor_id='FEED-SKU',
            sku='LASOO-SKU',
            vendor_url='https://www.vevor.com.au/item-p_99ABC.html?sku=QSKU',
        )
        self.assertEqual(keys[0], 'FEED-SKU')
        self.assertIn('LASOO-SKU', keys)
        self.assertIn('QSKU', keys)
        self.assertIn('99ABC', keys)

    def test_lookup_matches_product_link_before_listing_sku(self):
        path = _write_xlsx([
            CURRENT_FEED_HEADER,
            _current_feed_row(
                '00PSIX5NJR2YV2MH3V0',
                'in stock',
                '11',
                '11.2',
                178.90,
                189.99,
                product_link='https://www.vevor.com.au/winch.html',
            ),
        ])
        try:
            lookup, compact, _ = load_veror_via_excel_positions(path)
        finally:
            os.unlink(path)
        self.assertEqual(
            lookup['00PSIX5NJR2YV2MH3V0']['Product Link'],
            'https://www.vevor.com.au/winch.html',
        )
        by_url = {
            normalize_vevor_product_url('https://www.vevor.com.au/winch.html'):
            lookup['00PSIX5NJR2YV2MH3V0'],
        }
        hit = lookup_vevor_price_stock(
            lookup,
            compact,
            by_url,
            sku='NOT-IN-FEED',
            vendor_url='https://www.vevor.com.au/winch.html?utm=1',
        )
        self.assertIsNotNone(hit)
        self.assertEqual(hit['Posted Price'], 178.90)
        miss = lookup_vevor_price_stock(
            lookup, compact, {}, sku='NOT-IN-FEED', vendor_url='https://www.vevor.com.au/other.html',
        )
        self.assertIsNone(miss)
        by_sku = lookup_vevor_price_stock(
            lookup, compact, {}, sku='00PSIX5NJR2YV2MH3V0', vendor_url='',
        )
        self.assertEqual(by_sku['Posted Inventory'], 11)


if __name__ == '__main__':
    unittest.main(verbosity=2)
