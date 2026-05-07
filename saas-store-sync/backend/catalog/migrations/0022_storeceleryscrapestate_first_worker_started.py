from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0021_storeceleryscrapestate_cancel_requested'),
    ]

    operations = [
        migrations.AddField(
            model_name='storecatalogceleryscrapestate',
            name='first_worker_started_at',
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text=(
                    'Set when a worker first begins scraping rows; until then the job is only queued.'
                ),
            ),
        ),
    ]
