from django.contrib import admin

from .models import DiscoveryJob


@admin.register(DiscoveryJob)
class DiscoveryJobAdmin(admin.ModelAdmin):
    list_display = ('id', 'marketplace', 'mode', 'status', 'use_sample', 'queue_name', 'created_at')
    list_filter = ('marketplace', 'mode', 'status', 'use_sample')
    readonly_fields = ('created_at', 'started_at', 'finished_at', 'stats')
