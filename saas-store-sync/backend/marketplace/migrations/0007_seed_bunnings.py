from django.db import migrations


def seed_bunnings(apps, schema_editor):
    Marketplace = apps.get_model('marketplace', 'Marketplace')
    Marketplace.objects.get_or_create(code='bunnings', defaults={'name': 'Bunnings'})


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0006_seed_lasoo'),
    ]

    operations = [
        migrations.RunPython(seed_bunnings, noop),
    ]
