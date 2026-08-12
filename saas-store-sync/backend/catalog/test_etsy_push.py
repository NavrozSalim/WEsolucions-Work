"""Tests for Etsy Open API v3 inventory adapter."""
from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from catalog.marketplace_catalog import store_is_etsy
from store_adapters.etsy_adapter import EtsyAdapter, EtsyAPIError


def _store():
    st = MagicMock()
    st.marketplace = MagicMock()
    st.marketplace.code = 'etsy'
    st.marketplace.name = 'Etsy'
    st.pk = None
    st.id = None
    return st


def _creds(**extra):
    data = {
        'api_key': 'keystring:sharedsecret',
        'access_token': '12345678.access-token',
        'shop_id': '998877',
    }
    data.update(extra)
    return json.dumps(data)


def _inventory_response():
    return {
        'products': [
            {
                'product_id': 11,
                'sku': 'SKU-1',
                'property_values': [
                    {
                        'property_id': 200,
                        'property_name': 'Color',
                        'scale_name': None,
                        'value_ids': [1],
                        'values': ['Red'],
                    }
                ],
                'offerings': [
                    {
                        'offering_id': 99,
                        'price': {'amount': 1999, 'divisor': 100, 'currency_code': 'USD'},
                        'quantity': 4,
                        'is_enabled': True,
                        'readiness_state_id': 55,
                    }
                ],
            }
        ],
        'price_on_property': [],
        'quantity_on_property': [200],
        'sku_on_property': [],
    }


class EtsyAdapterTests(SimpleTestCase):
    def test_store_is_etsy(self):
        self.assertTrue(store_is_etsy(_store()))
        other = _store()
        other.marketplace.code = 'reverb'
        self.assertFalse(store_is_etsy(other))

    @patch('store_adapters.etsy_adapter.requests.Session')
    def test_validate_connection_uses_me_and_shop(self, session_cls):
        session = session_cls.return_value

        def request_side_effect(method, url, **kwargs):
            resp = MagicMock(status_code=200, text='{}')
            if url.endswith('/application/users/me'):
                resp.text = json.dumps({'user_id': 1, 'shop_id': 998877})
                resp.json.return_value = {'user_id': 1, 'shop_id': 998877}
            elif '/application/shops/998877' in url:
                resp.text = json.dumps({'shop_id': 998877, 'shop_name': 'Demo'})
                resp.json.return_value = {'shop_id': 998877, 'shop_name': 'Demo'}
            return resp

        session.request.side_effect = request_side_effect
        store = _store()
        # Omit shop_id so adapter discovers it via /users/me
        store.api_token = _creds()
        data = json.loads(store.api_token)
        data.pop('shop_id', None)
        store.api_token = json.dumps(data)
        adapter = EtsyAdapter(store)
        ok, msg, shop_id = adapter.test_etsy_connection()
        self.assertTrue(ok)
        self.assertIn('connected', msg.lower())
        self.assertEqual(shop_id, '998877')

    @patch('store_adapters.etsy_adapter.requests.Session')
    def test_update_inventory_puts_sanitized_body(self, session_cls):
        session = session_cls.return_value

        def request_side_effect(method, url, **kwargs):
            resp = MagicMock(status_code=200, text='{}')
            if method == 'GET' and url.endswith('/inventory'):
                body = _inventory_response()
                resp.text = json.dumps(body)
                resp.json.return_value = body
            elif method == 'PUT' and url.endswith('/inventory'):
                resp.text = '{}'
                resp.json.return_value = {}
            return resp

        session.request.side_effect = request_side_effect
        store = _store()
        store.api_token = _creds()
        adapter = EtsyAdapter(store)
        self.assertTrue(adapter.update_inventory('192837465', 12))

        put_calls = [c for c in session.request.call_args_list if c[0][0] == 'PUT']
        self.assertEqual(len(put_calls), 1)
        body = put_calls[0][1]['json']
        product = body['products'][0]
        self.assertNotIn('product_id', product)
        offering = product['offerings'][0]
        self.assertNotIn('offering_id', offering)
        self.assertEqual(offering['quantity'], 12)
        self.assertEqual(offering['price'], 19.99)
        self.assertEqual(offering['readiness_state_id'], 55)
        headers = put_calls[0][1]['headers']
        self.assertEqual(headers['x-api-key'], 'keystring:sharedsecret')
        self.assertTrue(headers['Authorization'].startswith('Bearer '))

    @patch('store_adapters.etsy_adapter.requests.Session')
    def test_update_product_price_and_stock(self, session_cls):
        session = session_cls.return_value

        def request_side_effect(method, url, **kwargs):
            resp = MagicMock(status_code=200, text='{}')
            if method == 'GET' and url.endswith('/inventory'):
                body = _inventory_response()
                resp.text = json.dumps(body)
                resp.json.return_value = body
            return resp

        session.request.side_effect = request_side_effect
        store = _store()
        store.api_token = _creds()
        adapter = EtsyAdapter(store)
        adapter.update_product('555', price=Decimal('25.50'), stock=3)

        put_calls = [c for c in session.request.call_args_list if c[0][0] == 'PUT']
        body = put_calls[0][1]['json']
        offering = body['products'][0]['offerings'][0]
        self.assertEqual(offering['price'], 25.5)
        self.assertEqual(offering['quantity'], 3)

    @patch('store_adapters.etsy_adapter.requests.Session')
    def test_lookup_listing_by_numeric_id(self, session_cls):
        session = session_cls.return_value
        body = _inventory_response()
        resp = MagicMock(status_code=200, text=json.dumps(body))
        resp.json.return_value = body
        session.request.return_value = resp

        store = _store()
        store.api_token = _creds()
        adapter = EtsyAdapter(store)
        self.assertEqual(adapter.lookup_listing_by_sku('192837465'), '192837465')

    @patch('store_adapters.etsy_adapter.requests.Session')
    def test_missing_api_key_raises(self, session_cls):
        store = _store()
        store.api_token = json.dumps({'access_token': 'tok'})
        adapter = EtsyAdapter(store)
        with self.assertRaises(EtsyAPIError) as ctx:
            adapter.update_inventory('1', 1)
        self.assertIn('api_key', str(ctx.exception).lower())
