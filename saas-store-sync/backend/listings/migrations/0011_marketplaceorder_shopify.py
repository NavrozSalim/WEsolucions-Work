from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('listings', '0010_store_orphan_retention'),
    ]

    operations = [
        migrations.AddField(
            model_name='marketplaceorder',
            name='shopify_order_gid',
            field=models.CharField(blank=True, default='', max_length=128),
        ),
        migrations.AddField(
            model_name='marketplaceorder',
            name='shopify_order_id',
            field=models.CharField(blank=True, db_index=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='marketplaceorder',
            name='shopify_order_name',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='marketplaceorder',
            name='shopify_sync_error',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
        migrations.AddField(
            model_name='marketplaceorder',
            name='shopify_synced_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
