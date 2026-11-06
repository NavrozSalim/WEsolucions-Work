import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('discovery', '0005_discoveryjob_cancel'),
    ]

    operations = [
        migrations.CreateModel(
            name='ListingExport',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('target', models.CharField(choices=[('reverb', 'Reverb'), ('etsy', 'Etsy')], max_length=10)),
                ('status', models.CharField(
                    choices=[
                        ('queued', 'Queued'),
                        ('running', 'Running'),
                        ('succeeded', 'Succeeded'),
                        ('failed', 'Failed'),
                    ],
                    default='queued',
                    max_length=20,
                )),
                ('store_name', models.CharField(blank=True, max_length=255)),
                ('stats', models.JSONField(blank=True, default=dict)),
                ('error_message', models.TextField(blank=True)),
                ('result_bytes', models.BinaryField(blank=True, null=True)),
                ('celery_task_id', models.CharField(blank=True, default='', max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('job', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='listing_exports',
                    to='discovery.discoveryjob',
                )),
                ('owner', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='listing_exports',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
