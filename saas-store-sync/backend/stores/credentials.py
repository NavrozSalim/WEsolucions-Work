"""Marketplace credential JSON validation and live connection checks for store create/update."""
from __future__ import annotations

import json
from types import SimpleNamespace

from rest_framework.exceptions import ValidationError

SEARS_REQUIRED_KEYS = ('seller_id', 'email', 'secret_key', 'location_id')
WALMART_REQUIRED_KEYS = ('client_id', 'client_secret')


def marketplace_kind(marketplace) -> str:
    if marketplace is None:
        return ''
    code = (getattr(marketplace, 'code', None) or '').strip().lower()
    name = (getattr(marketplace, 'name', None) or '').strip().lower()
    if code in ('sears', 'walmart', 'kogan', 'mydeal', 'reverb'):
        return code
    if 'walmart' in name:
        return 'walmart'
    if 'sears' in name:
        return 'sears'
    if 'kogan' in name:
        return 'kogan'
    if 'mydeal' in name:
        return 'mydeal'
    if 'reverb' in name:
        return 'reverb'
    return code or name


def requires_structured_credentials(marketplace) -> bool:
    return marketplace_kind(marketplace) in ('sears', 'walmart')


def parse_credentials_json(api_token: str) -> dict:
    raw = (api_token or '').strip()
    if not raw:
        raise ValidationError({'api_token': 'Credentials are required.'})
    if not raw.startswith('{'):
        raise ValidationError({
            'api_token': 'Credentials must be a JSON object (e.g. {"client_id": "...", "client_secret": "..."}).',
        })
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError({'api_token': f'Invalid JSON: {exc}'}) from exc
    if not isinstance(data, dict):
        raise ValidationError({'api_token': 'Credentials must be a JSON object.'})
    return data


def validate_api_token_shape(marketplace, api_token: str) -> str:
    """
    Validate required keys for Sears / Walmart. Returns normalized JSON string to store.
    """
    data = parse_credentials_json(api_token)
    kind = marketplace_kind(marketplace)

    if kind == 'sears':
        missing = [k for k in SEARS_REQUIRED_KEYS if not str(data.get(k) or '').strip()]
        if missing:
            raise ValidationError({
                'api_token': (
                    'Sears credentials must include seller_id, email, secret_key, and location_id. '
                    f'Missing: {", ".join(missing)}. '
                    'Example: {"seller_id":"...","email":"...","secret_key":"...","location_id":"..."}'
                ),
            })
    elif kind == 'walmart':
        missing = [k for k in WALMART_REQUIRED_KEYS if not str(data.get(k) or '').strip()]
        if missing:
            raise ValidationError({
                'api_token': (
                    'Walmart credentials must include client_id and client_secret. '
                    f'Missing: {", ".join(missing)}. '
                    'Example: {"client_id":"...","client_secret":"..."}'
                ),
            })
    return json.dumps(data, separators=(',', ':'))


def verify_store_connection(store) -> tuple[bool, str | None]:
    """Call the marketplace adapter to verify credentials. Returns (ok, error_message)."""
    from store_adapters import get_adapter

    marketplace = getattr(store, 'marketplace', None)
    if requires_structured_credentials(marketplace):
        try:
            validate_api_token_shape(marketplace, getattr(store, 'api_token', None) or '')
        except ValidationError as exc:
            detail = exc.detail
            if isinstance(detail, dict):
                msg = detail.get('api_token')
                if isinstance(msg, list):
                    msg = msg[0] if msg else str(detail)
            else:
                msg = str(detail)
            return False, str(msg)

    try:
        adapter = get_adapter(store)
        adapter._validate_using_store_json_only = True
        if getattr(adapter, 'validate_connection', lambda: False)():
            return True, None
        kind = marketplace_kind(marketplace)
        if kind == 'sears':
            return False, (
                'Sears rejected these credentials. Check seller_id, email, secret_key, and location_id.'
            )
        if kind == 'walmart':
            return False, 'Walmart rejected these credentials. Check client_id and client_secret.'
        return False, 'Marketplace rejected these credentials.'
    except Exception as exc:
        return False, str(exc)[:500]
