from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0025_mydealtemplaterow'),
    ]

    operations = [
        migrations.AddField(
            model_name='cataloguploadrow',
            name='fulfillment_center_id_raw',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='productmapping',
            name='fulfillment_center_id',
            field=models.CharField(
                blank=True,
                help_text='Walmart ship node / fulfillment center ID for inventory push',
                max_length=64,
                null=True,
            ),
        ),
    ]
