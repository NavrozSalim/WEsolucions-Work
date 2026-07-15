# Generated manually for managed listing inventory scrape fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('listings', '0004_support_tickets'),
    ]

    operations = [
        migrations.AddField(
            model_name='storelisting',
            name='vendor_price',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name='storelisting',
            name='inventory_sync_status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pending'),
                    ('scraped', 'Scraped'),
                    ('synced', 'Synced'),
                    ('failed', 'Failed'),
                ],
                db_index=True,
                default='pending',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='storelisting',
            name='last_scrape_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='storelisting',
            name='last_scrape_error',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
    ]
