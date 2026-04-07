# Add HEB vendor for price rules and heb.com product URLs
from django.db import migrations


def add_heb(apps, schema_editor):
    Vendor = apps.get_model('vendor', 'Vendor')
    Vendor.objects.get_or_create(code='heb', defaults={'name': 'HEB'})


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('vendor', '0004_add_amazonusa_vendor'),
    ]

    operations = [
        migrations.RunPython(add_heb, noop),
    ]
