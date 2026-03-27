# Migration: Product becomes vendor-only source catalog
# Step 1: Add vendor_url, make store nullable, prep for ProductMapping link
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('stores', '0005_consolidate_pricing_inventory_remove_legacy'),
        ('products', '0003_switch_product_store_to_stores'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='vendor_url',
            field=models.URLField(max_length=1000, null=True, blank=True),
        ),
        migrations.AlterField(
            model_name='product',
            name='store',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='product_records',
                to='stores.store',
                null=True,
                blank=True,
                db_index=True,
            ),
        ),
        migrations.RemoveIndex(
            model_name='product',
            name='prod_store_vendor',
        ),
        migrations.RemoveIndex(
            model_name='product',
            name='prod_mkt_child_sku',
        ),
        migrations.AlterUniqueTogether(
            name='product',
            unique_together=set(),
        ),
    ]
