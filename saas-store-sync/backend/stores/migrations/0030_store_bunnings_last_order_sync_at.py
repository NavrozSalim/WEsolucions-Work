from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stores', '0029_store_bunnings_credentials'),
    ]

    operations = [
        migrations.AddField(
            model_name='store',
            name='bunnings_last_order_sync_at',
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text='Last successful Bunnings order sync cutoff (UTC). Used for incremental OR11 pulls.',
                null=True,
            ),
        ),
    ]
