"""Tests for Walmart OAuth headers and price/inventory push."""
from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from catalog.reverb_catalog import listing_sku_lookup_order, store_is_walmart
from store_adapters.walmart_adapter import WalmartAdapter


def _store(code: str = 'walmart'):
    st = MagicMock()
    st.marketplace = MagicMock()
    st.marketplace.code = code
    st.marketplace.name = code.title()
    st.use_sandbox = False
    return st


def _oauth_creds():
    return json.dumps(
        {
            'client_id': 'test-client-id',
            'client_secret': 'test-client-secret',
            'channel_type': 'test-channel-type',
        }
    )


class WalmartAdapterAuthTests(SimpleTestCase):
    def test_store_is_walmart(self):
        self.assertTrue(store_is_walmart(_store('walmart')))
        self.assertFalse(store_is_walmart(_store('sears')))

    def test_listing_sku_lookup_order_walmart_uses_child_sku_only(self):
        pm = MagicMock()
        pm.marketplace_child_sku = 'WM-001'
        pm.marketplace_parent_sku = 'PARENT-1'
        pm.product = MagicMock()
        pm.product.vendor_sku = 'VENDOR-1'
        self.assertEqual(listing_sku_lookup_order(pm, _store()), ['WM-001'])

    @patch('store_adapters.walmart_adapter.requests.Session')
    def test_oauth_headers_use_basic_and_access_token(self, session_cls):
        session = session_cls.return_value
        token_resp = MagicMock(status_code=200)
        token_resp.text = json.dumps({'access_token': 'tok-abc', 'expires_in': 900})
        token_resp.json.return_value = {'access_token': 'tok-abc', 'expires_in': 900}
        session.post.return_value = token_resp
        session.request.return_value = MagicMock(status_code=200, text='{}')

        store = _store()
        store.api_token = _oauth_creds()
        adapter = WalmartAdapter(store)
        adapter.update_inventory('WM-001', 5)

        _, kwargs = session.request.call_args
        headers = kwargs['headers']
        self.assertTrue(headers['Authorization'].startswith('Basic '))
        self.assertEqual(headers['WM_SEC.ACCESS_TOKEN'], 'tok-abc')
        self.assertEqual(headers['WM_CONSUMER.CHANNEL.TYPE'], 'test-channel-type')
        self.assertEqual(headers['WM_SVC.NAME'], 'Walmart Marketplace')
        self.assertIn('WM_QOS.CORRELATION_ID', headers)

    @patch('store_adapters.walmart_adapter.requests.Session')
    def test_update_inventory_uses_v3_inventory_endpoint(self, session_cls):
        session = session_cls.return_value
        token_resp = MagicMock(status_code=200)
        token_resp.text = json.dumps({'access_token': 'tok-abc', 'expires_in': 900})
        token_resp.json.return_value = {'access_token': 'tok-abc', 'expires_in': 900}
        session.post.return_value = token_resp
        session.request.return_value = MagicMock(status_code=200, text='{}')

        store = _store()
        store.api_token = _oauth_creds()
        adapter = WalmartAdapter(store)
        adapter.update_inventory('WM-002', 12)

        method, url = session.request.call_args[0][:2]
        self.assertEqual(method, 'PUT')
        self.assertTrue(url.endswith('/v3/inventory'))
        body = session.request.call_args[1]['json']
        self.assertEqual(body['sku'], 'WM-002')
        self.assertEqual(body['quantity'], {'unit': 'EACH', 'amount': 12})

    @patch('store_adapters.walmart_adapter.requests.Session')
    def test_lookup_uses_items_query_by_sku(self, session_cls):
        session = session_cls.return_value
        token_resp = MagicMock(status_code=200)
        token_resp.text = json.dumps({'access_token': 'tok-abc', 'expires_in': 900})
        token_resp.json.return_value = {'access_token': 'tok-abc', 'expires_in': 900}
        session.post.return_value = token_resp
        session.request.return_value = MagicMock(status_code=200, text='{"items": []}')

        store = _store()
        store.api_token = _oauth_creds()
        adapter = WalmartAdapter(store)
        self.assertEqual(adapter.lookup_listing_by_sku('WM-003'), 'WM-003')

        method, url = session.request.call_args[0][:2]
        self.assertEqual(method, 'GET')
        self.assertIn('/v3/items?sku=WM-003', url)

    @patch('store_adapters.walmart_adapter.requests.Session')
    def test_update_product_sends_price_and_inventory(self, session_cls):
        session = session_cls.return_value
        token_resp = MagicMock(status_code=200)
        token_resp.text = json.dumps({'access_token': 'tok-abc', 'expires_in': 900})
        token_resp.json.return_value = {'access_token': 'tok-abc', 'expires_in': 900}
        session.post.return_value = token_resp
        session.request.return_value = MagicMock(status_code=200, text='{}')

        store = _store()
        store.api_token = _oauth_creds()
        adapter = WalmartAdapter(store)
        adapter.update_product('WM-004', price=Decimal('19.99'), stock=3)

        calls = session.request.call_args_list
        self.assertEqual(len(calls), 2)
        price_call = calls[0]
        inv_call = calls[1]
        self.assertEqual(price_call[0][0], 'PUT')
        self.assertTrue(price_call[0][1].endswith('/v3/price'))
        self.assertEqual(price_call[1]['json']['sku'], 'WM-004')
        self.assertEqual(inv_call[0][0], 'PUT')
        self.assertTrue(inv_call[0][1].endswith('/v3/inventory'))


class WalmartListingIdTests(SimpleTestCase):
    def test_resolve_listing_id_walmart_prefers_child_over_marketplace_id(self):
        from sync.tasks import _resolve_listing_id_for_pm

        pm = MagicMock()
        pm.marketplace_id = 'WALMART-ITEM-ID-999'
        pm.marketplace_child_sku = 'WM-123'
        adapter = MagicMock()
        self.assertEqual(_resolve_listing_id_for_pm(adapter, pm, _store()), 'WM-123')
        adapter.lookup_listing_by_sku.assert_not_called()
