"""Unit tests for the Costway AU dropship CSV parser.

Live feed: https://au.costway.com/media/feed/Dropship-AU.csv
Columns: SKU, Item NO., Title, Description, Price, Category, Link, QTY, Weight, Image
"""
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from scrapers.costway_au_ingest import (
    COSTWAY_PRICE_COL,
    COSTWAY_QTY_COL,
    COSTWAY_SKU_COL,
    fetch_costway_feed,
    is_costway_product_url,
    is_costway_vendor_code,
    load_costway_via_csv,
    lookup_costway_price_stock,
    lookup_sku,
    normalize_costway_product_url,
    resolve_costway_feed_columns,
)

COSTWAY_FEED_HEADER = [
    'SKU', 'Item NO.', 'Title', 'Description', 'Price', 'Category',
    'Link', 'QTY', 'Weight', 'Image',
]


def _write_csv(rows, *, delimiter=',', bom=False) -> str:
    tmp = tempfile.NamedTemporaryFile(prefix='costway_test_', suffix='.csv', delete=False, mode='wb')
    try:
        if bom:
            tmp.write(b'\xef\xbb\xbf')
        for row in rows:
            line = delimiter.join(_csv_cell(cell, delimiter) for cell in row)
            tmp.write((line + '\n').encode('utf-8'))
    finally:
        tmp.close()
    return tmp.name


def _csv_cell(cell, delimiter):
    s = '' if cell is None else str(cell)
    if delimiter in s or '"' in s or '\n' in s:
        return '"' + s.replace('"', '""') + '"'
    return s


class ResolveColumnsTests(unittest.TestCase):
    def test_screenshot_header_resolves_by_name(self):
        sku_i, price_i, qty_i, item_i, link_i, mode = resolve_costway_feed_columns(COSTWAY_FEED_HEADER)
        self.assertEqual(mode, 'header')
        self.assertEqual(sku_i, 0)
        self.assertEqual(price_i, 4)
        self.assertEqual(qty_i, 7)
        self.assertEqual(item_i, 1)
        self.assertEqual(link_i, 6)

    def test_qty_is_not_weight(self):
        header = ['SKU', 'Price', 'Weight', 'QTY']
        sku_i, price_i, qty_i, _, _, mode = resolve_costway_feed_columns(header)
        self.assertEqual(mode, 'header')
        self.assertEqual((sku_i, price_i, qty_i), (0, 1, 3))

    def test_item_no_period_and_case(self):
        header = ['  sku ', 'ITEM NO.', 'x', 'Price', 'qty']
        sku_i, price_i, qty_i, item_i, _, mode = resolve_costway_feed_columns(header)
        self.assertEqual(mode, 'header')
        self.assertEqual((sku_i, price_i, qty_i, item_i), (0, 3, 4, 1))

    def test_unknown_header_uses_screenshot_positions(self):
        sku_i, price_i, qty_i, item_i, link_i, mode = resolve_costway_feed_columns(['a', 'b', 'c'])
        self.assertEqual(mode, 'positional')
        self.assertEqual(sku_i, COSTWAY_SKU_COL)
        self.assertEqual(price_i, COSTWAY_PRICE_COL)
        self.assertEqual(qty_i, COSTWAY_QTY_COL)
        self.assertEqual(item_i, 1)
        self.assertEqual(link_i, 6)


class LoadCostwayCsvTests(unittest.TestCase):
    def setUp(self):
        self.path = _write_csv([
            COSTWAY_FEED_HEADER,
            [
                'TP10003', '73982054', 'Costway 3 If you are',
                'If you are, still agonizing', '109.95', 'Baby & Kid',
                'http://au.costway.com/tp10003.html', '5', '37.58882',
                'http://au.costway.com/img.jpg',
            ],
            [
                'TW10004C', '98032174', 'Costway S Enjoy a cal',
                'Enjoy', '262.95', 'Toys & Ho',
                'https://au.costway.com/tw.html', '407', '78.61684',
                'http://au.costway.com/img2.jpg',
            ],
            [
                'ZEROSTOCK', '111', 'Title', 'Desc', '56.95', 'Toys',
                'http://au.costway.com/zero.html', '0', '7.89', '',
            ],
        ])

    def tearDown(self):
        os.unlink(self.path)

    def test_price_and_qty_from_named_columns(self):
        lookup, _, rows = load_costway_via_csv(self.path)
        self.assertEqual(rows, 3)
        entry = lookup['TP10003']
        self.assertEqual(entry['Posted Price'], 109.95)
        self.assertEqual(entry['Posted Inventory'], 5)

    def test_weight_is_not_used_as_qty(self):
        lookup, _, _ = load_costway_via_csv(self.path)
        self.assertEqual(lookup['TP10003']['Posted Inventory'], 5)
        self.assertEqual(lookup['TW10004C']['Posted Inventory'], 407)

    def test_quoted_description_commas_do_not_shift_columns(self):
        lookup, _, _ = load_costway_via_csv(self.path)
        self.assertEqual(lookup['TP10003']['Posted Price'], 109.95)

    def test_item_no_is_secondary_key(self):
        lookup, compact, _ = load_costway_via_csv(self.path)
        hit = lookup_sku(lookup, compact, '73982054')
        self.assertIsNotNone(hit)
        self.assertEqual(hit['Posted Price'], 109.95)

    def test_zero_qty_still_indexed(self):
        lookup, _, _ = load_costway_via_csv(self.path)
        self.assertEqual(lookup['ZEROSTOCK']['Posted Inventory'], 0)
        self.assertEqual(lookup['ZEROSTOCK']['Posted Price'], 56.95)

    def test_link_lookup_strips_query(self):
        lookup, compact, _ = load_costway_via_csv(self.path)
        by_url = {
            normalize_costway_product_url('http://au.costway.com/tp10003.html'):
            lookup['TP10003'],
        }
        hit = lookup_costway_price_stock(
            lookup, compact, by_url,
            sku='NOT-IN-FEED',
            vendor_url='http://au.costway.com/tp10003.html?utm=1',
        )
        self.assertEqual(hit['Posted Inventory'], 5)
        miss = lookup_costway_price_stock(
            lookup, compact, {},
            sku='NOT-IN-FEED',
            vendor_url='http://au.costway.com/other.html',
        )
        self.assertIsNone(miss)

    def test_sku_lookup_when_listing_sku_matches_feed(self):
        lookup, compact, _ = load_costway_via_csv(self.path)
        hit = lookup_costway_price_stock(lookup, compact, {}, sku='TW10004C')
        self.assertEqual(hit['Posted Price'], 262.95)
        self.assertEqual(hit['Posted Inventory'], 407)


class BomAndDelimiterTests(unittest.TestCase):
    def test_utf8_bom_header(self):
        path = _write_csv([
            COSTWAY_FEED_HEADER,
            ['BOM-SKU', '1', 'T', 'D', '12.50', 'Cat', 'http://au.costway.com/a', '3', '1.1', ''],
        ], bom=True)
        try:
            lookup, _, _ = load_costway_via_csv(path)
        finally:
            os.unlink(path)
        self.assertEqual(lookup['BOM-SKU']['Posted Price'], 12.50)
        self.assertEqual(lookup['BOM-SKU']['Posted Inventory'], 3)

    def test_semicolon_delimited(self):
        path = _write_csv([
            COSTWAY_FEED_HEADER,
            ['SEMI-SKU', '2', 'T', 'D', '9.99', 'Cat', 'http://au.costway.com/b', '8', '2.2', ''],
        ], delimiter=';')
        try:
            lookup, _, rows = load_costway_via_csv(path)
        finally:
            os.unlink(path)
        self.assertEqual(rows, 1)
        self.assertEqual(lookup['SEMI-SKU']['Posted Inventory'], 8)


class IdentityTests(unittest.TestCase):
    def test_vendor_code_is_costway_not_costco(self):
        self.assertTrue(is_costway_vendor_code('costwayau'))
        self.assertTrue(is_costway_vendor_code('Costway AU'))
        self.assertTrue(is_costway_vendor_code('costway_au'))
        self.assertTrue(is_costway_vendor_code('costway'))
        self.assertFalse(is_costway_vendor_code('costcoau'))
        self.assertFalse(is_costway_vendor_code('costco'))
        self.assertFalse(is_costway_vendor_code('vevorau'))
        self.assertFalse(is_costway_vendor_code('ebayau'))

    def test_product_url_is_costway_not_costco(self):
        self.assertTrue(is_costway_product_url('http://au.costway.com/tp10003.html'))
        self.assertTrue(is_costway_product_url('https://www.costway.com.au/p/1'))
        self.assertFalse(is_costway_product_url('https://www.costco.com.au/p/1'))
        self.assertFalse(is_costway_product_url('https://www.vevor.com.au/x'))
        self.assertFalse(is_costway_product_url(''))

    def test_dispatcher_returns_ingest_only_for_costway_url(self):
        from scrapers import get_price_and_stock

        result = get_price_and_stock('http://au.costway.com/tp10003.html', 'AU', {})
        self.assertEqual(result.get('error_code'), 'costway_ingest_only')
        self.assertIsNone(result.get('price'))

    def test_dispatcher_does_not_treat_costco_as_costway(self):
        from scrapers import get_price_and_stock

        result = get_price_and_stock('https://www.costco.com.au/p/1', 'AU', {})
        self.assertNotEqual(result.get('error_code'), 'costway_ingest_only')


class FetchNoProxyTests(unittest.TestCase):
    @patch('scrapers.costway_au_ingest.requests.Session')
    def test_fetch_disables_env_proxies(self, mock_session_cls):
        session = mock_session_cls.return_value
        resp = MagicMock()
        resp.iter_content.return_value = [b'SKU,Price,QTY\nA,1,2\n']
        resp.raise_for_status.return_value = None
        session.get.return_value = resp
        path = fetch_costway_feed('https://au.costway.com/media/feed/Dropship-AU.csv')
        try:
            self.assertFalse(session.trust_env)
            self.assertEqual(session.proxies, {'http': None, 'https': None})
            kwargs = session.get.call_args.kwargs
            self.assertEqual(kwargs.get('proxies'), {'http': None, 'https': None})
        finally:
            os.unlink(path)

    @patch('scrapers.costway_au_ingest.requests.Session')
    def test_html_body_is_treated_as_geo_block(self, mock_session_cls):
        session = mock_session_cls.return_value
        resp = MagicMock()
        resp.iter_content.return_value = [b'<!DOCTYPE html><html><body>Forbidden</body></html>']
        resp.raise_for_status.return_value = None
        session.get.return_value = resp
        with self.assertRaises(RuntimeError) as ctx:
            fetch_costway_feed('https://au.costway.com/media/feed/Dropship-AU.csv')
        self.assertIn('geo-restricted', str(ctx.exception).lower())
        self.assertIn('heavy-au', str(ctx.exception))


if __name__ == '__main__':
    unittest.main(verbosity=2)
