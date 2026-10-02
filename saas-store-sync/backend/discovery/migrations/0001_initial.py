import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='DiscoveryJob',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('marketplace', models.CharField(choices=[('amazon_us', 'Amazon US'), ('amazon_au', 'Amazon AU'), ('ebay_us', 'eBay US'), ('ebay_au', 'eBay AU')], max_length=20)),
                ('mode', models.CharField(choices=[('category', 'Category'), ('product', 'Product details')], max_length=20)),
                ('status', models.CharField(choices=[('queued', 'Queued'), ('running', 'Running'), ('succeeded', 'Succeeded'), ('failed', 'Failed')], db_index=True, default='queued', max_length=20)),
                ('original_filename', models.CharField(blank=True, max_length=255)),
                ('source_file', models.FileField(upload_to='discovery/uploads/%Y/%m/')),
                ('result_file', models.FileField(blank=True, upload_to='discovery/results/%Y/%m/')),
                ('rules', models.JSONField(blank=True, default=dict)),
                ('columns', models.JSONField(blank=True, default=list)),
                ('use_sample', models.BooleanField(default=False)),
                ('queue_name', models.CharField(blank=True, max_length=32)),
                ('stats', models.JSONField(blank=True, default=dict)),
                ('error_message', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='discovery_jobs', to=settings.AUTH_USER_MODEL)),
                ('parent', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='children', to='self')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
