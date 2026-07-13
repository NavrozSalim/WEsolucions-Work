# Data migration: ensure Lasoo exists for the marketplace dropdown (managed stores).
from django.db import migrations


def seed_lasoo(apps, schema_editor):
    Marketplace = apps.get_model('marketplace', 'Marketplace')
    Marketplace.objects.get_or_create(code='lasoo', defaults={'name': 'Lasoo'})


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('marketplace', '0005_seed_reverb_and_marketplaces'),
    ]

    operations = [
        migrations.RunPython(seed_lasoo, noop),
    ]
