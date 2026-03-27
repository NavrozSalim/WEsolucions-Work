from rest_framework import serializers
from sync.models import SyncSchedule, StoreSyncRun


class StoreSyncRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = StoreSyncRun
        fields = ['id', 'store', 'started_at', 'finished_at', 'status', 'listings_processed', 'listings_updated', 'error_summary']
        read_only_fields = ('store',)


class SyncScheduleSerializer(serializers.ModelSerializer):
    class Meta:
        model = SyncSchedule
        fields = [
            'id', 'schedule_type', 'crontab_minute', 'crontab_hour', 'crontab_day_of_week',
            'crontab_day_of_month', 'crontab_month_of_year', 'interval_seconds', 'timezone',
            'is_active', 'last_run', 'created_at',
        ]
        read_only_fields = ('store', 'last_run', 'created_at')
