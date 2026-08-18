from django.db import migrations, models
import core.fields


class Migration(migrations.Migration):

    dependencies = [
        ('stores', '0027_store_etsy_last_order_sync_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='store',
            name='shopify_access_token',
            field=core.fields.EncryptedTextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='store',
            name='shopify_client_id',
            field=core.fields.EncryptedTextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='store',
            name='shopify_client_secret',
            field=core.fields.EncryptedTextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='store',
            name='shopify_enabled',
            field=models.BooleanField(
                default=False,
                help_text='When True, newly fetched marketplace orders are also created in Shopify.',
            ),
        ),
        migrations.AddField(
            model_name='store',
            name='shopify_location_id',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Optional Shopify location numeric id or GID.',
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name='store',
            name='shopify_shop_domain',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Store domain, e.g. t4nx6h-ds.myshopify.com',
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name='store',
            name='shopify_token_expires_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
