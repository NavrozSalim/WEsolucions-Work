from django.db import migrations, models


def migrate_profile_to_setup(apps, schema_editor):
    Store = apps.get_model('stores', 'Store')
    for store in Store.objects.exclude(mydeal_profile__isnull=True).exclude(mydeal_profile=''):
        store.mydeal_setup_method = 'upload'
        store.save(update_fields=['mydeal_setup_method'])


class Migration(migrations.Migration):

    dependencies = [
        ('stores', '0019_alter_store_mydeal_profile_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='store',
            name='mydeal_setup_method',
            field=models.CharField(
                blank=True,
                choices=[('upload', 'Upload templates'), ('api', 'API connection')],
                default='upload',
                help_text='Mydeal: upload Price/Inventory CSV templates or API (future).',
                max_length=20,
            ),
        ),
        migrations.RunPython(migrate_profile_to_setup, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='store',
            name='mydeal_profile',
        ),
    ]
