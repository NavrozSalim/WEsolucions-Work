from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0012_catalog_activity_and_pending_reset'),
    ]

    operations = [
        migrations.AddField(
            model_name='cataloguploadrow',
            name='pack_qty_raw',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='cataloguploadrow',
            name='prep_fees_raw',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='cataloguploadrow',
            name='shipping_fees_raw',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='productmapping',
            name='pack_qty',
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name='productmapping',
            name='prep_fees',
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name='productmapping',
            name='shipping_fees',
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=12, null=True),
        ),
    ]
