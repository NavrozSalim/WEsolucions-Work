"""Sync HebScrapeJob.store help_text with the generalized multi-vendor wording.

This is a pure metadata change (help_text only) — no schema changes.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0016_scrapejob_vendor_code'),
        ('stores', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='hebscrapejob',
            name='store',
            field=models.ForeignKey(
                blank=True,
                help_text='Optional: scope to a single store. Null = all vendor stores.',
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='heb_scrape_jobs',
                to='stores.store',
            ),
        ),
    ]
