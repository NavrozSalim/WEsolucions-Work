from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('discovery', '0002_discoveryproduct'),
    ]

    operations = [
        migrations.AddField(
            model_name='discoveryjob',
            name='zip_code',
            field=models.CharField(blank=True, max_length=12),
        ),
    ]
