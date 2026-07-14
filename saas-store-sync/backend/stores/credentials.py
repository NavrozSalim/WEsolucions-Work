"""Marketplace credential JSON validation and live connection checks for store create/update."""
from __future__ import annotations

import json
from types import SimpleNamespace

from rest_framework.exceptions import ValidationError

SEARS_AUTH_KEYS = ('seller_id', 'email', 'secret_key')
SEARS_REQUIRED_KEYS = SEARS_AUTH_KEYS  # location_id optional at connection time
WALMART_REQUIRED_KEYS = ('client_id', 'client_secret')


def marketplace_kind(marketplace) -> str:
    if marketplace is None:
        return ''
    code = (getattr(marketplace, 'code', None) or '').strip().lower()
    name = (getattr(marketplace, 'name', None) or '').strip().lower()
    if code in ('sears', 'walmart', 'kogan', 'mydeal', 'reverb', 'lasoo'):
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
    if 'lasoo' in name:
        return 'lasoo'
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
        missing = [k for k in SEARS_AUTH_KEYS if not str(data.get(k) or '').strip()]
        if missing:
            raise ValidationError({
                'api_token': (
                    'Sears credentials must include seller_id, email, and secret_key. '
                    f'Missing: {", ".join(missing)}. '
                    'Example: {"seller_id":"...","email":"...","secret_key":"...","location_id":"..."} '
                    '(location_id is optional for connection but required for inventory sync).'
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


def verify_lasoo_connection(store) -> tuple[bool, str | None]:
    """Verify Lasoo AuthKey by calling an authenticated Variants Search."""
    from listings.errors import MarketplaceError
    from listings.lasoo.client import LasooClient
    from listings.lasoo.queries import build_payload

    try:
        client = LasooClient(store)
    except MarketplaceError as exc:
        return False, str(exc)

    payload = build_payload(
        'variants_search',
        data={'page': 1, 'take': 1},
        auth=client.auth_key,
    )
    result = client.send('variants_search', payload)
    if result.ok:
        return True, f'Lasoo {client.environment} connection successful.'
    return False, result.message or 'Lasoo rejected these credentials.'


def verify_store_connection(store) -> tuple[bool, str | None]:
    """Call the marketplace adapter to verify credentials. Returns (ok, error_message)."""
    from store_adapters import get_adapter

    marketplace = getattr(store, 'marketplace', None)
    kind = marketplace_kind(marketplace)
    if kind == 'lasoo':
        return verify_lasoo_connection(store)

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
        if kind == 'walmart' and hasattr(adapter, 'test_walmart_connection'):
            ok, msg, _code = adapter.test_walmart_connection()
            return ok, None if ok else msg
        if kind == 'sears' and hasattr(adapter, 'test_sears_connection'):
            ok, msg, _code, _loc = adapter.test_sears_connection()
            return ok, msg
        if getattr(adapter, 'validate_connection', lambda: False)():
            return True, None
        if kind == 'sears':
            from store_adapters.sears_adapter import MSG_SEARS_INVALID_CREDS
            return False, MSG_SEARS_INVALID_CREDS
        if kind == 'walmart':
            from store_adapters.walmart_adapter import MSG_WALMART_INVALID_CREDS
            return False, MSG_WALMART_INVALID_CREDS
        return False, 'Marketplace rejected these credentials.'
    except Exception as exc:
        return False, str(exc)[:500]


def verify_walmart_credentials_from_token(
    api_token: str,
    *,
    region: str = 'USA',
    use_sandbox: bool = False,
) -> tuple[bool, str | None]:
    """Test Walmart JSON credentials before a store is saved (create flow)."""
    from types import SimpleNamespace

    from store_adapters.walmart_adapter import WalmartAdapter

    store = SimpleNamespace(
        api_token=api_token,
        marketplace=SimpleNamespace(code='walmart', name='Walmart'),
        region=region,
        use_sandbox=use_sandbox,
        id=None,
    )
    adapter = WalmartAdapter(store)
    ok, msg, _code = adapter.test_walmart_connection()
    return ok, None if ok else msg


def verify_sears_credentials_from_token(api_token: str) -> tuple[bool, str | None]:
    """Test Sears JSON credentials before a store is saved (create flow)."""
    from types import SimpleNamespace

    from store_adapters.sears_adapter import SearsAdapter

    store = SimpleNamespace(
        api_token=api_token,
        marketplace=SimpleNamespace(code='sears', name='Sears'),
        id=None,
    )
    adapter = SearsAdapter(store)
    ok, msg, _code, _loc = adapter.test_sears_connection()
    return ok, msg
