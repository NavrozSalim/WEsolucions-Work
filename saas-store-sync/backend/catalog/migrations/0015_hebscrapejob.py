import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0014_ingesttoken'),
        ('stores', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='HebScrapeJob',
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
                    'status',
                    models.CharField(
                        choices=[
                            ('pending', 'Pending'),
                            ('claimed', 'Claimed'),
                            ('done', 'Done'),
                            ('failed', 'Failed'),
                            ('cancelled', 'Cancelled'),
                        ],
                        db_index=True,
                        default='pending',
                        max_length=20,
                    ),
                ),
                ('requested_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('claimed_at', models.DateTimeField(blank=True, null=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('claimed_by_ip', models.GenericIPAddressField(blank=True, null=True)),
                (
                    'url_count',
                    models.PositiveIntegerField(
                        default=0,
                        help_text='# URLs handed to runner.',
                    ),
                ),
                (
                    'stats',
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text='Runner-reported result: {"received": N, "matched": N, "applied": N, ...}',
                    ),
                ),
                ('note', models.TextField(blank=True, default='')),
                (
                    'claimed_by_token',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='claimed_heb_jobs',
                        to='catalog.ingesttoken',
                    ),
                ),
                (
                    'requested_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='heb_scrape_jobs',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    'store',
                    models.ForeignKey(
                        blank=True,
                        help_text='Optional: scope to a single store. Null = all HEB stores.',
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='heb_scrape_jobs',
                        to='stores.store',
                    ),
                ),
            ],
            options={
                'db_table': 'catalog_hebscrapejob',
                'ordering': ['-requested_at'],
                'indexes': [
                    models.Index(fields=['status', 'requested_at'], name='catalog_heb_status_eb1d07_idx'),
                ],
            },
        ),
    ]
