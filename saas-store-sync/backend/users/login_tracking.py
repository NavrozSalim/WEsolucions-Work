"""Helpers for recording login IP / user-agent for platform oversight."""

from __future__ import annotations

from users.models import LoginEvent


def client_ip(request) -> str | None:
    forwarded = (request.META.get('HTTP_X_FORWARDED_FOR') or '').split(',')[0].strip()
    if forwarded:
        return forwarded
    return request.META.get('REMOTE_ADDR') or None


def summarize_user_agent(user_agent: str) -> dict:
    """Return a compact browser/os/device summary for admin views."""
    ua = (user_agent or '').lower()

    browser = 'Unknown browser'
    if 'edg/' in ua or 'edge/' in ua:
        browser = 'Edge'
    elif 'opr/' in ua or 'opera' in ua:
        browser = 'Opera'
    elif 'chrome/' in ua and 'chromium' not in ua and 'edg/' not in ua:
        browser = 'Chrome'
    elif 'firefox/' in ua:
        browser = 'Firefox'
    elif 'safari/' in ua and 'chrome/' not in ua:
        browser = 'Safari'

    os_name = 'Unknown OS'
    if 'windows' in ua:
        os_name = 'Windows'
    elif 'android' in ua:
        os_name = 'Android'
    elif 'iphone' in ua or 'ipad' in ua or 'ios' in ua:
        os_name = 'iOS'
    elif 'mac os x' in ua or 'macintosh' in ua:
        os_name = 'macOS'
    elif 'linux' in ua:
        os_name = 'Linux'

    device = 'Desktop'
    if 'mobile' in ua or 'iphone' in ua or 'android' in ua:
        device = 'Mobile'
    elif 'ipad' in ua or 'tablet' in ua:
        device = 'Tablet'

    return {
        'browser': browser,
        'os': os_name,
        'device': device,
        'label': f'{browser} on {os_name} ({device})',
    }


def record_login_event(
    request,
    user=None,
    *,
    success: bool = True,
    account_type: str = '',
    email: str = '',
) -> LoginEvent:
    email_value = (email or '').strip().lower()
    if user is not None:
        email_value = (user.email or '').strip().lower()
        if not account_type:
            account_type = getattr(user, 'account_type', '') or ''
        if getattr(user, 'is_staff', False) and account_type == 'platform_admin':
            account_type = 'platform_admin'
    ua = (request.META.get('HTTP_USER_AGENT') or '')[:512]
    return LoginEvent.objects.create(
        user=user if user and getattr(user, 'pk', None) else None,
        email=email_value,
        account_type=account_type or '',
        ip_address=client_ip(request),
        user_agent=ua,
        success=success,
    )
