"""AliExpress Drop Shipping OAuth connect/callback endpoints."""
from __future__ import annotations

import os
import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.http import HttpResponseRedirect
from django.shortcuts import redirect
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from scrapers.aliexpress_iop import AliExpressIOPError
from vendor.aliexpress_oauth import (
    build_authorize_url,
    get_oauth_redirect_uri,
    oauth_status_for_user,
    save_tokens_from_code,
)

User = get_user_model()
_STATE_SIGNER = TimestampSigner(salt='aliexpress-oauth-state')
_STATE_MAX_AGE = 600


def _make_state(user_id) -> str:
    return _STATE_SIGNER.sign(f'{user_id}:{secrets.token_urlsafe(16)}')


def _user_id_from_state(state: str):
    try:
        payload = _STATE_SIGNER.unsign(state, max_age=_STATE_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    user_id = (payload or '').split(':', 1)[0].strip()
    return user_id or None


def _frontend_base(request, origin: str = '') -> str:
    allowed = getattr(settings, 'CORS_ALLOWED_ORIGINS', []) or []
    if isinstance(allowed, str):
        allowed = [o.strip() for o in allowed.split(',') if o.strip()]
    else:
        allowed = [str(o).strip() for o in allowed if str(o).strip()]
    origin = (origin or '').strip().rstrip('/')
    if origin in allowed:
        return origin
    return (getattr(settings, 'FRONTEND_URL', None) or os.getenv('FRONTEND_URL', 'http://localhost:3000')).rstrip('/')


class AliExpressConnectView(APIView):
    """Return AliExpress OAuth authorize URL for the logged-in user."""

    permission_classes = (IsAuthenticated,)

    def get(self, request):
        app_key = (getattr(settings, 'ALIEXPRESS_APP_KEY', '') or '').strip()
        if not app_key:
            return Response(
                {'error': 'AliExpress OAuth not configured (missing ALIEXPRESS_APP_KEY)'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        redirect_uri = get_oauth_redirect_uri(request)
        if not redirect_uri:
            return Response(
                {'error': 'AliExpress OAuth redirect URI not configured'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        state = _make_state(request.user.id)
        authorize_url = build_authorize_url(redirect_uri=redirect_uri, state=state)
        if request.GET.get('redirect') == '1':
            return HttpResponseRedirect(authorize_url)
        return Response({'authorize_url': authorize_url, 'redirect_uri': redirect_uri})


class AliExpressCallbackView(APIView):
    """AliExpress OAuth callback — exchanges code for tokens and redirects to frontend."""

    permission_classes = (AllowAny,)

    def get(self, request):
        frontend = _frontend_base(request, request.GET.get('origin', ''))
        error = (request.GET.get('error') or request.GET.get('error_description') or '').strip()
        if error:
            return redirect(f'{frontend}/settings?aliexpress_error={error[:120]}')

        state = request.GET.get('state')
        user_id = _user_id_from_state(state or '')
        if not user_id:
            return redirect(f'{frontend}/settings?aliexpress_error=invalid_state')

        code = (request.GET.get('code') or '').strip()
        if not code:
            return redirect(f'{frontend}/settings?aliexpress_error=no_code')

        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return redirect(f'{frontend}/settings?aliexpress_error=user_not_found')

        try:
            save_tokens_from_code(user, code)
        except AliExpressIOPError as exc:
            return redirect(f'{frontend}/settings?aliexpress_error={str(exc)[:120]}')

        return redirect(f'{frontend}/settings?aliexpress_connected=1')


class AliExpressStatusView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        return Response(oauth_status_for_user(request.user))


class AliExpressDisconnectView(APIView):
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        from vendor.models import AliExpressOAuthCredentials

        AliExpressOAuthCredentials.objects.filter(user=request.user).delete()
        return Response({'disconnected': True})
