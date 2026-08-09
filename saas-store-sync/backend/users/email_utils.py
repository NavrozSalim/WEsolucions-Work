"""Email validation and OTP delivery helpers."""

from __future__ import annotations

import logging
import re

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')

# Common disposable / throwaway domains — block for Super User and member emails.
DISPOSABLE_DOMAINS = frozenset({
    'mailinator.com', 'guerrillamail.com', 'guerrillamail.net', 'sharklasers.com',
    'grr.la', 'yopmail.com', 'trashmail.com', 'tempmail.com', 'temp-mail.org',
    '10minutemail.com', 'throwaway.email', 'fakeinbox.com', 'getnada.com',
    'maildrop.cc', 'discard.email', 'mailnesia.com', 'tempail.com',
    'moakt.com', 'emailondeck.com', 'trash-mail.com', 'dispostable.com',
    'mailcatch.com', 'tempinbox.com', 'mintemail.com', 'mytemp.email',
    'example.com', 'example.org', 'example.net', 'test.com', 'fake.com',
    'mailinator.net', 'guerrillamail.biz', 'spam4.me', 'trashmail.me',
})


def is_valid_email_format(email: str) -> bool:
    return bool(email and EMAIL_RE.match(email.strip()))


def is_disposable_email(email: str) -> bool:
    domain = email.strip().rsplit('@', 1)[-1].lower()
    if domain in DISPOSABLE_DOMAINS:
        return True
    # Block obvious local/fake hosts
    if domain.endswith('.local') or domain.endswith('.test') or '.' not in domain:
        return True
    return False


def validate_real_email(email: str) -> str | None:
    """Return an error message if email is not acceptable, else None."""
    email = (email or '').strip().lower()
    if not email:
        return 'Email is required.'
    if not is_valid_email_format(email):
        return 'Enter a valid email address.'
    if is_disposable_email(email):
        return 'Disposable or fake email addresses are not allowed. Use a real email.'
    return None


def send_otp_email(to_email: str, otp_code: str, purpose: str = 'verification') -> bool:
    """
    Send OTP via configured email backend.
    When SMTP (EMAIL_HOST) is configured, failures are real failures — never fake success.
    Console-backend DEBUG fallback only applies when SMTP is not configured.
    """
    subject = 'SellerPilot Hub verification code'
    if purpose == 'super_user_signup':
        subject = 'SellerPilot Hub — confirm your Super User account'
    elif purpose == 'member_invite':
        subject = 'SellerPilot Hub — confirm your account'

    body = (
        f'Your SellerPilot Hub verification code is: {otp_code}\n\n'
        f'This code expires in {getattr(settings, "OTP_EXPIRY_MINUTES", 10)} minutes.\n'
        f'If you did not request this, you can ignore this email.\n'
    )
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@sellerpilothub.com')
    smtp_configured = bool(getattr(settings, 'EMAIL_HOST', '') or '')
    try:
        send_mail(
            subject,
            body,
            from_email,
            [to_email],
            fail_silently=False,
        )
        if settings.DEBUG:
            logger.info('OTP emailed to %s (%s)', to_email, purpose)
        return True
    except Exception:
        logger.exception('Failed to send OTP email to %s', to_email)
        # Only skip SMTP when no host is configured (console backend in local DEBUG).
        if settings.DEBUG and not smtp_configured:
            logger.warning('DEBUG OTP fallback (no SMTP) for %s: %s', to_email, otp_code)
            return True
        return False
