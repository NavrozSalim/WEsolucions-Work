from django.db import migrations


def seed_temu(apps, schema_editor):
    Marketplace = apps.get_model('marketplace', 'Marketplace')
    Marketplace.objects.get_or_create(code='temu', defaults={'name': 'Temu'})


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0007_seed_bunnings'),
    ]

    operations = [
        migrations.RunPython(seed_temu, noop),
    ]
