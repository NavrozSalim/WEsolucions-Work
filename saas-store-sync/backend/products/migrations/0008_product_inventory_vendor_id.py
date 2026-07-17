from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0007_product_owner'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='inventory_vendor_id',
            field=models.CharField(
                blank=True,
                db_index=True,
                default='',
                help_text='Supplier Vendor ID (e.g. Nora BarCode after -G1/-V* normalization).',
                max_length=255,
            ),
        ),
    ]
