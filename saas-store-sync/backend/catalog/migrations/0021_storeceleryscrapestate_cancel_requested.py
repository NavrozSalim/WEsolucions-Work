from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0020_catalog_upload_async_ingest'),
    ]

    operations = [
        migrations.AddField(
            model_name='storecatalogceleryscrapestate',
            name='cancel_requested',
            field=models.BooleanField(
                default=False,
                help_text='User clicked Stop Scraping; worker loops should exit cooperatively.',
            ),
        ),
    ]
