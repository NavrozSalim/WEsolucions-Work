# Remove SyncLog model and table

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('sync', '0003_add_sync_run_remove_job'),
    ]

    operations = [
        migrations.DeleteModel(name='SyncLog'),
    ]
