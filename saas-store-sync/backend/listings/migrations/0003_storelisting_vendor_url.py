from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('listings', '0002_listing_action_and_upload_history'),
    ]

    operations = [
        migrations.AddField(
            model_name='storelisting',
            name='vendor_url',
            field=models.CharField(blank=True, default='', max_length=1000),
        ),
    ]
