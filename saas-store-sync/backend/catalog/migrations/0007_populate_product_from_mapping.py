# Data migration: create Product from ProductMapping, link product_id
from django.db import migrations


def create_products_and_link(apps, schema_editor):
    Product = apps.get_model('products', 'Product')
    ProductMapping = apps.get_model('catalog', 'ProductMapping')

    seen = {}  # (vendor_id, vendor_sku) -> product_id
    for pm in ProductMapping.objects.filter(vendor_id__isnull=False):
        key = (pm.vendor_id, pm.sku or '')
        if key not in seen:
            product = Product.objects.filter(
                vendor_id=pm.vendor_id, vendor_sku=pm.sku
            ).first()
            if not product:
                product = Product.objects.create(
                    vendor_id=pm.vendor_id,
                    vendor_sku=pm.sku,
                    variation_id='',
                    vendor_url=pm.vendor_url or '',
                    store_id=None,
                )
            seen[key] = product.id
        pm.product_id = seen[key]
        pm.save(update_fields=['product_id'])


def reverse_unlink(apps, schema_editor):
    ProductMapping = apps.get_model('catalog', 'ProductMapping')
    ProductMapping.objects.all().update(product_id=None)


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0006_add_product_id'),
    ]

    operations = [
        migrations.RunPython(create_products_and_link, reverse_unlink),
    ]
