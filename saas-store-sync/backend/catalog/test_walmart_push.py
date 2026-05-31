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


def _oauth_creds(*, ship_node=None):
    data = {
        'client_id': 'test-client-id',
        'client_secret': 'test-client-secret',
    }
    if ship_node is not None:
        data['ship_node'] = ship_node
    return json.dumps(data)


def _mock_token(session):
    token_resp = MagicMock(status_code=200)
    token_resp.text = json.dumps({'access_token': 'tok-abc', 'expires_in': 900})
    token_resp.json.return_value = {'access_token': 'tok-abc', 'expires_in': 900}
    session.post.return_value = token_resp


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
    def test_oauth_headers_default_channel_type_to_client_id(self, session_cls):
        session = session_cls.return_value
        _mock_token(session)
        session.request.return_value = MagicMock(status_code=200, text='{}')

        store = _store()
        store.api_token = _oauth_creds()
        adapter = WalmartAdapter(store)
        adapter.update_inventory('WM-001', 5)

        headers = session.request.call_args[1]['headers']
        self.assertTrue(headers['Authorization'].startswith('Basic '))
        self.assertEqual(headers['WM_SEC.ACCESS_TOKEN'], 'tok-abc')
        self.assertEqual(headers['WM_CONSUMER.CHANNEL.TYPE'], 'test-client-id')

    @patch('store_adapters.walmart_adapter.requests.Session')
    def test_update_inventory_uses_listing_ship_node_kwarg(self, session_cls):
        session = session_cls.return_value
        _mock_token(session)
        session.request.return_value = MagicMock(status_code=200, text='{}')

        store = _store()
        store.api_token = _oauth_creds()
        adapter = WalmartAdapter(store)
        adapter.update_inventory('WM-LIST', 7, ship_node='861260459919982593')

        method, url = session.request.call_args[0][:2]
        self.assertEqual(method, 'PUT')
        self.assertIn('/v3/inventories/WM-LIST', url)
        body = session.request.call_args[1]['json']
        self.assertEqual(body['inventories']['nodes'][0]['shipNode'], '861260459919982593')

    @patch('store_adapters.walmart_adapter.requests.Session')
    def test_update_inventory_uses_default_endpoint_without_ship_node(self, session_cls):
        session = session_cls.return_value
        _mock_token(session)
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
    def test_update_inventory_uses_ship_node_endpoint_when_configured(self, session_cls):
        session = session_cls.return_value
        _mock_token(session)
        session.request.return_value = MagicMock(status_code=200, text='{}')

        store = _store()
        store.api_token = _oauth_creds(ship_node='861260459919982593')
        adapter = WalmartAdapter(store)
        adapter.update_inventory('WM-SHIP', 2)

        method, url = session.request.call_args[0][:2]
        self.assertEqual(method, 'PUT')
        self.assertIn('/v3/inventories/WM-SHIP', url)
        body = session.request.call_args[1]['json']
        self.assertEqual(body['inventories']['nodes'][0]['shipNode'], '861260459919982593')
        self.assertEqual(body['inventories']['nodes'][0]['inputQty']['amount'], 2)

    @patch('store_adapters.walmart_adapter.requests.Session')
    def test_update_inventory_auto_discovers_ship_node(self, session_cls):
        session = session_cls.return_value
        _mock_token(session)

        def request_side_effect(method, url, **kwargs):
            resp = MagicMock(status_code=200, text='{}')
            resp.json.return_value = {}
            if method == 'PUT' and url.endswith('/v3/inventory'):
                resp.status_code = 400
                resp.text = '{"error":"ship node required"}'
            elif method == 'GET' and '/v3/inventories/WM-AUTO' in url:
                resp.status_code = 200
                resp.text = json.dumps({'sku': 'WM-AUTO', 'nodes': [{'shipNode': '999888777'}]})
                resp.json.return_value = {'sku': 'WM-AUTO', 'nodes': [{'shipNode': '999888777'}]}
            return resp

        session.request.side_effect = request_side_effect

        store = _store()
        store.api_token = _oauth_creds()
        adapter = WalmartAdapter(store)
        adapter.update_inventory('WM-AUTO', 4)

        put_calls = [c for c in session.request.call_args_list if c[0][0] == 'PUT']
        self.assertEqual(len(put_calls), 2)
        self.assertTrue(put_calls[0][0][1].endswith('/v3/inventory'))
        self.assertIn('/v3/inventories/WM-AUTO', put_calls[1][0][1])
        body = put_calls[1][1]['json']
        self.assertEqual(body['inventories']['nodes'][0]['shipNode'], '999888777')

    @patch('store_adapters.walmart_adapter.requests.Session')
    def test_lookup_uses_items_query_by_sku(self, session_cls):
        session = session_cls.return_value
        _mock_token(session)
        session.request.return_value = MagicMock(status_code=200, text='{"items": []}')

        store = _store()
        store.api_token = _oauth_creds()
        adapter = WalmartAdapter(store)
        self.assertEqual(adapter.lookup_listing_by_sku('WM-003'), 'WM-003')

        method, url = session.request.call_args[0][:2]
        self.assertEqual(method, 'GET')
        self.assertIn('/v3/items?sku=WM-003', url)

    @patch('store_adapters.walmart_adapter.requests.Session')
    def test_update_product_uses_listing_ship_node_kwarg(self, session_cls):
        session = session_cls.return_value
        _mock_token(session)
        session.request.return_value = MagicMock(status_code=200, text='{}')

        store = _store()
        store.api_token = _oauth_creds()
        adapter = WalmartAdapter(store)
        adapter.update_product(
            'WM-FC',
            price=Decimal('19.99'),
            stock=3,
            ship_node='861260459919982593',
        )

        inv_call = [c for c in session.request.call_args_list if c[0][0] == 'PUT'][-1]
        self.assertIn('/v3/inventories/WM-FC', inv_call[0][1])
        self.assertEqual(
            inv_call[1]['json']['inventories']['nodes'][0]['shipNode'],
            '861260459919982593',
        )

    def test_adapter_push_kwargs_includes_ship_node_from_pm(self):
        from catalog.marketplace_rrp import adapter_push_kwargs

        pm = MagicMock()
        pm.fulfillment_center_id = '861260459919982593'
        pm.product_id = 1
        kwargs = adapter_push_kwargs(_store(), pm, Decimal('10.00'), 5)
        self.assertEqual(kwargs['ship_node'], '861260459919982593')
        self.assertEqual(kwargs['stock'], 5)

    @patch('store_adapters.walmart_adapter.requests.Session')
    def test_update_product_sends_price_and_inventory(self, session_cls):
        session = session_cls.return_value
        _mock_token(session)
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
