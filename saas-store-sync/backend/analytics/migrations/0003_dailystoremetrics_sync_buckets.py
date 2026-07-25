from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0002_remove_dailystoremetrics_analytics_dailystoremetrics_store_date_unique_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='dailystoremetrics',
            name='failed_count',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='dailystoremetrics',
            name='needs_attention_count',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='dailystoremetrics',
            name='pending_count',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='dailystoremetrics',
            name='scraped_count',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='dailystoremetrics',
            name='synced_count',
            field=models.IntegerField(default=0),
        ),
    ]
