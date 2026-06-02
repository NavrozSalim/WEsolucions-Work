from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0026_walmart_fulfillment_center_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='cataloguploadrow',
            name='fulfillment_lag_time_raw',
            field=models.CharField(blank=True, default='', max_length=32),
        ),
        migrations.AddField(
            model_name='productmapping',
            name='fulfillment_lag_time',
            field=models.PositiveSmallIntegerField(
                blank=True,
                help_text='Walmart fulfillmentLagTime (days to ship); pushed via lagtime feed',
                null=True,
            ),
        ),
    ]
