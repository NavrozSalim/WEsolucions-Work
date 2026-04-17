import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0013_walmart_upload_fields'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='IngestToken',
            fields=[
                (
                    'id',
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    'label',
                    models.CharField(
                        help_text='Human-friendly label, e.g. "heb-pc-navroz".',
                        max_length=128,
                    ),
                ),
                ('token_hash', models.CharField(db_index=True, max_length=64, unique=True)),
                (
                    'token_prefix',
                    models.CharField(
                        blank=True,
                        default='',
                        help_text='First few characters of the plaintext token for identification (non-secret).',
                        max_length=12,
                    ),
                ),
                (
                    'scopes',
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text='Allowed scopes, e.g. ["heb"]. Empty list = deny all.',
                    ),
                ),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('last_used_at', models.DateTimeField(blank=True, null=True)),
                ('last_used_ip', models.GenericIPAddressField(blank=True, null=True)),
                ('last_used_count', models.PositiveIntegerField(default=0)),
                (
                    'created_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='ingest_tokens',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'db_table': 'catalog_ingesttoken',
                'ordering': ['-created_at'],
            },
        ),
    ]
