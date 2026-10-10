from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('discovery', '0004_discoveryjob_file_bytes'),
    ]

    operations = [
        migrations.AddField(
            model_name='discoveryjob',
            name='cancel_requested',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='discoveryjob',
            name='celery_task_id',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
    ]
