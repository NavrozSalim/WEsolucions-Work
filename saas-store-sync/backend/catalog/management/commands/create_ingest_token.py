"""Create an ingest API token.

Example:
    python manage.py create_ingest_token --label heb-pc-navroz --scopes heb

Prints the plaintext token once; only its SHA-256 hash is stored in DB.
"""

import hashlib
import secrets

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

        length = max(24, int(opts['length']))
        raw = secrets.token_urlsafe(length)
        token_hash = hashlib.sha256(raw.encode('utf-8')).hexdigest()

        tok = IngestToken.objects.create(
            label=label,
            token_hash=token_hash,
            token_prefix=raw[:8],
            scopes=scopes,
            is_active=True,
        )

        self.stdout.write(self.style.SUCCESS('Ingest token created.'))
        self.stdout.write(f'  id:     {tok.id}')
        self.stdout.write(f'  label:  {tok.label}')
        self.stdout.write(f'  scopes: {scopes}')
        self.stdout.write('')
        self.stdout.write(self.style.WARNING('Plaintext token (shown once, store it now):'))
        self.stdout.write('')
        self.stdout.write(f'  {raw}')
        self.stdout.write('')
        self.stdout.write('Use it in HTTP requests as:')
        self.stdout.write(f'  Authorization: Bearer {raw}')
