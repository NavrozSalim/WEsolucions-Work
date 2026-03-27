import os
import secrets
from urllib.parse import urlencode, quote
from django.conf import settings
from django.shortcuts import redirect
from django.http import HttpResponseRedirect
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.tokens import RefreshToken
from users.serializers import UserSerializer
from core.throttles import LoginRateThrottle
from vendor.models import GoogleOAuthCredentials

User = get_user_model()


class LoginView(APIView):
    """Login with email + password; returns JWT access and refresh tokens."""
    permission_classes = (AllowAny,)
    throttle_classes = [LoginRateThrottle]

    def post(self, request):
        email = (request.data.get('email') or '').strip()
        password = request.data.get('password') or ''
        if not email or not password:
            return Response(
                {'detail': 'Must include email and password.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user = User.objects.filter(email__iexact=email).first()
        if not user or not user.check_password(password):
            return Response(
                {'detail': 'Invalid email or password.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        refresh = RefreshToken.for_user(user)
        return Response({
            'refresh': str(refresh),
            'access': str(refresh.access_token),
            'user': {'id': str(user.id), 'email': user.email},
        })


class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    permission_classes = (AllowAny,)
    serializer_class = UserSerializer

class UserProfileView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        serializer = UserSerializer(request.user)
        return Response(serializer.data)


class GoogleLoginView(APIView):
    """Redirect to Google OAuth. Frontend should open this URL or redirect user."""
    permission_classes = (AllowAny,)

    def get(self, request):
        client_id = getattr(settings, 'GOOGLE_CLIENT_ID', None) or os.getenv('GOOGLE_CLIENT_ID')
        if not client_id:
            return Response({'error': 'Google OAuth not configured'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        redirect_uri = getattr(settings, 'GOOGLE_REDIRECT_URI', None) or os.getenv('GOOGLE_REDIRECT_URI') or request.build_absolute_uri('/api/v1/auth/google/callback/')
        if settings.DEBUG and request.GET.get('debug'):
            return Response({'redirect_uri': redirect_uri, 'hint': 'Add this exact URL to Google Console > OAuth client > Authorized redirect URIs'})
        state = secrets.token_urlsafe(32)
        next_path = request.GET.get('next', '/')
        origin = (request.GET.get('origin') or '').strip().rstrip('/')
        request.session['oauth_state'] = state
        request.session['oauth_next'] = next_path
        request.session['oauth_origin'] = origin
        params = {
            'client_id': client_id,
            'redirect_uri': redirect_uri,
            'response_type': 'code',
            'scope': 'openid email profile',
            'state': state,
            'access_type': 'offline',
            'prompt': 'consent',
        }
        url = 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode(params)
        response = HttpResponseRedirect(url)
        # Store state in cookie as fallback (session may be lost on redirect from Google)
        # Use path='/api/v1/auth' so cookies are sent on both /google and /google/callback/
        response.set_cookie('oauth_state', state, max_age=600, samesite='Lax', path='/api/v1/auth')
        response.set_cookie('oauth_next', next_path, max_age=600, samesite='Lax', path='/api/v1/auth')
        response.set_cookie('oauth_origin', origin, max_age=600, samesite='Lax', path='/api/v1/auth')
        return response


class GoogleCallbackView(APIView):
    """Google OAuth callback. Exchanges code for user info, creates/gets user, returns JWT via redirect."""
    permission_classes = (AllowAny,)

    def get(self, request):
        client_id = getattr(settings, 'GOOGLE_CLIENT_ID', None) or os.getenv('GOOGLE_CLIENT_ID')
        client_secret = getattr(settings, 'GOOGLE_CLIENT_SECRET', None) or os.getenv('GOOGLE_CLIENT_SECRET')
        if not client_id or not client_secret:
            return redirect(self._frontend_url('/login?error=oauth_not_configured'))
        state = request.GET.get('state')
        stored_state = request.session.get('oauth_state') or request.COOKIES.get('oauth_state')
        if not state or state != stored_state:
            response = redirect(self._frontend_url('/login?error=invalid_state'))
            response.delete_cookie('oauth_state', path='/api/v1/auth')
            response.delete_cookie('oauth_next', path='/api/v1/auth')
            response.delete_cookie('oauth_origin', path='/api/v1/auth')
            return response
        code = request.GET.get('code')
        if not code:
            return redirect(self._frontend_url('/login?error=no_code'))
        redirect_uri = getattr(settings, 'GOOGLE_REDIRECT_URI', None) or os.getenv('GOOGLE_REDIRECT_URI') or request.build_absolute_uri('/api/v1/auth/google/callback/')
        try:
            import requests
            token_resp = requests.post(
                'https://oauth2.googleapis.com/token',
                data={
                    'code': code,
                    'client_id': client_id,
                    'client_secret': client_secret,
                    'redirect_uri': redirect_uri,
                    'grant_type': 'authorization_code',
                },
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                timeout=10,
            )
            token_resp.raise_for_status()
            tokens = token_resp.json()
            access_token = tokens.get('access_token')
            user_resp = requests.get(
                'https://www.googleapis.com/oauth2/v2/userinfo',
                headers={'Authorization': f'Bearer {access_token}'},
                timeout=10,
            )
            user_resp.raise_for_status()
            info = user_resp.json()
        except Exception as e:
            return redirect(self._frontend_url(f'/login?error={str(e)[:50]}'))
        email = (info.get('email') or '').strip()
        if not email:
            return redirect(self._frontend_url('/login?error=no_email'))
        google_id = info.get('id', '')
        user = User.objects.filter(email__iexact=email).first()
        if not user:
            user = User.objects.create_user(
                username=email,
                email=email,
                password=None,
                first_name=info.get('given_name', ''),
                last_name=info.get('family_name', ''),
            )
            user.set_unusable_password()
            user.save()
        GoogleOAuthCredentials.objects.update_or_create(
            user=user,
            defaults={
                'google_user_id': google_id,
                'user_email': email,
                'is_valid': True,
            },
        )
        refresh = RefreshToken.for_user(user)
        next_url = request.session.pop('oauth_next', None) or request.COOKIES.get('oauth_next', '/')
        origin = request.session.pop('oauth_origin', None) or request.COOKIES.get('oauth_origin', '')
        allowed = getattr(settings, 'CORS_ALLOWED_ORIGINS', []) or []
        if isinstance(allowed, str):
            allowed = [o.strip() for o in allowed.split(',') if o.strip()]
        else:
            allowed = [str(o).strip() for o in allowed if str(o).strip()]
        frontend_base = (origin if origin in allowed else None) or (getattr(settings, 'FRONTEND_URL', None) or os.getenv('FRONTEND_URL', 'http://localhost:3000')).rstrip('/')
        # URL-encode tokens (JWTs contain + and / which corrupt in query strings)
        access_enc = quote(str(refresh.access_token), safe='')
        refresh_enc = quote(str(refresh), safe='')
        next_enc = quote(next_url, safe='')
        callback_url = f'{frontend_base}/auth/callback?access={access_enc}&refresh={refresh_enc}&next={next_enc}'
        response = redirect(callback_url)
        response.delete_cookie('oauth_state', path='/api/v1/auth')
        response.delete_cookie('oauth_next', path='/api/v1/auth')
        response.delete_cookie('oauth_origin', path='/api/v1/auth')
        return response

    def _frontend_url(self, path):
        base = getattr(settings, 'FRONTEND_URL', None) or os.getenv('FRONTEND_URL', 'http://localhost:3000')
        return base.rstrip('/') + (path if path.startswith('/') else '/' + path)
