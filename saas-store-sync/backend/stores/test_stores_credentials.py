"""Tests for Sears/Walmart store credential JSON validation."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from store_adapters.sears_adapter import SearsAdapter
from store_adapters.walmart_adapter import WalmartAdapter, WalmartAPIError

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

    def test_sears_missing_location_id(self):
        raw = json.dumps({'seller_id': '1', 'email': 'a@b.com', 'secret_key': 'key'})
        with self.assertRaises(ValidationError) as ctx:
            validate_api_token_shape(_mkt('sears'), raw)
        self.assertIn('location_id', str(ctx.exception.detail))

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
    @patch('store_adapters.walmart_adapter.WalmartAdapter.validate_connection', return_value=True)
    def test_verify_walmart_success(self, _mock_val):
        store = SimpleNamespace(
            api_token=json.dumps({'client_id': 'x', 'client_secret': 'y'}),
            marketplace=_mkt('walmart'),
            region='USA',
            use_sandbox=False,
        )
        ok, msg = verify_store_connection(store)
        self.assertTrue(ok)
        self.assertIsNone(msg)

    @patch('store_adapters.walmart_adapter.WalmartAdapter.validate_connection', return_value=False)
    def test_verify_walmart_api_failure(self, _mock_val):
        store = SimpleNamespace(
            api_token=json.dumps({'client_id': 'x', 'client_secret': 'y'}),
            marketplace=_mkt('walmart'),
            region='USA',
            use_sandbox=False,
        )
        ok, msg = verify_store_connection(store)
        self.assertFalse(ok)
        self.assertIn('Walmart', msg or '')


class SearsValidateConnectionTests(SimpleTestCase):
    def test_validate_connection_requires_location_id_for_lmp(self):
        store = SimpleNamespace(
            api_token=json.dumps({
                'seller_id': '1',
                'email': 'a@b.com',
                'secret_key': 'key',
            }),
            marketplace=_mkt('sears'),
        )
        adapter = SearsAdapter(store)
        self.assertFalse(adapter.validate_connection())


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
        mock_refresh.side_effect = WalmartAPIError('Walmart token request failed: 401')
        store = SimpleNamespace(
            api_token=json.dumps({'client_id': 'bad', 'client_secret': 'bad'}),
            marketplace=_mkt('walmart'),
            region='USA',
            use_sandbox=False,
        )
        adapter = WalmartAdapter(store)
        self.assertFalse(adapter.validate_connection())
