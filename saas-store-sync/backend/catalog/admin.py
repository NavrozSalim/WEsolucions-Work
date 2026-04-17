from django.contrib import admin

from .models import IngestToken


@admin.register(IngestToken)
class IngestTokenAdmin(admin.ModelAdmin):
    list_display = (
        'label', 'token_prefix', 'scopes', 'is_active',
        'last_used_at', 'last_used_count', 'created_at',
    )
    list_filter = ('is_active',)
    search_fields = ('label', 'token_prefix')
    readonly_fields = (
        'token_hash', 'token_prefix', 'created_at',
        'last_used_at', 'last_used_ip', 'last_used_count',
    )
