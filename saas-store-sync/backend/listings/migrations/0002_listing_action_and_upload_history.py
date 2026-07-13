# Generated manually for managed-store listing action + upload history.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('listings', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='storelisting',
            name='action',
            field=models.CharField(
                choices=[('create', 'Create'), ('mapped', 'Mapped'), ('delete', 'Delete')],
                default='create',
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name='ListingUpload',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('filename', models.CharField(blank=True, default='', max_length=500)),
                ('source', models.CharField(choices=[('file', 'File'), ('single', 'Single')], default='file', max_length=20)),
                ('action', models.CharField(choices=[('create', 'Create'), ('mapped', 'Mapped'), ('delete', 'Delete')], default='create', max_length=20)),
                ('status', models.CharField(choices=[('completed', 'Completed'), ('partial', 'Partial'), ('failed', 'Failed')], default='completed', max_length=20)),
                ('total_rows', models.IntegerField(default=0)),
                ('success_rows', models.IntegerField(default=0)),
                ('error_rows', models.IntegerField(default=0)),
                ('rows_json', models.JSONField(blank=True, null=True)),
                ('message', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('store', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='listing_uploads', to='stores.store')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='listing_uploads', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'listings_listingupload',
                'ordering': ['-created_at'],
            },
        ),
    ]
