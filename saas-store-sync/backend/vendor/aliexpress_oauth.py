"""AliExpress Drop Shipping OAuth token storage and refresh."""
from __future__ import annotations

import logging
import os
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from scrapers.aliexpress_iop import AliExpressIOPError, create_token_from_code, refresh_access_token

logger = logging.getLogger(__name__)
User = get_user_model()

# Refresh slightly before expiry to avoid race during long scrape runs.
TOKEN_REFRESH_BUFFER = timedelta(minutes=5)


def _credentials_configured() -> bool:
    return bool(
        (getattr(settings, 'ALIEXPRESS_APP_KEY', '') or '').strip()
        and (getattr(settings, 'ALIEXPRESS_APP_SECRET', '') or '').strip()
    )


def get_oauth_redirect_uri(request=None) -> str:
    configured = (getattr(settings, 'ALIEXPRESS_OAUTH_REDIRECT_URI', '') or '').strip()
    if configured:
        return configured
    if request is not None:
        return request.build_absolute_uri('/api/v1/vendors/aliexpress/callback/')
    return ''


def build_authorize_url(*, redirect_uri: str, state: str) -> str:
    from urllib.parse import urlencode

    app_key = (getattr(settings, 'ALIEXPRESS_APP_KEY', '') or '').strip()
    params = {
        'response_type': 'code',
        'force_auth': 'true',
        'redirect_uri': redirect_uri,
        'client_id': app_key,
        'state': state,
    }
    gateway = (getattr(settings, 'ALIEXPRESS_IOP_GATEWAY', '') or 'https://api-sg.aliexpress.com').rstrip('/')
    return f'{gateway}/oauth/authorize?' + urlencode(params)


def _seconds_from_token_response(data: dict, *keys: str, default: int = 3600) -> int:
    for key in keys:
        raw = data.get(key)
        if raw is None or raw == '':
            continue
        try:
            return max(60, int(raw))
        except (TypeError, ValueError):
            continue
    return default


def _apply_token_response(creds, data: dict) -> None:
    now = timezone.now()
    access_token = (data.get('access_token') or data.get('accessToken') or '').strip()
    refresh_token = (data.get('refresh_token') or data.get('refreshToken') or creds.refresh_token or '').strip()
    if not access_token:
        raise AliExpressIOPError('AliExpress token response missing access_token')

    expires_in = _seconds_from_token_response(data, 'expires_in', 'expiresIn', 'expire_time', 'expireTime')
    refresh_expires_in = _seconds_from_token_response(
        data,
        'refresh_expires_in',
        'refreshExpiresIn',
        'refresh_token_valid_time',
        'refreshTokenValidTime',
        default=60 * 60 * 24 * 180,
    )

    creds.access_token = access_token
    if refresh_token:
        creds.refresh_token = refresh_token
    creds.account_id = (
        (data.get('user_id') or data.get('userId') or data.get('account') or data.get('seller_id') or creds.account_id or '')
    )
    creds.account = (data.get('account') or data.get('account_platform') or creds.account or '')[:255]
    creds.expires_at = now + timedelta(seconds=expires_in)
    creds.refresh_expires_at = now + timedelta(seconds=refresh_expires_in)
    creds.is_valid = True
    creds.save(
        update_fields=[
            'access_token',
            'refresh_token',
            'account_id',
            'account',
            'expires_at',
            'refresh_expires_at',
            'is_valid',
            'updated_at',
        ]
    )


def save_tokens_from_code(user, code: str) -> 'AliExpressOAuthCredentials':
    from vendor.models import AliExpressOAuthCredentials

    if not _credentials_configured():
        raise AliExpressIOPError('AliExpress app credentials not configured')
    data = create_token_from_code(code)
    creds, _ = AliExpressOAuthCredentials.objects.get_or_create(user=user)
    _apply_token_response(creds, data)
    return creds


def _env_access_token() -> str:
    return (getattr(settings, 'ALIEXPRESS_ACCESS_TOKEN', '') or os.getenv('ALIEXPRESS_ACCESS_TOKEN', '') or '').strip()


def get_valid_access_token(user_id=None) -> str | None:
    """
    Return a usable access token for AliExpress DS API calls.

    Resolution order:
    1. Per-user OAuth credentials (refreshed if near expiry)
    2. ``ALIEXPRESS_ACCESS_TOKEN`` env fallback (single-tenant / dev)
    """
    if not _credentials_configured():
        return None

    env_token = _env_access_token()
    if user_id is None:
        return env_token or None

    from django.core.exceptions import ValidationError
    from vendor.models import AliExpressOAuthCredentials

    try:
        user = User.objects.get(pk=user_id)
    except (User.DoesNotExist, ValidationError):
        return env_token or None

    creds = AliExpressOAuthCredentials.objects.filter(user=user, is_valid=True).first()
    if not creds or not creds.access_token:
        return env_token or None

    now = timezone.now()
    if creds.expires_at and creds.expires_at > now + TOKEN_REFRESH_BUFFER:
        return creds.access_token

    if not creds.refresh_token:
        if env_token:
            return env_token
        creds.is_valid = False
        creds.save(update_fields=['is_valid', 'updated_at'])
        return None

    if creds.refresh_expires_at and creds.refresh_expires_at <= now:
        creds.is_valid = False
        creds.save(update_fields=['is_valid', 'updated_at'])
        return env_token or None

    try:
        data = refresh_access_token(creds.refresh_token)
        _apply_token_response(creds, data)
        return creds.access_token
    except AliExpressIOPError as exc:
        logger.warning('AliExpress token refresh failed for user %s: %s', user_id, exc)
        creds.is_valid = False
        creds.save(update_fields=['is_valid', 'updated_at'])
        return env_token or None


def oauth_status_for_user(user) -> dict:
    from vendor.models import AliExpressOAuthCredentials

    if not _credentials_configured():
        return {
            'configured': False,
            'connected': False,
            'message': 'AliExpress app credentials not configured on server',
        }

    creds = AliExpressOAuthCredentials.objects.filter(user=user).first()
    connected = bool(creds and creds.is_valid and creds.access_token)
    expires_at = creds.expires_at.isoformat() if creds and creds.expires_at else None
    return {
        'configured': True,
        'connected': connected,
        'account': creds.account if creds else '',
        'account_id': creds.account_id if creds else '',
        'expires_at': expires_at,
        'redirect_uri_hint': (getattr(settings, 'ALIEXPRESS_OAUTH_REDIRECT_URI', '') or '').strip(),
    }
