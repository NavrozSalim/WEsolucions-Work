from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('listings', '0011_marketplaceorder_shopify'),
    ]

    operations = [
        migrations.AddField(
            model_name='listingupload',
            name='processed_rows',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='listingupload',
            name='source_file',
            field=models.FileField(blank=True, max_length=500, null=True, upload_to='listing_uploads/%Y/%m/'),
        ),
        migrations.AlterField(
            model_name='listingupload',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pending'),
                    ('processing', 'Processing'),
                    ('completed', 'Completed'),
                    ('partial', 'Partial'),
                    ('failed', 'Failed'),
                ],
                default='completed',
                max_length=20,
            ),
        ),
    ]
