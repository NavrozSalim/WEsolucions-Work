from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stores', '0022_store_lasoo_environment_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='store',
            name='reverb_last_order_sync_at',
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text='Last successful Reverb order sync cutoff (UTC). Used for incremental pulls.',
                null=True,
            ),
        ),
    ]
