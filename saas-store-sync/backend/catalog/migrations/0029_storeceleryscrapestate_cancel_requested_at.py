from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0028_store_orphan_retention'),
    ]

    operations = [
        migrations.AddField(
            model_name='storecatalogceleryscrapestate',
            name='cancel_requested_at',
            field=models.DateTimeField(
                blank=True,
                help_text='When Stop was clicked; used to expire a stuck stopping state.',
                null=True,
            ),
        ),
    ]
