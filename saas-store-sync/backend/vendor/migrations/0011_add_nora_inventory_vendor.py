from django.db import migrations


def seed_nora(apps, schema_editor):
    Vendor = apps.get_model('vendor', 'Vendor')
    Vendor.objects.get_or_create(code='noraau', defaults={'name': 'Nora Inventory'})


def unseed_nora(apps, schema_editor):
    Vendor = apps.get_model('vendor', 'Vendor')
    Vendor.objects.filter(code='noraau').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('vendor', '0010_split_aliexpress_regional_vendors'),
    ]

    operations = [
        migrations.RunPython(seed_nora, unseed_nora),
    ]
