# Generated manually for dashboard / catalog list filters on (store, is_active, sync_status).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0022_storeceleryscrapestate_first_worker_started'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='productmapping',
            index=models.Index(
                fields=['store', 'is_active', 'sync_status'],
                name='catalog_pm_store_act_sync',
            ),
        ),
    ]
