from rest_framework import serializers
from .models import DailyStoreMetrics
from stores.models import Store


class DailyStoreMetricsSerializer(serializers.ModelSerializer):
    class Meta:
        model = DailyStoreMetrics
        fields = ['date', 'orders_count', 'revenue', 'out_of_stock_count']


class DashboardSummarySerializer(serializers.Serializer):
    """Summary KPIs across user's stores."""
    total_products = serializers.IntegerField()
    catalog_count = serializers.IntegerField(required=False)
    total_orders = serializers.IntegerField()
    out_of_stock_count = serializers.IntegerField()
    store_breakdown = serializers.ListField(child=serializers.DictField(), required=False)
