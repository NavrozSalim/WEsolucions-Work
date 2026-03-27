# Generated migration for SyncSchedule model

import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('stores', '0001_initial'),
        ('sync', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='SyncSchedule',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('schedule_type', models.CharField(choices=[('crontab', 'Crontab'), ('interval', 'Interval')], default='crontab', max_length=20)),
                ('crontab_minute', models.CharField(default='0', max_length=32)),
                ('crontab_hour', models.CharField(default='10', max_length=32)),
                ('crontab_day_of_week', models.CharField(default='*', max_length=32)),
                ('crontab_day_of_month', models.CharField(default='*', max_length=32)),
                ('crontab_month_of_year', models.CharField(default='*', max_length=32)),
                ('interval_seconds', models.IntegerField(blank=True, help_text='e.g. 7200 for every 2 hours', null=True)),
                ('timezone', models.CharField(default='UTC', max_length=64)),
                ('is_active', models.BooleanField(default=True)),
                ('last_run', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('store', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='sync_schedule', to='stores.store')),
            ],
        ),
    ]
