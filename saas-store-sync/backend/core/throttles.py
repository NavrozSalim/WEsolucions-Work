"""Custom throttle scopes for login and sync trigger."""
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class LoginRateThrottle(AnonRateThrottle):
    scope = 'login'


class SyncTriggerRateThrottle(UserRateThrottle):
    scope = 'sync_trigger'


class ProgressReadRateThrottle(UserRateThrottle):
    """High limit for progress polling during long Sears bulk syncs."""

    scope = 'progress_read'
