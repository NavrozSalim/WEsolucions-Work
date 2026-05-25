"""Create an ingest API token.

Examples:
    python manage.py create_ingest_token --label heb-pc-navroz --scopes heb
    python manage.py create_ingest_token --label heb-pc --scopes heb --owner-email you@example.com

One token can list every scope your runners need. Extend scopes on an existing
``IngestToken`` in Django admin if you add new desktop vendors later.

Prints the plaintext token once; only its SHA-256 hash is stored in DB.
"""

import hashlib
import secrets

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from catalog.models import IngestToken


class Command(BaseCommand):
    help = 'Create a new ingest API token (for the HEB desktop runner, etc.).'

    def add_arguments(self, parser):
        parser.add_argument('--label', required=True, help='Human-friendly label.')
        parser.add_argument(
            '--scopes',
            nargs='+',
            default=['heb'],
            help='Allowed scopes (default: heb).',
        )
        parser.add_argument(
            '--owner-email',
            default='',
            help='Store owner email — sets created_by so /ingest/heb/urls/ lists their products.',
        )
        parser.add_argument(
            '--length',
            type=int,
            default=48,
            help='Token length in URL-safe characters (default: 48).',
        )

    def handle(self, *args, **opts):
        label = opts['label'].strip()
        if not label:
            raise CommandError('--label cannot be empty.')
        scopes = [s.strip().lower() for s in opts['scopes'] if s.strip()]
        if not scopes:
            raise CommandError('At least one --scope is required.')

        created_by = None
        owner_email = (opts.get('owner_email') or '').strip()
        if owner_email:
            User = get_user_model()
            created_by = User.objects.filter(email__iexact=owner_email).first()
            if created_by is None:
                raise CommandError(f'No user found with email {owner_email!r}.')

        length = max(24, int(opts['length']))
        raw = secrets.token_urlsafe(length)
        token_hash = hashlib.sha256(raw.encode('utf-8')).hexdigest()

        tok = IngestToken.objects.create(
            label=label,
            token_hash=token_hash,
            token_prefix=raw[:8],
            scopes=scopes,
            is_active=True,
            created_by=created_by,
        )

        self.stdout.write(self.style.SUCCESS('Ingest token created.'))
        self.stdout.write(f'  id:     {tok.id}')
        self.stdout.write(f'  label:  {tok.label}')
        self.stdout.write(f'  scopes: {scopes}')
        if created_by:
            self.stdout.write(f'  owner:  {created_by.email}')
        else:
            self.stdout.write(self.style.WARNING(
                '  owner:  (none — set Created by in admin or pass --owner-email)'
            ))
        self.stdout.write('')
        self.stdout.write(self.style.WARNING('Plaintext token (shown once, store it now):'))
        self.stdout.write('')
        self.stdout.write(f'  {raw}')
        self.stdout.write('')
        self.stdout.write('Use it in HTTP requests as:')
        self.stdout.write(f'  Authorization: Bearer {raw}')
