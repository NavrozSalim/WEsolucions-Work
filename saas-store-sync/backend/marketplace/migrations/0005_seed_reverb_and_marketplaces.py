# Data migration: ensure Reverb and other marketplaces exist for the dropdown
from django.db import migrations


def seed_marketplaces(apps, schema_editor):
    Marketplace = apps.get_model('marketplace', 'Marketplace')
    marketplaces = [
        ('reverb', 'Reverb'),
        ('etsy', 'Etsy'),
        ('walmart', 'Walmart'),
        ('sears', 'Sears'),
        ('mydeal', 'MyDeal'),
        ('kogan', 'Kogan'),
    ]
    for code, name in marketplaces:
        Marketplace.objects.get_or_create(code=code, defaults={'name': name})


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0004_remove_duplicate_store_models'),
    ]

    operations = [
        migrations.RunPython(seed_marketplaces, noop),
    ]
