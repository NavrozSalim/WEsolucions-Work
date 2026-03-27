# Add range_type and fixed_value to StoreInventoryRangeMultiplier

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stores', '0006_remove_platform'),
    ]

    operations = [
        migrations.AddField(
            model_name='storeinventoryrangemultiplier',
            name='range_type',
            field=models.CharField(
                choices=[('multiplier', 'Multiplier'), ('fixed', 'Fixed Value')],
                default='multiplier',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='storeinventoryrangemultiplier',
            name='fixed_value',
            field=models.IntegerField(blank=True, null=True, help_text='Store stock when range_type is fixed'),
        ),
    ]
