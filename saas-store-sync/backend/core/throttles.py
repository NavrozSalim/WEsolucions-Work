"""Custom throttle scopes for login, ingest, and sync trigger."""
import hashlib

from rest_framework.throttling import AnonRateThrottle, SimpleRateThrottle, UserRateThrottle


class LoginRateThrottle(AnonRateThrottle):
    scope = 'login'


class OTPRateThrottle(AnonRateThrottle):
    scope = 'otp'


class SyncTriggerRateThrottle(UserRateThrottle):
    scope = 'sync_trigger'


class ProgressReadRateThrottle(UserRateThrottle):
    """High limit for progress polling during long Sears bulk syncs."""

    scope = 'progress_read'


class IngestRateThrottle(SimpleRateThrottle):
    """Limit desktop ingest calls per bearer token and direct peer address.

    Ingest views authenticate a bearer token after clearing DRF authentication,
    so the default user and anonymous throttles do not count those requests.
    The cache key is a hash of the token plus ``REMOTE_ADDR``. ``X-Forwarded-For``
    is ignored so a caller cannot open a new bucket by changing that header.
    A runner that polls about every 30 seconds and posts batched results stays
    under the ``ingest`` rate.
    """

    scope = 'ingest'

    def get_cache_key(self, request, view):
        header = request.META.get('HTTP_AUTHORIZATION', '') or ''
        raw = ''
        if header.lower().startswith('bearer '):
            raw = header.split(' ', 1)[1].strip()
        token_id = (
            hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32] if raw else 'anon'
        )
        ident = request.META.get('REMOTE_ADDR') or 'unknown'
        return self.cache_format % {
            'scope': self.scope,
            'ident': f'{token_id}:{ident}',
        }
