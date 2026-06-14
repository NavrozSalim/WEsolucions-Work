"""Tests for AliExpress API scraper helpers and client parsing."""
from __future__ import annotations

import json
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from scrapers.aliexpress_client import sign_params, _products_from_detail_response
from scrapers.aliexpress_markets import resolve_aliexpress_market
from scrapers.aliexpress_scraper import (
    build_aliexpress_item_url,
    extract_aliexpress_product_id,
    is_aliexpress_url,
    is_aliexpress_vendor_code,
    scrape_aliexpress,
)


class AliExpressMarketTests(SimpleTestCase):
    @override_settings(ALIEXPRESS_DEFAULT_MARKET='UK')
    def test_unknown_region_defaults_to_env(self):
        self.assertEqual(resolve_aliexpress_market(None), 'UK')
        self.assertEqual(resolve_aliexpress_market(''), 'UK')

    def test_store_region_mapping(self):
        self.assertEqual(resolve_aliexpress_market('UK'), 'UK')
        self.assertEqual(resolve_aliexpress_market('USA'), 'USA')
        self.assertEqual(resolve_aliexpress_market('AU'), 'AU')


class AliExpressUrlTests(SimpleTestCase):
    def test_extract_id_from_item_url(self):
        url = 'https://www.aliexpress.com/item/1005001234567890.html'
        self.assertEqual(extract_aliexpress_product_id(url), '1005001234567890')

    def test_extract_id_from_uk_tld(self):
        url = 'https://www.aliexpress.co.uk/item/1005001234567890.html?spm=a.b.c'
        self.assertEqual(extract_aliexpress_product_id(url), '1005001234567890')

    def test_extract_id_from_plain_sku(self):
        self.assertEqual(extract_aliexpress_product_id('1005001234567890'), '1005001234567890')

    def test_build_item_url(self):
        self.assertEqual(
            build_aliexpress_item_url('1005001234567890'),
            'https://www.aliexpress.com/item/1005001234567890.html',
        )

    def test_is_aliexpress_url(self):
        self.assertTrue(is_aliexpress_url('https://www.aliexpress.co.uk/item/1.html'))
        self.assertFalse(is_aliexpress_url('https://www.amazon.com/dp/B001'))

    def test_vendor_code(self):
        self.assertTrue(is_aliexpress_vendor_code('aliexpress'))
        self.assertTrue(is_aliexpress_vendor_code('AliExpressUK'))
        self.assertFalse(is_aliexpress_vendor_code('amazonus'))


class AliExpressSignTests(SimpleTestCase):
    def test_md5_sign_is_uppercase_hex(self):
        params = {
            'method': 'aliexpress.affiliate.productdetail.get',
            'app_key': 'testkey',
            'timestamp': '2026-01-01 12:00:00',
            'v': '2.0',
        }
        sig = sign_params(params, 'secret', 'md5')
        self.assertEqual(len(sig), 32)
        self.assertEqual(sig, sig.upper())


class AliExpressResponseParseTests(SimpleTestCase):
    def test_products_from_detail_response(self):
        payload = {
            'aliexpress_affiliate_productdetail_get_response': {
                'resp_result': {
                    'result': {
                        'products': {
                            'product': [
                                {
                                    'product_id': '123',
                                    'target_sale_price': '19.99',
                                    'product_title': 'Sample widget',
                                }
                            ]
                        }
                    }
                }
            }
        }
        rows = _products_from_detail_response(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['product_id'], '123')


@override_settings(
    ALIEXPRESS_APP_KEY='key',
    ALIEXPRESS_APP_SECRET='secret',
    ALIEXPRESS_TRACKING_ID='track',
    ALIEXPRESS_DEFAULT_MARKET='UK',
)
class AliExpressScrapeTests(SimpleTestCase):
    def test_not_configured(self):
        with override_settings(ALIEXPRESS_APP_KEY='', ALIEXPRESS_APP_SECRET=''):
            result = scrape_aliexpress(
                'https://www.aliexpress.com/item/1005001234567890.html',
                'UK',
            )
        self.assertEqual(result['error_code'], 'aliexpress_not_configured')

    def test_invalid_url(self):
        result = scrape_aliexpress('https://example.com/nope', 'UK')
        self.assertEqual(result['error_code'], 'aliexpress_invalid_url')

    @patch('scrapers.aliexpress_client.requests.post')
    def test_scrape_success_uk(self, mock_post):
        api_body = {
            'aliexpress_affiliate_productdetail_get_response': {
                'resp_result': {
                    'result': {
                        'products': {
                            'product': {
                                'product_id': '1005001234567890',
                                'target_sale_price': '24.50',
                                'product_title': 'UK priced item',
                            }
                        }
                    }
                }
            }
        }
        mock_resp = mock_post.return_value
        mock_resp.status_code = 200
        mock_resp.text = json.dumps(api_body)
        mock_resp.json.return_value = api_body

        result = scrape_aliexpress(
            'https://www.aliexpress.com/item/1005001234567890.html',
            'UK',
        )
        self.assertEqual(result['price'], 24.50)
        self.assertEqual(result['stock'], 999)
        self.assertEqual(result['title'], 'UK priced item')
        self.assertNotIn('error_code', result)

        posted = mock_post.call_args.kwargs.get('data') or mock_post.call_args[1].get('data')
        self.assertEqual(posted['country'], 'GB')
        self.assertEqual(posted['target_currency'], 'GBP')

    @patch('scrapers.aliexpress_client.requests.post')
    def test_scrape_us_region(self, mock_post):
        api_body = {
            'aliexpress_affiliate_productdetail_get_response': {
                'resp_result': {
                    'result': {
                        'products': {
                            'product': {
                                'product_id': '1005001234567890',
                                'target_sale_price': '10.00',
                            }
                        }
                    }
                }
            }
        }
        mock_resp = mock_post.return_value
        mock_resp.status_code = 200
        mock_resp.text = json.dumps(api_body)
        mock_resp.json.return_value = api_body

        scrape_aliexpress(
            'https://www.aliexpress.com/item/1005001234567890.html',
            'USA',
        )
        posted = mock_post.call_args.kwargs.get('data') or mock_post.call_args[1].get('data')
        self.assertEqual(posted['country'], 'US')
        self.assertEqual(posted['target_currency'], 'USD')
