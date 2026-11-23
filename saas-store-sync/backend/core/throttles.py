"""Custom throttle scopes for login, ingest, and sync trigger."""
import hashlib

from rest_framework.throttling import AnonRateThrottle, SimpleRateThrottle, UserRateThrottle


class LoginRateThrottle(AnonRateThrottle):
    """Limit failed sign-in attempts per client IP.

    ``allow_request`` only checks the bucket; it does not add to it. Views using
    ``FailedLoginThrottleMixin`` call ``record_failure`` after a rejected
    sign-in, so successful logins never use up the ``login`` rate.
    """

    scope = 'login'

    def throttle_success(self):
        return True

    def record_failure(self, request, view):
        if self.rate is None:
            return
        key = self.get_cache_key(request, view)
        if key is None:
            return
        now = self.timer()
        history = [t for t in self.cache.get(key, []) if t > now - self.duration]
        history.insert(0, now)
        self.cache.set(key, history, self.duration)


class FailedLoginThrottleMixin:
    """Count a request against ``LoginRateThrottle`` only when it fails (4xx).

    A 429 from the throttle itself is not counted again, so waiting out the
    shown time always unblocks the next attempt.
    """

    throttle_classes = [LoginRateThrottle]

    def finalize_response(self, request, response, *args, **kwargs):
        code = getattr(response, 'status_code', 200)
        if 400 <= code < 500 and code != 429:
            for throttle in self.get_throttles():
                if isinstance(throttle, LoginRateThrottle):
                    throttle.record_failure(request, self)
        return super().finalize_response(request, response, *args, **kwargs)


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
