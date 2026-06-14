# Generated manually for AliExpress Drop Shipping OAuth credentials.

import uuid

from django.conf import settings
from django.db import migrations, models

import core.fields


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('vendor', '0008_remove_koganau_vendor'),
    ]

    operations = [
        migrations.CreateModel(
            name='AliExpressOAuthCredentials',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('account', models.CharField(blank=True, default='', max_length=255)),
                ('account_id', models.CharField(blank=True, db_index=True, default='', max_length=255)),
                ('access_token', core.fields.EncryptedTextField(blank=True, null=True)),
                ('refresh_token', core.fields.EncryptedTextField(blank=True, null=True)),
                ('expires_at', models.DateTimeField(blank=True, null=True)),
                ('refresh_expires_at', models.DateTimeField(blank=True, null=True)),
                ('is_valid', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'user',
                    models.OneToOneField(
                        on_delete=models.deletion.CASCADE,
                        related_name='aliexpress_oauth_credentials',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'verbose_name_plural': 'AliExpress OAuth credentials',
                'db_table': 'vendor_aliexpressoauthcredentials',
            },
        ),
    ]
