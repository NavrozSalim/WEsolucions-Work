import secrets
import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone

from users.permissions_defs import ALL_PERMISSIONS, DEFAULT_MEMBER_PERMISSIONS, normalize_permissions


class User(AbstractUser):
    class AccountType(models.TextChoices):
        SUPER_USER = 'super_user', 'Super User'
        USER_ACCOUNT = 'user_account', 'User Account'
        STANDALONE = 'standalone', 'Standalone'  # legacy full-access accounts

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    account_type = models.CharField(
        max_length=20,
        choices=AccountType.choices,
        default=AccountType.STANDALONE,
        db_index=True,
    )
    email_verified = models.BooleanField(default=False)
    organization = models.ForeignKey(
        'Organization',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='users',
    )

    def __str__(self):
        return self.email

    @property
    def is_product_super_user(self) -> bool:
        return self.account_type == self.AccountType.SUPER_USER

    def effective_permissions(self) -> dict:
        if self.account_type in (self.AccountType.SUPER_USER, self.AccountType.STANDALONE):
            return dict(ALL_PERMISSIONS)
        membership = getattr(self, 'membership', None)
        if membership is None:
            try:
                membership = self.membership_profile
            except Membership.DoesNotExist:
                return {key: False for key in ALL_PERMISSIONS}
        return normalize_permissions(membership.permissions)

    def has_product_permission(self, key: str) -> bool:
        return bool(self.effective_permissions().get(key))


class Organization(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    owner = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='owned_organization',
    )
    seat_limit = models.PositiveIntegerField(default=5)
    plan_price_usd = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    def occupied_seats(self) -> int:
        # Owner counts as one seat; members are user_account rows in this org.
        return self.users.filter(
            account_type__in=[User.AccountType.SUPER_USER, User.AccountType.USER_ACCOUNT],
        ).count()

    def seats_remaining(self) -> int:
        return max(0, self.seat_limit - self.occupied_seats())


class Membership(models.Model):
    """Permissions for a User Account inside an Organization."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='membership_profile',
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='memberships',
    )
    permissions = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('organization', 'user')]

    def save(self, *args, **kwargs):
        if not self.permissions:
            self.permissions = dict(DEFAULT_MEMBER_PERMISSIONS)
        else:
            self.permissions = normalize_permissions(self.permissions)
        super().save(*args, **kwargs)


class EmailOTP(models.Model):
    class Purpose(models.TextChoices):
        SUPER_USER_SIGNUP = 'super_user_signup', 'Super User signup'
        MEMBER_INVITE = 'member_invite', 'Member invite'
        EMAIL_VERIFY = 'email_verify', 'Email verify'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(db_index=True)
    code = models.CharField(max_length=6)
    purpose = models.CharField(max_length=32, choices=Purpose.choices)
    payload = models.JSONField(default=dict, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    is_used = models.BooleanField(default=False)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['email', 'purpose', 'is_used']),
        ]

    @classmethod
    def generate_code(cls) -> str:
        return f'{secrets.randbelow(1_000_000):06d}'

    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    def mark_used(self):
        self.is_used = True
        self.save(update_fields=['is_used'])


class LoginEvent(models.Model):
    """Tracks where accounts sign in (IP / user-agent) for platform admin oversight."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='login_events',
    )
    email = models.EmailField(blank=True, default='', db_index=True)
    account_type = models.CharField(max_length=32, blank=True, default='')
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True, default='')
    success = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at']),
        ]

    def __str__(self):
        return f'{self.email or self.user_id} @ {self.ip_address} ({self.created_at})'
