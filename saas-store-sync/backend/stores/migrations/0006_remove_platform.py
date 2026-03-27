from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('stores', '0005_consolidate_pricing_inventory_remove_legacy'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='store',
            name='platform',
        ),
    ]
