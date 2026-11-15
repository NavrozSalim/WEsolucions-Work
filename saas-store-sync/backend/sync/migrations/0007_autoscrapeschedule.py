from django.db import migrations, models
import django.db.models.deletion
import sync.models
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('stores', '0041_auto_order_run_attention'),
        ('sync', '0006_scraperun'),
    ]

    operations = [
        migrations.CreateModel(
            name='AutoScrapeSchedule',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('is_active', models.BooleanField(default=False)),
                ('run_times', models.JSONField(default=sync.models._default_auto_scrape_times, help_text='Local "HH:MM" times, max 6.')),
                ('timezone', models.CharField(default='UTC', max_length=64)),
                ('retry_failed_cycles', models.PositiveSmallIntegerField(default=2)),
                ('push_after', models.BooleanField(default=False)),
                ('last_triggered_at', models.DateTimeField(blank=True, null=True)),
                ('last_run_at', models.DateTimeField(blank=True, null=True)),
                ('last_finished_at', models.DateTimeField(blank=True, null=True)),
                ('last_status', models.CharField(blank=True, default='', max_length=20)),
                ('last_summary', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('store', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='auto_scrape_schedule', to='stores.store')),
            ],
            options={
                'db_table': 'sync_autoscrapeschedule',
            },
        ),
    ]
