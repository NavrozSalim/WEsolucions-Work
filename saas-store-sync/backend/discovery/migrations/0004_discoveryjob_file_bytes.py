from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('discovery', '0003_discoveryjob_zip_code'),
    ]

    operations = [
        migrations.AddField(
            model_name='discoveryjob',
            name='source_bytes',
            field=models.BinaryField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='discoveryjob',
            name='result_bytes',
            field=models.BinaryField(blank=True, null=True),
        ),
    ]
