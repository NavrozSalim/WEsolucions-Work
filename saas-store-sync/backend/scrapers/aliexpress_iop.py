"""AliExpress Open Platform (IOP) client for api-sg.aliexpress.com."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

DEFAULT_IOP_GATEWAY = 'https://api-sg.aliexpress.com'
IOP_SIGN_METHOD = 'sha256'

TOKEN_CREATE_PATH = '/auth/token/security/create'
TOKEN_REFRESH_PATH = '/auth/token/refresh'
DS_PRODUCT_GET_METHOD = 'aliexpress.ds.product.get'


class AliExpressIOPError(Exception):
    def __init__(self, message: str, *, response_body: str | None = None):
        super().__init__(message)
        self.response_body = response_body


def get_iop_gateway() -> str:
    url = (getattr(settings, 'ALIEXPRESS_IOP_GATEWAY', '') or '').strip()
    return url or DEFAULT_IOP_GATEWAY


def iop_timestamp_ms() -> str:
    return str(int(time.time() * 1000))


def method_to_iop_path(api_method: str) -> str:
    """Map dotted API name to IOP REST path (aliexpress.ds.product.get → /aliexpress/ds/product/get)."""
    name = (api_method or '').strip().lstrip('/')
    if not name:
        raise ValueError('api_method is required')
    if name.startswith('auth/'):
        return f'/{name}'
    return '/' + name.replace('.', '/')


def sign_iop_request(api_path: str, params: dict[str, Any], app_secret: str) -> str:
    """
    IOP HMAC-SHA256 signature: api_path + sorted(key+value pairs), excluding sign.
    """
    items = sorted(
        (k, v)
        for k, v in params.items()
        if k != 'sign' and v is not None and str(v) != ''
    )
    base = api_path + ''.join(f'{k}{v}' for k, v in items)
    digest = hmac.new(app_secret.encode('utf-8'), base.encode('utf-8'), hashlib.sha256)
    return digest.hexdigest().upper()


def _app_credentials() -> tuple[str, str]:
    app_key = (getattr(settings, 'ALIEXPRESS_APP_KEY', '') or '').strip()
    app_secret = (getattr(settings, 'ALIEXPRESS_APP_SECRET', '') or '').strip()
    if not app_key or not app_secret:
        raise AliExpressIOPError(
            'AliExpress API credentials not configured (set ALIEXPRESS_APP_KEY and ALIEXPRESS_APP_SECRET)'
        )
    return app_key, app_secret


def _base_iop_params() -> dict[str, str]:
    app_key, _ = _app_credentials()
    return {
        'app_key': app_key,
        'sign_method': IOP_SIGN_METHOD,
        'timestamp': iop_timestamp_ms(),
    }


def iop_system_request(api_path: str, business_params: dict[str, Any], *, timeout: int = 30) -> dict:
    """POST to {gateway}/rest{api_path} for token create/refresh (no access_token)."""
    _, app_secret = _app_credentials()
    path = api_path if api_path.startswith('/') else f'/{api_path}'
    params = _base_iop_params()
    params.update({k: str(v) for k, v in business_params.items() if v is not None and str(v) != ''})
    params['sign'] = sign_iop_request(path, params, app_secret)
    url = f'{get_iop_gateway().rstrip("/")}/rest{path}'
    return _post_iop(url, params, timeout=timeout)


def iop_business_request(
    api_method: str,
    business_params: dict[str, Any],
    access_token: str,
    *,
    timeout: int = 30,
) -> dict:
    """POST to {gateway}/rest/{dotted/path} for business APIs (requires access_token)."""
    _, app_secret = _app_credentials()
    path = method_to_iop_path(api_method)
    params = _base_iop_params()
    params['access_token'] = access_token
    params.update({k: str(v) for k, v in business_params.items() if v is not None and str(v) != ''})
    params['sign'] = sign_iop_request(path, params, app_secret)
    url = f'{get_iop_gateway().rstrip("/")}/rest{path}'
    return _post_iop(url, params, timeout=timeout)


def _post_iop(url: str, params: dict[str, str], *, timeout: int) -> dict:
    try:
        resp = requests.post(url, data=params, timeout=timeout)
    except requests.RequestException as exc:
        raise AliExpressIOPError(str(exc)) from exc
    body = resp.text or ''
    if resp.status_code >= 400:
        raise AliExpressIOPError(
            f'AliExpress IOP HTTP {resp.status_code}',
            response_body=body[:500],
        )
    try:
        data = resp.json()
    except json.JSONDecodeError as exc:
        raise AliExpressIOPError('AliExpress IOP returned non-JSON response', response_body=body[:500]) from exc
    err = _extract_iop_error(data)
    if err:
        raise AliExpressIOPError(err, response_body=body[:500])
    return data


def _extract_iop_error(data: dict) -> str | None:
    if not isinstance(data, dict):
        return 'Invalid IOP response'
    if data.get('error_response'):
        err = data['error_response']
        if isinstance(err, dict):
            code = err.get('code') or err.get('type') or ''
            msg = err.get('msg') or err.get('message') or 'API error'
            return f'{code}: {msg}'.strip(': ')
    code = data.get('code') or data.get('error_code') or ''
    if str(code) not in ('', '0', '200'):
        msg = data.get('message') or data.get('msg') or data.get('error_msg') or 'API error'
        return f'{code}: {msg}'.strip(': ')
    if data.get('type') and str(data.get('type')).upper() == 'ISV':
        return data.get('message') or data.get('code') or 'ISV error'
    return None


def create_token_from_code(code: str, *, uuid: str | None = None) -> dict:
    """Exchange OAuth authorization code for access/refresh tokens."""
    params: dict[str, Any] = {'code': code}
    if uuid:
        params['uuid'] = uuid
    return iop_system_request(TOKEN_CREATE_PATH, params)


def refresh_access_token(refresh_token: str) -> dict:
    """Refresh an expired access token."""
    return iop_system_request(TOKEN_REFRESH_PATH, {'refresh_token': refresh_token})


def fetch_ds_product(product_id: str, store_region: str | None, access_token: str) -> dict | None:
    """Call aliexpress.ds.product.get and return the ``result`` object, or None."""
    from scrapers.aliexpress_markets import get_aliexpress_market

    pid = str(product_id or '').strip()
    if not pid:
        return None
    market = get_aliexpress_market(store_region)
    data = iop_business_request(
        DS_PRODUCT_GET_METHOD,
        {
            'product_id': pid,
            'ship_to_country': market['country'],
            'target_currency': market['target_currency'],
            'target_language': market['target_language'],
        },
        access_token,
    )
    return _extract_ds_product_result(data)


def _extract_ds_product_result(data: dict) -> dict | None:
    if not isinstance(data, dict):
        return None
    for key in (
        'aliexpress_ds_product_get_response',
        'aliexpressDsProductGetResponse',
    ):
        root = data.get(key)
        if isinstance(root, dict):
            result = root.get('result')
            if isinstance(result, dict):
                return result
    result = data.get('result')
    return result if isinstance(result, dict) else None
