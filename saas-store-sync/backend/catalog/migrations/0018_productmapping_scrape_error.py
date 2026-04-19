"""Add ``ProductMapping.scrape_error`` so failed live scrapes can record
the reason (captcha, block, no URL, …) instead of silently falling back
to stale VendorPrice history.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0017_alter_hebscrapejob_store_help_text'),
    ]

    operations = [
        migrations.AddField(
            model_name='productmapping',
            name='scrape_error',
            field=models.TextField(
                blank=True,
                null=True,
                help_text=(
                    'Short reason the most recent scrape failed (e.g. "amazon_captcha", '
                    '"ebay_blocked", "no_vendor_url"). Cleared on the next success.'
                ),
            ),
        ),
    ]
