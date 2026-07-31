"""Tests for Sears/Walmart store credential JSON validation."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from store_adapters.sears_adapter import (
    MSG_SEARS_CONNECTED,
    MSG_SEARS_INVALID_CREDS,
    SEARS_AUTH_PROBE_PATH,
    SearsAdapter,
    SearsAPIError,
)
from store_adapters.walmart_adapter import (
    MSG_WALMART_CONNECTED,
    MSG_WALMART_FORBIDDEN,
    MSG_WALMART_INVALID_CREDS,
    WALMART_ITEMS_PROBE_PATH,
    WalmartAdapter,
    WalmartAPIError,
)

from stores.credentials import (
    parse_credentials_json,
    requires_structured_credentials,
    validate_api_token_shape,
    verify_store_connection,
)


def _mkt(code: str):
    return SimpleNamespace(code=code, name=code.title())


class CredentialShapeTests(SimpleTestCase):
    def test_requires_structured_credentials(self):
        self.assertTrue(requires_structured_credentials(_mkt('sears')))
        self.assertTrue(requires_structured_credentials(_mkt('walmart')))
        self.assertFalse(requires_structured_credentials(_mkt('reverb')))

    def test_sears_missing_secret_key(self):
        raw = json.dumps({'seller_id': '1', 'email': 'a@b.com', 'location_id': '931015916'})
        with self.assertRaises(ValidationError) as ctx:
            validate_api_token_shape(_mkt('sears'), raw)
        self.assertIn('secret_key', str(ctx.exception.detail))

    def test_sears_missing_location_id_allowed(self):
        raw = json.dumps({'seller_id': '1', 'email': 'a@b.com', 'secret_key': 'key'})
        out = validate_api_token_shape(_mkt('sears'), raw)
        data = json.loads(out)
        self.assertEqual(data['secret_key'], 'key')
        self.assertNotIn('location_id', data)

    def test_sears_valid_normalizes_json(self):
        raw = json.dumps({
            'seller_id': '10673110',
            'email': 'seller@example.com',
            'secret_key': 'abc123',
            'location_id': '931015916',
        })
        out = validate_api_token_shape(_mkt('sears'), raw)
        data = json.loads(out)
        self.assertEqual(data['seller_id'], '10673110')

    def test_walmart_missing_client_secret(self):
        raw = json.dumps({'client_id': 'cid'})
        with self.assertRaises(ValidationError) as ctx:
            validate_api_token_shape(_mkt('walmart'), raw)
        self.assertIn('client_secret', str(ctx.exception.detail))

    def test_walmart_valid(self):
        raw = json.dumps({'client_id': 'a', 'client_secret': 'b'})
        out = validate_api_token_shape(_mkt('walmart'), raw)
        self.assertIn('client_id', out)

    def test_not_json_object_rejected(self):
        with self.assertRaises(ValidationError):
            parse_credentials_json('plain-token')


class VerifyConnectionTests(SimpleTestCase):
    @patch('store_adapters.walmart_adapter.WalmartAdapter.test_walmart_connection')
    def test_verify_walmart_success(self, mock_test):
        mock_test.return_value = (True, MSG_WALMART_CONNECTED, 200)
        store = SimpleNamespace(
            api_token=json.dumps({'client_id': 'x', 'client_secret': 'y'}),
            marketplace=_mkt('walmart'),
            region='USA',
            use_sandbox=False,
        )
        ok, msg = verify_store_connection(store)
        self.assertTrue(ok)
        self.assertIsNone(msg)

    @patch('store_adapters.walmart_adapter.WalmartAdapter.test_walmart_connection')
    def test_verify_walmart_api_failure(self, mock_test):
        mock_test.return_value = (False, MSG_WALMART_INVALID_CREDS, 401)
        store = SimpleNamespace(
            api_token=json.dumps({'client_id': 'x', 'client_secret': 'y'}),
            marketplace=_mkt('walmart'),
            region='USA',
            use_sandbox=False,
        )
        ok, msg = verify_store_connection(store)
        self.assertFalse(ok)
        self.assertEqual(msg, MSG_WALMART_INVALID_CREDS)


class SearsValidateConnectionTests(SimpleTestCase):
    @patch('store_adapters.sears_adapter.SearsAdapter._request')
    def test_test_sears_connection_auth_without_location_id(self, mock_request):
        mock_request.return_value = '<orders></orders>'
        store = SimpleNamespace(
            api_token=json.dumps({
                'seller_id': '1',
                'email': 'a@b.com',
                'secret_key': 'key',
            }),
            marketplace=_mkt('sears'),
            id=None,
        )
        adapter = SearsAdapter(store)
        ok, msg, code, loc = adapter.test_sears_connection()
        self.assertTrue(ok)
        self.assertEqual(msg, MSG_SEARS_CONNECTED)
        self.assertEqual(code, 200)
        self.assertIsNone(loc)
        mock_request.assert_called_once()
        args, kwargs = mock_request.call_args
        self.assertEqual(args[0], 'GET')
        self.assertEqual(args[1], SEARS_AUTH_PROBE_PATH)

    @patch('store_adapters.sears_adapter.SearsAdapter._request')
    def test_test_sears_connection_invalid_creds(self, mock_request):
        mock_request.side_effect = SearsAPIError(
            'Sears API GET: 401',
            status_code=401,
            response_body='Unauthorized',
        )
        store = SimpleNamespace(
            api_token=json.dumps({
                'seller_id': '1',
                'email': 'a@b.com',
                'secret_key': 'bad',
            }),
            marketplace=_mkt('sears'),
            id=None,
        )
        adapter = SearsAdapter(store)
        ok, msg, code, loc = adapter.test_sears_connection()
        self.assertFalse(ok)
        self.assertEqual(msg, MSG_SEARS_INVALID_CREDS)
        self.assertEqual(code, 401)

    @patch('store_adapters.sears_adapter.SearsAdapter._verify_location_id_optional')
    @patch('store_adapters.sears_adapter.SearsAdapter._request')
    def test_test_sears_connection_location_warning(self, mock_request, mock_loc):
        mock_request.return_value = '<orders></orders>'
        mock_loc.return_value = False
        store = SimpleNamespace(
            api_token=json.dumps({
                'seller_id': '1',
                'email': 'a@b.com',
                'secret_key': 'key',
                'location_id': '999',
            }),
            marketplace=_mkt('sears'),
            id=None,
        )
        adapter = SearsAdapter(store)
        ok, msg, code, loc = adapter.test_sears_connection()
        self.assertTrue(ok)
        self.assertIn(MSG_SEARS_CONNECTED, msg)
        self.assertIn('location_id', msg)
        self.assertFalse(loc)

    @patch('store_adapters.sears_adapter.SearsAdapter.test_sears_connection')
    def test_verify_sears_success(self, mock_test):
        mock_test.return_value = (True, MSG_SEARS_CONNECTED, 200, None)
        store = SimpleNamespace(
            api_token=json.dumps({'seller_id': '1', 'email': 'a@b.com', 'secret_key': 'k'}),
            marketplace=_mkt('sears'),
        )
        ok, msg = verify_store_connection(store)
        self.assertTrue(ok)
        self.assertEqual(msg, MSG_SEARS_CONNECTED)


class WalmartStoreOnlyCredentialTests(SimpleTestCase):
    @patch.dict(
        os.environ,
        {'WALMART_CLIENT_ID': 'env-id', 'WALMART_CLIENT_SECRET': 'env-secret'},
        clear=False,
    )
    def test_client_credentials_ignore_env_when_validating_store_json(self):
        store = SimpleNamespace(
            api_token=json.dumps({'client_id': 'store-id', 'client_secret': 'store-secret'}),
            marketplace=_mkt('walmart'),
            region='USA',
            use_sandbox=False,
        )
        adapter = WalmartAdapter(store)
        adapter._validate_using_store_json_only = True
        cid, secret = adapter._client_credentials()
        self.assertEqual(cid, 'store-id')
        self.assertEqual(secret, 'store-secret')

    @patch.dict(
        os.environ,
        {'WALMART_CLIENT_ID': 'env-id', 'WALMART_CLIENT_SECRET': 'env-secret'},
        clear=False,
    )
    def test_client_credentials_use_env_during_normal_sync_when_store_empty(self):
        store = SimpleNamespace(
            api_token='{}',
            marketplace=_mkt('walmart'),
            region='USA',
            use_sandbox=False,
        )
        adapter = WalmartAdapter(store)
        cid, secret = adapter._client_credentials()
        self.assertEqual(cid, 'env-id')
        self.assertEqual(secret, 'env-secret')

    def test_client_credentials_empty_when_validating_empty_store_json(self):
        store = SimpleNamespace(
            api_token='{}',
            marketplace=_mkt('walmart'),
            region='USA',
            use_sandbox=False,
        )
        adapter = WalmartAdapter(store)
        adapter._validate_using_store_json_only = True
        cid, secret = adapter._client_credentials()
        self.assertIsNone(cid)
        self.assertIsNone(secret)

    @patch.dict(
        os.environ,
        {'WALMART_CLIENT_ID': 'env-id', 'WALMART_CLIENT_SECRET': 'env-secret'},
        clear=False,
    )
    @patch('store_adapters.walmart_adapter.WalmartAdapter._refresh_access_token')
    def test_validate_connection_fails_when_store_secret_wrong(self, mock_refresh):
        mock_refresh.side_effect = WalmartAPIError(
            'Walmart token request failed: 401',
            status_code=401,
            response_body='Unauthorized',
        )
        store = SimpleNamespace(
            api_token=json.dumps({'client_id': 'bad', 'client_secret': 'bad'}),
            marketplace=_mkt('walmart'),
            region='USA',
            use_sandbox=False,
        )
        adapter = WalmartAdapter(store)
        ok, msg, code = adapter.test_walmart_connection()
        self.assertFalse(ok)
        self.assertEqual(msg, MSG_WALMART_INVALID_CREDS)
        self.assertEqual(code, 401)

    @patch('store_adapters.walmart_adapter.WalmartAdapter._request')
    @patch('store_adapters.walmart_adapter.WalmartAdapter._refresh_access_token')
    def test_test_walmart_connection_uses_items_endpoint(self, mock_refresh, mock_request):
        mock_refresh.return_value = 'token'
        mock_request.return_value = {'items': []}
        store = SimpleNamespace(
            api_token=json.dumps({'client_id': 'a', 'client_secret': 'b'}),
            marketplace=_mkt('walmart'),
            region='USA',
            use_sandbox=False,
        )
        adapter = WalmartAdapter(store)
        ok, msg, code = adapter.test_walmart_connection()
        self.assertTrue(ok)
        self.assertEqual(msg, MSG_WALMART_CONNECTED)
        self.assertEqual(code, 200)
        mock_request.assert_called_once_with('GET', WALMART_ITEMS_PROBE_PATH)

    @patch('store_adapters.walmart_adapter.WalmartAdapter._request')
    @patch('store_adapters.walmart_adapter.WalmartAdapter._refresh_access_token')
    def test_test_walmart_connection_403_message(self, mock_refresh, mock_request):
        mock_refresh.return_value = 'token'
        mock_request.side_effect = WalmartAPIError(
            'Walmart API GET /v3/items: 403',
            status_code=403,
            response_body='Forbidden',
        )
        store = SimpleNamespace(
            api_token=json.dumps({'client_id': 'a', 'client_secret': 'b'}),
            marketplace=_mkt('walmart'),
            region='USA',
            use_sandbox=False,
        )
        adapter = WalmartAdapter(store)
        ok, msg, code = adapter.test_walmart_connection()
        self.assertFalse(ok)
        self.assertEqual(msg, MSG_WALMART_FORBIDDEN)
        self.assertEqual(code, 403)


class LasooConnectionTests(SimpleTestCase):
    def test_marketplace_kind_lasoo(self):
        from stores.credentials import marketplace_kind
        self.assertEqual(marketplace_kind(_mkt('lasoo')), 'lasoo')
        self.assertEqual(marketplace_kind(SimpleNamespace(code='', name='Lasoo')), 'lasoo')

    @patch('listings.lasoo.client.LasooClient.send')
    def test_verify_store_connection_lasoo_ok(self, mock_send):
        mock_send.return_value = SimpleNamespace(ok=True, message='Success.')
        store = SimpleNamespace(
            name='P&P lesso',
            marketplace=_mkt('lasoo'),
            lasoo_environment='staging',
            lasoo_staging_base_url='https://stage.api.lasoo.com.au',
            lasoo_staging_auth_key='test-key',
            lasoo_production_base_url='',
            lasoo_production_auth_key='',
        )
        ok, msg = verify_store_connection(store)
        self.assertTrue(ok)
        self.assertIn('staging', msg or '')
        mock_send.assert_called_once()

    def test_verify_store_connection_lasoo_missing_key(self):
        store = SimpleNamespace(
            name='P&P lesso',
            marketplace=_mkt('lasoo'),
            lasoo_environment='staging',
            lasoo_staging_base_url='https://stage.api.lasoo.com.au',
            lasoo_staging_auth_key='',
            lasoo_production_base_url='',
            lasoo_production_auth_key='',
        )
        ok, msg = verify_store_connection(store)
        self.assertFalse(ok)
        self.assertIn('AuthKey', msg or '')


class MyDealConnectionTests(SimpleTestCase):
    def _store(self, **overrides):
        base = dict(
            name='TFS MyDeal',
            marketplace=_mkt('mydeal'),
            mydeal_setup_method='api',
            mydeal_environment='sandbox',
            mydeal_sandbox_base_url='https://sandbox.example.mydeal',
            mydeal_sandbox_client_id='cid',
            mydeal_sandbox_client_secret='csecret',
            mydeal_sandbox_seller_id='sid',
            mydeal_sandbox_seller_token='stoken',
            mydeal_production_base_url='',
            mydeal_production_client_id='',
            mydeal_production_client_secret='',
            mydeal_production_seller_id='',
            mydeal_production_seller_token='',
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_marketplace_kind_mydeal(self):
        from stores.credentials import marketplace_kind
        self.assertEqual(marketplace_kind(_mkt('mydeal')), 'mydeal')
        self.assertEqual(marketplace_kind(SimpleNamespace(code='', name='MyDeal')), 'mydeal')

    def test_verify_upload_mode_skips_api(self):
        store = self._store(mydeal_setup_method='upload')
        ok, msg = verify_store_connection(store)
        self.assertTrue(ok)
        self.assertIn('upload', (msg or '').lower())

    def test_verify_missing_credentials(self):
        store = self._store(mydeal_sandbox_client_id='')
        ok, msg = verify_store_connection(store)
        self.assertFalse(ok)
        self.assertIn('ClientID', msg or '')

    @patch('listings.mydeal.client.MyDealClient.verify_connection')
    def test_verify_store_connection_mydeal_ok(self, mock_verify):
        mock_verify.return_value = SimpleNamespace(ok=True, message='MyDeal response: Success.')
        store = self._store()
        ok, msg = verify_store_connection(store)
        self.assertTrue(ok)
        self.assertIn('sandbox', msg or '')
        mock_verify.assert_called_once()

    @patch('listings.mydeal.client.requests.post')
    @patch('listings.mydeal.client.requests.request')
    def test_mydeal_client_token_and_products(self, mock_request, mock_post):
        from listings.mydeal.client import MyDealClient

        token_resp = MagicMock()
        token_resp.ok = True
        token_resp.status_code = 200
        token_resp.json.return_value = {'access_token': 'tok-1', 'expires_in': 3600}
        mock_post.return_value = token_resp

        prod_resp = MagicMock()
        prod_resp.ok = True
        prod_resp.status_code = 200
        prod_resp.json.return_value = {'ResponseStatus': 'Success', 'Products': []}
        mock_request.return_value = prod_resp

        client = MyDealClient(self._store())
        result = client.verify_connection()
        self.assertTrue(result.ok)
        mock_post.assert_called()
        self.assertIn('/mydealaccesstoken', mock_post.call_args[0][0])
        token_kwargs = mock_post.call_args.kwargs
        token_body = token_kwargs.get('data') or (mock_post.call_args[1].get('data') if len(mock_post.call_args) > 1 else {})
        self.assertEqual(token_body.get('grant_type'), 'client_credentials')
        self.assertNotIn('client_id', token_body)
        self.assertNotIn('client_secret', token_body)
        self.assertEqual(token_kwargs.get('auth'), ('cid', 'csecret'))
        mock_request.assert_called()
        args, kwargs = mock_request.call_args
        self.assertEqual(args[0], 'GET')
        self.assertIn('/products', args[1])
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer tok-1')
        self.assertEqual(kwargs['headers']['SellerID'], 'sid')
        self.assertEqual(kwargs['headers']['SellerToken'], 'stoken')

    @patch('listings.mydeal.client.requests.post')
    def test_mydeal_token_gateway_forbidden_message(self, mock_post):
        from listings.errors import MarketplaceError
        from listings.mydeal.client import MyDealClient

        resp = MagicMock()
        resp.ok = False
        resp.status_code = 403
        resp.headers = {'x-amzn-ErrorType': 'ForbiddenException'}
        resp.json.return_value = {'message': 'Forbidden'}
        mock_post.return_value = resp

        client = MyDealClient(self._store())
        with self.assertRaises(MarketplaceError) as ctx:
            client.get_access_token(force=True)
        self.assertIn('allowlist', str(ctx.exception).lower())
