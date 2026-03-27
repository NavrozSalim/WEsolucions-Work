# Data migration: ensure vendors exist for the Add vendor dropdown
from django.db import migrations


def seed_vendors(apps, schema_editor):
    Vendor = apps.get_model('vendor', 'Vendor')
    vendors = [
        ('amazon', 'Amazon'),
        ('vevor', 'Vevor'),
        ('aliexpress', 'AliExpress'),
        ('ebay', 'eBay'),
    ]
    for code, name in vendors:
        Vendor.objects.get_or_create(code=code, defaults={'name': name})


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('vendor', '0002_add_is_valid'),
    ]

    operations = [
        migrations.RunPython(seed_vendors, noop),
    ]
