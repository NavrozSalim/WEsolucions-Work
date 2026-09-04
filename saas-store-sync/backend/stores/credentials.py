"""Marketplace credential JSON validation and live connection checks for store create/update."""
from __future__ import annotations

import json
from types import SimpleNamespace

from rest_framework.exceptions import ValidationError

SEARS_AUTH_KEYS = ('seller_id', 'email', 'secret_key')
SEARS_REQUIRED_KEYS = SEARS_AUTH_KEYS  # location_id optional at connection time
WALMART_REQUIRED_KEYS = ('client_id', 'client_secret')
ETSY_TOKEN_KEYS = ('access_token', 'refresh_token')


def marketplace_kind(marketplace) -> str:
    if marketplace is None:
        return ''
    code = (getattr(marketplace, 'code', None) or '').strip().lower()
    name = (getattr(marketplace, 'name', None) or '').strip().lower()
    if code in ('sears', 'walmart', 'kogan', 'mydeal', 'reverb', 'lasoo', 'bunnings', 'etsy'):
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
    if 'bunnings' in name:
        return 'bunnings'
    if 'etsy' in name:
        return 'etsy'
    return code or name


def requires_structured_credentials(marketplace) -> bool:
    return marketplace_kind(marketplace) in ('sears', 'walmart', 'etsy')


def _etsy_has_api_key(data: dict) -> bool:
    api_key = (
        data.get('api_key')
        or data.get('x_api_key')
        or data.get('x-api-key')
        or ''
    )
    if str(api_key).strip():
        return True
    keystring = str(data.get('keystring') or data.get('client_id') or '').strip()
    secret = str(data.get('shared_secret') or data.get('client_secret') or '').strip()
    return bool(keystring and secret)


def _etsy_has_oauth_token(data: dict) -> bool:
    return any(str(data.get(k) or '').strip() for k in ETSY_TOKEN_KEYS)

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
    elif kind == 'etsy':
        if not _etsy_has_api_key(data):
            raise ValidationError({
                'api_token': (
                    'Etsy credentials must include api_key as "keystring:shared_secret" '
                    '(or keystring + shared_secret). '
                    'Example: {"api_key":"keystring:secret","access_token":"...","refresh_token":"...","shop_id":"..."}'
                ),
            })
        if not _etsy_has_oauth_token(data):
            raise ValidationError({
                'api_token': (
                    'Etsy credentials must include access_token and/or refresh_token. '
                    'Example: {"api_key":"keystring:secret","access_token":"...","refresh_token":"...","shop_id":"..."}'
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


def verify_bunnings_connection(store) -> tuple[bool, str | None]:
    """Verify Bunnings Mirakl credentials via GET /api/hierarchies (H11)."""
    from listings.bunnings.client import BunningsClient
    from listings.errors import MarketplaceError

    try:
        client = BunningsClient(store)
    except MarketplaceError as exc:
        return False, str(exc)

    result = client.verify_connection()
    if result.ok:
        return True, result.message or f'Bunnings {client.environment} connection successful.'
    return False, result.message or 'Bunnings rejected these credentials.'


def verify_mydeal_connection(store) -> tuple[bool, str | None]:
    """Verify MyDeal (WMP) client + seller credentials via token + products list."""
    from listings.errors import MarketplaceError
    from listings.mydeal.client import MyDealClient

    method = (getattr(store, 'mydeal_setup_method', None) or 'upload').strip().lower()
    if method != 'api':
        return True, 'MyDeal upload mode — API credentials not required.'

    try:
        client = MyDealClient(store)
    except MarketplaceError as exc:
        return False, str(exc)

    result = client.verify_connection()
    if result.ok:
        return True, f'MyDeal {client.environment} connection successful.'
    return False, result.message or 'MyDeal rejected these credentials.'


def verify_store_connection(store) -> tuple[bool, str | None]:
    """Call the marketplace adapter to verify credentials. Returns (ok, error_message)."""
    from store_adapters import get_adapter

    marketplace = getattr(store, 'marketplace', None)
    kind = marketplace_kind(marketplace)
    if kind == 'lasoo':
        return verify_lasoo_connection(store)
    if kind == 'mydeal':
        return verify_mydeal_connection(store)
    if kind == 'bunnings':
        return verify_bunnings_connection(store)

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
        if kind == 'etsy' and hasattr(adapter, 'test_etsy_connection'):
            ok, msg, _shop = adapter.test_etsy_connection()
            return ok, None if ok else msg
        if getattr(adapter, 'validate_connection', lambda: False)():
            return True, None
        if kind == 'sears':
            from store_adapters.sears_adapter import MSG_SEARS_INVALID_CREDS
            return False, MSG_SEARS_INVALID_CREDS
        if kind == 'walmart':
            from store_adapters.walmart_adapter import MSG_WALMART_INVALID_CREDS
            return False, MSG_WALMART_INVALID_CREDS
        if kind == 'etsy':
            from store_adapters.etsy_adapter import MSG_ETSY_INVALID_CREDS
            return False, MSG_ETSY_INVALID_CREDS
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


def verify_etsy_credentials_from_token(api_token: str) -> tuple[bool, str | None]:
    """Test Etsy JSON credentials before a store is saved (create flow)."""
    from types import SimpleNamespace

    from store_adapters.etsy_adapter import EtsyAdapter

    store = SimpleNamespace(
        api_token=api_token,
        marketplace=SimpleNamespace(code='etsy', name='Etsy'),
        id=None,
    )
    adapter = EtsyAdapter(store)
    ok, msg, _shop = adapter.test_etsy_connection()
    return ok, None if ok else msg