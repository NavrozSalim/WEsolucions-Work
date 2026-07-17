from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('listings', '0005_storelisting_inventory_scrape'),
    ]

    operations = [
        migrations.AddField(
            model_name='storelisting',
            name='vendor_id',
            field=models.CharField(
                blank=True,
                db_index=True,
                default='',
                help_text='Supplier Vendor ID (Nora BarCode after -G1/-V* normalization).',
                max_length=255,
            ),
        ),
    ]
