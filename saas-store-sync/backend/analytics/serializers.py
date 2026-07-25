from rest_framework import serializers
from .models import DailyStoreMetrics


class DailyStoreMetricsSerializer(serializers.ModelSerializer):
    class Meta:
        model = DailyStoreMetrics
        fields = [
            'date',
            'orders_count',
            'revenue',
            'out_of_stock_count',
            'pending_count',
            'failed_count',
            'needs_attention_count',
            'synced_count',
            'scraped_count',
        ]


class DashboardSummarySerializer(serializers.Serializer):
    """Summary KPIs across user's stores."""
    total_products = serializers.IntegerField()
    catalog_count = serializers.IntegerField(required=False)
    total_orders = serializers.IntegerField()
    out_of_stock_count = serializers.IntegerField()
    needs_attention_count = serializers.IntegerField(required=False, default=0)
    pending_count = serializers.IntegerField(required=False, default=0)
    failed_count = serializers.IntegerField(required=False, default=0)
    scraped_count = serializers.IntegerField(required=False, default=0)
    synced_count = serializers.IntegerField(required=False, default=0)
    store_breakdown = serializers.ListField(child=serializers.DictField(), required=False)
