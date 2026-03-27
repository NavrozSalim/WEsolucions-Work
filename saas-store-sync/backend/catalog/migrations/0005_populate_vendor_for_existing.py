# Data migration: set vendor_id for existing ProductMapping rows (use first vendor)
from django.db import migrations


def populate_vendor(apps, schema_editor):
    ProductMapping = apps.get_model('catalog', 'ProductMapping')
    Vendor = apps.get_model('vendor', 'Vendor')
    first_vendor = Vendor.objects.first()
    if first_vendor:
        ProductMapping.objects.filter(vendor_id__isnull=True).update(vendor_id=first_vendor.id)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0004_add_vendor_and_marketplace_skus'),
    ]

    operations = [
        migrations.RunPython(populate_vendor, noop),
    ]
