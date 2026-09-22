from django.db import migrations, models

import core.fields


class Migration(migrations.Migration):

    dependencies = [
        ('stores', '0030_store_bunnings_last_order_sync_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='store',
            name='temu_region',
            field=models.CharField(
                blank=True,
                choices=[('au', 'Australia / Global')],
                default='au',
                help_text='Temu site region. AU sellers use the Global router (openapi-b-global.temu.com).',
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='store',
            name='temu_base_url',
            field=models.URLField(
                blank=True,
                default='',
                help_text='Override the Temu Open API router URL. Blank uses the AU/Global default.',
                max_length=500,
            ),
        ),
        migrations.AddField(
            model_name='store',
            name='temu_app_key',
            field=core.fields.EncryptedTextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='store',
            name='temu_app_secret',
            field=core.fields.EncryptedTextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='store',
            name='temu_access_token',
            field=core.fields.EncryptedTextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='store',
            name='temu_mall_id',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Temu mall id returned by bg.open.accesstoken.create.',
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name='store',
            name='temu_last_order_sync_at',
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text='Last successful Temu order sync cutoff (UTC). Used for incremental pulls.',
                null=True,
            ),
        ),
    ]
