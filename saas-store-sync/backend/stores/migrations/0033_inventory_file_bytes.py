from django.db import migrations, models


def copy_inventory_files_into_db(apps, schema_editor):
    """Copy an existing spreadsheet into Postgres when this server still has the file."""
    Settings = apps.get_model('stores', 'StoreVendorInventorySettings')
    from django.core.files.storage import default_storage

    for row in Settings.objects.all().iterator():
        name = row.nora_inventory_file or ''
        if not name or row.inventory_file_bytes:
            continue
        try:
            if not default_storage.exists(name):
                continue
            with default_storage.open(name, 'rb') as handle:
                payload = handle.read()
        except Exception:
            continue
        if not payload:
            continue
        row.inventory_file_bytes = payload
        row.save(update_fields=['inventory_file_bytes'])


class Migration(migrations.Migration):

    dependencies = [
        ('stores', '0032_temu_region_us'),
    ]

    operations = [
        migrations.AddField(
            model_name='storevendorinventorysettings',
            name='inventory_file_bytes',
            field=models.BinaryField(
                blank=True,
                help_text='Spreadsheet bytes. The AU worker reads this because it does not share the main media disk.',
                null=True,
            ),
        ),
        migrations.RunPython(copy_inventory_files_into_db, migrations.RunPython.noop),
    ]
