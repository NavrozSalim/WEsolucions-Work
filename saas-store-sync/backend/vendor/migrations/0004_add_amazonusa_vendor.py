# Add AmazonUSA vendor for catalog uploads
from django.db import migrations


def add_amazonusa(apps, schema_editor):
    Vendor = apps.get_model('vendor', 'Vendor')
    Vendor.objects.get_or_create(code='amazonusa', defaults={'name': 'AmazonUSA'})


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('vendor', '0003_seed_vendors'),
    ]

    operations = [
        migrations.RunPython(add_amazonusa, noop),
    ]
