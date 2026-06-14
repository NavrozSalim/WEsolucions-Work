"""Tests for AliExpress API scraper helpers and client parsing."""
from __future__ import annotations

import json
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from scrapers.aliexpress_client import sign_params, _products_from_detail_response, get_api_url, DEFAULT_API_URL
from scrapers.aliexpress_ds_parser import price_from_ds_result, stock_from_ds_result, title_from_ds_result
from scrapers.aliexpress_iop import (
    AliExpressIOPError,
    fetch_ds_product,
    iop_sync_business_request,
    method_to_iop_path,
    method_to_slash_path,
    sign_iop_request,
    sign_iop_sync_request,
)
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


class AliExpressApiUrlTests(SimpleTestCase):
    def test_default_api_url_is_overseas_gateway(self):
        self.assertEqual(DEFAULT_API_URL, 'https://api.taobao.com/router/rest')

    @override_settings(ALIEXPRESS_API_URL='https://eco.taobao.com/router/rest')
    def test_get_api_url_from_settings(self):
        self.assertEqual(get_api_url(), 'https://eco.taobao.com/router/rest')

    @override_settings(ALIEXPRESS_API_URL='')
    def test_get_api_url_falls_back_to_default(self):
        self.assertEqual(get_api_url(), DEFAULT_API_URL)


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

    def test_iop_sign_is_uppercase_hex(self):
        params = {
            'app_key': '536712',
            'code': 'abc123',
            'sign_method': 'sha256',
            'timestamp': '1710000000000',
        }
        sig = sign_iop_request('/auth/token/security/create', params, 'secret')
        self.assertEqual(len(sig), 64)
        self.assertEqual(sig, sig.upper())

    def test_method_to_iop_path(self):
        self.assertEqual(method_to_iop_path('aliexpress.ds.product.get'), '/aliexpress.ds.product.get')
        self.assertEqual(method_to_iop_path('/auth/token/security/create'), '/auth/token/security/create')

    def test_method_to_slash_path(self):
        self.assertEqual(method_to_slash_path('aliexpress.ds.product.get'), '/aliexpress/ds/product/get')

    def test_sync_sign_no_path_prefix(self):
        params = {
            'app_key': '536784',
            'method': 'aliexpress.ds.product.get',
            'session': 'token',
            'sign_method': 'sha256',
            'timestamp': '1710000000000',
            'simplify': 'true',
            'product_id': '1005007170995524',
        }
        sig = sign_iop_sync_request(params, 'secret')
        self.assertEqual(len(sig), 64)
        self.assertEqual(sig, sig.upper())
        self.assertNotEqual(
            sig,
            sign_iop_request('/aliexpress/ds/product/get', params, 'secret'),
        )

    @override_settings(
        ALIEXPRESS_APP_KEY='536784',
        ALIEXPRESS_APP_SECRET='secret',
        ALIEXPRESS_IOP_GATEWAY='https://api-sg.aliexpress.com',
    )
    @patch('scrapers.aliexpress_iop.requests.post')
    def test_iop_sync_business_request_uses_sync_gateway(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.text = json.dumps(
            {
                'aliexpress_ds_product_get_response': {
                    'result': {'ae_item_base_info_dto': {'subject': 'x'}},
                }
            }
        )
        mock_post.return_value.json.return_value = json.loads(mock_post.return_value.text)

        iop_sync_business_request(
            'aliexpress.ds.product.get',
            {'product_id': '1005007170995524'},
            'access-token',
        )

        self.assertEqual(
            mock_post.call_args[0][0],
            'https://api-sg.aliexpress.com/sync',
        )
        posted = mock_post.call_args.kwargs.get('params') or mock_post.call_args[1].get('params')
        self.assertEqual(posted['method'], 'aliexpress.ds.product.get')
        self.assertEqual(posted['session'], 'access-token')
        self.assertNotIn('v', posted)
        self.assertEqual(posted['sign_method'], 'sha256')
        self.assertGreaterEqual(len(posted['timestamp']), 13)

    @override_settings(
        ALIEXPRESS_APP_KEY='536784',
        ALIEXPRESS_APP_SECRET='secret',
        ALIEXPRESS_IOP_GATEWAY='https://api-sg.aliexpress.com',
    )
    @patch('scrapers.aliexpress_iop.iop_rest_business_request')
    @patch('scrapers.aliexpress_iop._iop_sync_business_request_form')
    @patch('scrapers.aliexpress_iop.iop_sync_business_request')
    def test_fetch_ds_product_falls_back_after_sync_failures(self, mock_sync, mock_sync_form, mock_rest):
        mock_sync.side_effect = AliExpressIOPError('sync: IncompleteSignature')
        mock_sync_form.side_effect = AliExpressIOPError('sync-form: IncompleteSignature')
        mock_rest.return_value = {
            'aliexpress_ds_product_get_response': {
                'result': {
                    'ae_item_base_info_dto': {'subject': 'REST item', 'product_status_type': 'onSelling'},
                    'ae_item_sku_info_dtos': {
                        'ae_item_sku_info_dto': {'offer_sale_price': '9.99', 'sku_available_stock': 4}
                    },
                }
            }
        }

        result = fetch_ds_product('1005007170995524', 'UK', 'oauth-token')
        self.assertEqual(result['ae_item_base_info_dto']['subject'], 'REST item')
        self.assertGreaterEqual(mock_sync.call_count, 1)
        mock_rest.assert_called_once()


class AliExpressDsParserTests(SimpleTestCase):
    def test_parse_ds_result(self):
        result = {
            'ae_item_base_info_dto': {
                'subject': 'Sample DS product',
                'product_status_type': 'onSelling',
            },
            'ae_item_sku_info_dtos': {
                'ae_item_sku_info_dto': [
                    {'offer_sale_price': '12.50', 'sku_available_stock': 5},
                    {'offer_sale_price': '11.99', 'sku_available_stock': 3},
                ]
            },
        }
        self.assertEqual(title_from_ds_result(result), 'Sample DS product')
        self.assertEqual(price_from_ds_result(result), 11.99)
        self.assertEqual(stock_from_ds_result(result), 8)

    def test_parse_simplified_ds_result(self):
        """simplify=true uses ae_item_sku_info_d_t_o nested key."""
        result = {
            'ae_item_base_info_dto': {
                'subject': 'Simplified SKU product',
                'product_status_type': 'onSelling',
            },
            'ae_item_sku_info_dtos': {
                'ae_item_sku_info_d_t_o': [
                    {'offer_sale_price': '14.20', 'sku_available_stock': 2},
                    {'offer_sale_price': '13.50', 'sku_available_stock': 1},
                ]
            },
        }
        self.assertEqual(title_from_ds_result(result), 'Simplified SKU product')
        self.assertEqual(price_from_ds_result(result), 13.50)
        self.assertEqual(stock_from_ds_result(result), 3)

    def test_parse_base_min_price_fallback(self):
        result = {
            'ae_item_base_info_dto': {
                'subject': 'No SKU prices',
                'product_min_price': '9.99',
                'product_status_type': 'onSelling',
            },
            'ae_item_sku_info_dtos': {},
        }
        self.assertEqual(price_from_ds_result(result), 9.99)


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
    ALIEXPRESS_API_URL='https://api.taobao.com/router/rest',
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
        self.assertEqual(mock_post.call_args[0][0], 'https://api.taobao.com/router/rest')

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

    @patch('scrapers.aliexpress_scraper.fetch_ds_product')
    @patch('scrapers.aliexpress_scraper.get_valid_access_token', return_value='test-access-token')
    def test_scrape_ds_success_uk(self, _mock_token, mock_fetch):
        mock_fetch.return_value = {
            'ae_item_base_info_dto': {'subject': 'DS UK item', 'product_status_type': 'onSelling'},
            'ae_item_sku_info_dtos': {
                'ae_item_sku_info_dto': {'offer_sale_price': '18.75', 'sku_available_stock': 12}
            },
        }
        result = scrape_aliexpress(
            'https://www.aliexpress.com/item/1005001234567890.html',
            'UK',
            {'aliexpress_user_id': 'user-1'},
        )
        self.assertEqual(result['price'], 18.75)
        self.assertEqual(result['stock'], 12)
        self.assertEqual(result['title'], 'DS UK item')
        mock_fetch.assert_called_once()

    @patch('scrapers.aliexpress_client.requests.post')
    @patch('scrapers.aliexpress_scraper.get_valid_access_token', return_value=None)
    def test_oauth_required_without_token(self, _mock_token, mock_post):
        mock_resp = mock_post.return_value
        mock_resp.status_code = 200
        mock_resp.text = '{"error_response":{"code":29,"msg":"appkey not exists"}}'
        mock_resp.json.return_value = {'error_response': {'code': 29, 'msg': 'appkey not exists'}}

        result = scrape_aliexpress(
            'https://www.aliexpress.com/item/1005001234567890.html',
            'UK',
        )
        self.assertEqual(result['error_code'], 'aliexpress_oauth_required')
