"""Add vendor_code to HebScrapeJob so the same queue table powers multiple
desktop-runner vendors (HEB, Costco, ...). Existing rows keep the implicit
'heb' value via the field default.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0015_hebscrapejob'),
    ]

    operations = [
        migrations.AddField(
            model_name='hebscrapejob',
            name='vendor_code',
            field=models.CharField(
                db_index=True,
                default='heb',
                help_text=(
                    "Which desktop-runner vendor this job belongs to. One of "
                    "'heb', 'costco', etc. Desktop pollers filter on this so each "
                    "runner only picks up jobs for its vendor."
                ),
                max_length=32,
            ),
        ),
        migrations.AddIndex(
            model_name='hebscrapejob',
            index=models.Index(
                fields=['vendor_code', 'status', 'requested_at'],
                name='catalog_heb_vendor_status_idx',
            ),
        ),
    ]
