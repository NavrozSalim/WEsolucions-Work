from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stores', '0023_store_reverb_last_order_sync_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='storevendorinventorysettings',
            name='nora_inventory_file',
            field=models.FileField(
                blank=True,
                help_text='Nora Inventory Excel (Export inventory sheet). Overwritten on re-upload.',
                null=True,
                upload_to='nora_inventory/%Y/%m/',
            ),
        ),
        migrations.AddField(
            model_name='storevendorinventorysettings',
            name='nora_inventory_original_name',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='storevendorinventorysettings',
            name='nora_inventory_uploaded_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
