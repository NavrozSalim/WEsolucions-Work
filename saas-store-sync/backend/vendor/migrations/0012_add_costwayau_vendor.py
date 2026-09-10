from django.db import migrations


def seed_costway(apps, schema_editor):
    Vendor = apps.get_model('vendor', 'Vendor')
    Vendor.objects.get_or_create(code='costwayau', defaults={'name': 'CostwayAU'})


def unseed_costway(apps, schema_editor):
    Vendor = apps.get_model('vendor', 'Vendor')
    Product = apps.get_model('products', 'Product')
    vendor_qs = Vendor.objects.filter(code='costwayau')
    for vendor in vendor_qs:
        attached = Product.objects.filter(vendor_id=vendor.id).count()
        if attached:
            raise RuntimeError(
                f"Cannot remove Vendor 'CostwayAU' ({vendor.id}): {attached} product(s) still attached."
            )
    vendor_qs.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('vendor', '0011_add_nora_inventory_vendor'),
    ]

    operations = [
        migrations.RunPython(seed_costway, unseed_costway),
    ]
