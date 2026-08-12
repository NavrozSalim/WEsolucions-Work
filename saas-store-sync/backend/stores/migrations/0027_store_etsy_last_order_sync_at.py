from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stores', '0026_store_orphan_retention'),
    ]

    operations = [
        migrations.AddField(
            model_name='store',
            name='etsy_last_order_sync_at',
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text='Last successful Etsy receipt sync cutoff (UTC). Used for incremental pulls.',
                null=True,
            ),
        ),
    ]
