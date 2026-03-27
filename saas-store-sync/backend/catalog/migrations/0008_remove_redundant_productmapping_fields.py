# Remove redundant ProductMapping fields; require product_id
from django.db import migrations, models


def delete_orphan_mappings(apps, schema_editor):
    """Remove ProductMappings without product_id before making it required."""
    ProductMapping = apps.get_model('catalog', 'ProductMapping')
    ProductMapping.objects.filter(product_id__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0007_populate_product_from_mapping'),
    ]

    operations = [
        migrations.RunPython(delete_orphan_mappings, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='productmapping',
            name='vendor',
        ),
        migrations.RemoveField(
            model_name='productmapping',
            name='sku',
        ),
        migrations.RemoveField(
            model_name='productmapping',
            name='vendor_url',
        ),
        migrations.RemoveField(
            model_name='productmapping',
            name='vendor_price',
        ),
        migrations.RemoveField(
            model_name='productmapping',
            name='vendor_stock',
        ),
        migrations.AlterField(
            model_name='productmapping',
            name='product',
            field=models.ForeignKey(
                on_delete=models.CASCADE,
                related_name='listings',
                to='products.product',
                db_index=True,
            ),
        ),
        migrations.AddConstraint(
            model_name='productmapping',
            constraint=models.UniqueConstraint(
                fields=['store', 'product'],
                name='uq_productmapping_store_product',
            ),
        ),
    ]
