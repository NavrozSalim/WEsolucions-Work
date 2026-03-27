# Add product_id FK to ProductMapping (nullable for migration)
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0004_schema_product_vendor_catalog'),
        ('catalog', '0005_populate_vendor_for_existing'),
    ]

    operations = [
        migrations.AddField(
            model_name='productmapping',
            name='product',
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='listings',
                to='products.product',
                db_index=True,
            ),
        ),
        migrations.AlterUniqueTogether(
            name='productmapping',
            unique_together=set(),
        ),
    ]
