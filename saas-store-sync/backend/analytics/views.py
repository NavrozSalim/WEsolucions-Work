from django.utils import timezone
from django.db.models import Sum, Q
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from datetime import timedelta

from stores.models import Store
from catalog.models import ProductMapping
from .models import DailyStoreMetrics
from .serializers import DailyStoreMetricsSerializer, DashboardSummarySerializer


class DashboardSummaryView(APIView):
    """KPIs: total products, total orders (30d), out-of-stock count. Optional: ?store_id=<uuid>"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        stores = Store.objects.filter(user=user)
        store_id = request.query_params.get('store_id')
        if store_id:
            stores = stores.filter(id=store_id)
        store_ids = list(stores.values_list('id', flat=True))

        total_products = ProductMapping.objects.filter(store_id__in=store_ids).count()
        total_orders = 0
        out_of_stock_count = ProductMapping.objects.filter(
            store_id__in=store_ids
        ).filter(Q(store_stock=0) | Q(store_stock__isnull=True)).count()

        store_breakdown = []
        for store in stores:
            count = ProductMapping.objects.filter(store=store).count()
            synced = ProductMapping.objects.filter(store=store, sync_status='synced').count()
            needs_attention = ProductMapping.objects.filter(store=store, sync_status='needs_attention').count()
            store_breakdown.append({
                'store_id': str(store.id),
                'store_name': store.name,
                'product_count': count,
                'synced_count': synced,
                'needs_attention_count': needs_attention,
            })

        data = {
            'total_products': total_products,
            'catalog_count': total_products,
            'total_orders': total_orders,
            'out_of_stock_count': out_of_stock_count,
            'store_breakdown': store_breakdown,
        }
        serializer = DashboardSummarySerializer(data=data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.data)


class AnalyticsChartView(APIView):
    """Time-series analytics for charts. Query params: store_id (optional), range=7|30|custom, start_date, end_date."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        stores = Store.objects.filter(user=user)
        store_id = request.query_params.get('store_id')
        if store_id:
            stores = stores.filter(id=store_id)
        if not stores.exists():
            return Response({'daily_metrics': [], 'orders': [], 'revenue': [], 'out_of_stock': []})
        store_ids = list(stores.values_list('id', flat=True))

        from datetime import datetime
        range_type = request.query_params.get('range', '30')
        end_date = timezone.now().date()
        if range_type == '7':
            start_date = end_date - timedelta(days=7)
        elif range_type == 'custom':
            end_date_param = request.query_params.get('end_date')
            if end_date_param:
                try:
                    end_date = datetime.strptime(end_date_param, '%Y-%m-%d').date()
                except ValueError:
                    pass
            start_date = end_date - timedelta(days=30)
            start_param = request.query_params.get('start_date')
            if start_param:
                try:
                    start_date = datetime.strptime(start_param, '%Y-%m-%d').date()
                except (ValueError, TypeError):
                    pass
        else:
            start_date = end_date - timedelta(days=30)

        metrics = DailyStoreMetrics.objects.filter(
            store_id__in=store_ids,
            date__gte=start_date,
            date__lte=end_date
        )
        daily = metrics.values('date').annotate(
            orders_count=Sum('orders_count'),
            revenue=Sum('revenue'),
            out_of_stock_count=Sum('out_of_stock_count'),
        ).order_by('date')

        orders = [{'date': str(m['date']), 'count': m['orders_count']} for m in daily]
        revenue = [{'date': str(m['date']), 'value': float(m['revenue'] or 0)} for m in daily]
        out_of_stock = [{'date': str(m['date']), 'count': m['out_of_stock_count']} for m in daily]

        return Response({
            'daily_metrics': list(daily),
            'orders': orders,
            'revenue': revenue,
            'out_of_stock': out_of_stock,
        })
