# Consolidate products_product into vendor-only Products, remove store
from django.db import migrations, models
import django.db.models.deletion


def consolidate_products(apps, schema_editor):
    """Merge Products with same (vendor, vendor_sku, variation_id)."""
    Product = apps.get_model('products', 'Product')
    VendorPrice = apps.get_model('vendor', 'VendorPrice')
    Product.objects.filter(variation_id__isnull=True).update(variation_id='')
    Scrape = apps.get_model('products', 'Scrape')

    # Group by (vendor_id, vendor_sku, variation_id)
    from collections import defaultdict
    groups = defaultdict(list)
    var_default = ''
    for p in Product.objects.all():
        vid = p.variation_id if p.variation_id else var_default
        groups[(p.vendor_id, p.vendor_sku, vid)].append(p)

    for key, products in groups.items():
        if len(products) <= 1:
            if products and products[0].store_id:
                products[0].store_id = None
                products[0].save(update_fields=['store_id'])
            continue
        # Keep first (prefer one with store=None from ProductMapping flow)
        canonical = None
        for p in products:
            if p.store_id is None:
                canonical = p
                break
        if not canonical:
            canonical = products[0]
        for p in products:
            if p.id == canonical.id:
                p.store_id = None
                p.save(update_fields=['store_id'])
                continue
            # Migrate VendorPrice and Scrape to canonical
            VendorPrice.objects.filter(product_id=p.id).update(product_id=canonical.id)
            Scrape.objects.filter(product_id=p.id).update(product_id=canonical.id)
            p.delete()
    # Ensure all have store=None
    Product.objects.filter(store_id__isnull=False).update(store_id=None)


def reverse_noop(apps, schema_editor):
    pass  # Cannot reverse consolidation


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0004_schema_product_vendor_catalog'),
        ('catalog', '0007_populate_product_from_mapping'),
    ]

    operations = [
        migrations.RunPython(consolidate_products, reverse_noop),
        migrations.RemoveField(
            model_name='product',
            name='store',
        ),
        migrations.RemoveField(
            model_name='product',
            name='marketplace_child_sku',
        ),
        migrations.RemoveField(
            model_name='product',
            name='marketplace_parent_sku',
        ),
        migrations.RemoveField(
            model_name='product',
            name='marketplace_price_external_id',
        ),
        migrations.RemoveField(
            model_name='product',
            name='marketplace_final_price',
        ),
        migrations.RemoveField(
            model_name='product',
            name='marketplace_final_inventory',
        ),
        migrations.AlterField(
            model_name='product',
            name='variation_id',
            field=models.CharField(max_length=255, default='', blank=True, db_index=True),
        ),
        migrations.AddConstraint(
            model_name='product',
            constraint=models.UniqueConstraint(
                fields=['vendor', 'vendor_sku', 'variation_id'],
                name='uq_product_vendor_sku_variation',
            ),
        ),
    ]
