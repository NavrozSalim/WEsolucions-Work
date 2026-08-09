"""Create or update the platform master-admin account from env vars."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

User = get_user_model()


class Command(BaseCommand):
    help = 'Ensure PLATFORM_ADMIN_EMAIL / PLATFORM_ADMIN_PASSWORD exist as a staff master admin.'

    def handle(self, *args, **options):
        email = (getattr(settings, 'PLATFORM_ADMIN_EMAIL', '') or '').strip().lower()
        password = getattr(settings, 'PLATFORM_ADMIN_PASSWORD', '') or ''
        if not email or not password:
            raise CommandError(
                'Set PLATFORM_ADMIN_EMAIL and PLATFORM_ADMIN_PASSWORD in the environment.'
            )

        user = User.objects.filter(email__iexact=email).first()
        created = False
        if not user:
            user = User(
                username=email,
                email=email,
                account_type=User.AccountType.STANDALONE,
                email_verified=True,
                is_staff=True,
                is_superuser=True,
                is_active=True,
            )
            created = True
        user.set_password(password)
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.email_verified = True
        user.username = email
        user.save()

        action = 'Created' if created else 'Updated'
        self.stdout.write(self.style.SUCCESS(f'{action} platform admin: {email}'))
