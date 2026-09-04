from django.db import migrations, models

import core.fields


class Migration(migrations.Migration):

    dependencies = [
        ('stores', '0028_store_shopify_connection'),
    ]

    operations = [
        migrations.AddField(
            model_name='store',
            name='bunnings_environment',
            field=models.CharField(
                blank=True,
                choices=[('staging', 'Staging'), ('production', 'Production')],
                default='production',
                help_text='Which Bunnings Mirakl environment listing pushes / order pulls use.',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='store',
            name='bunnings_staging_base_url',
            field=models.URLField(blank=True, default='', max_length=500),
        ),
        migrations.AddField(
            model_name='store',
            name='bunnings_production_base_url',
            field=models.URLField(blank=True, default='', max_length=500),
        ),
        migrations.AddField(
            model_name='store',
            name='bunnings_staging_shop_key',
            field=core.fields.EncryptedTextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='store',
            name='bunnings_production_shop_key',
            field=core.fields.EncryptedTextField(blank=True, null=True),
        ),
    ]
