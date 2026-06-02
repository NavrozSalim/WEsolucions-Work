"""Tests for Sears/Walmart store credential JSON validation."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

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
        raw = json.dumps({'seller_id': '1', 'email': 'a@b.com'})
        with self.assertRaises(ValidationError) as ctx:
            validate_api_token_shape(_mkt('sears'), raw)
        self.assertIn('secret_key', str(ctx.exception.detail))

    def test_sears_valid_normalizes_json(self):
        raw = json.dumps({
            'seller_id': '10673110',
            'email': 'seller@example.com',
            'secret_key': 'abc123',
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
