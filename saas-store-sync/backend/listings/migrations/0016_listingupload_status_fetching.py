from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('listings', '0015_listing_action_update'),
    ]

    operations = [
        migrations.AlterField(
            model_name='listingupload',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pending'),
                    ('processing', 'Processing'),
                    ('fetching', 'Fetching'),
                    ('completed', 'Completed'),
                    ('partial', 'Partial'),
                    ('failed', 'Failed'),
                ],
                default='completed',
                max_length=20,
            ),
        ),
    ]
