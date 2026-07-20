# Generated manually for StoreListing.options (Lasoo variant Options column).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('listings', '0007_storelisting_source_vendor_code'),
    ]

    operations = [
        migrations.AddField(
            model_name='storelisting',
            name='options',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Variant options for Lasoo (e.g. Colour=Black). Required when Product Key differs from Variant Key.',
                max_length=500,
            ),
        ),
    ]
