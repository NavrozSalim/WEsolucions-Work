from django.utils import timezone
from django.db.models import Sum, Q, Count, Max
from django.db.models.functions import TruncDate
from django.core.cache import cache
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from datetime import timedelta, datetime, date as date_cls, time as time_cls

from stores.models import Store
from catalog.models import ProductMapping
from listings.models import MarketplaceOrder, OrderStatus
from .serializers import DashboardSummarySerializer
from .live_jobs import collect_live_jobs

OPEN_ORDER_STATUSES = (OrderStatus.NEW, OrderStatus.PAID)
IN_SHIPPING_STATUSES = (OrderStatus.SENT, OrderStatus.SHIPPING_SUBMITTED)
COMPLETE_ORDER_STATUSES = (OrderStatus.SHIPPING_COMPLETE,)


def _live_sync_snapshot(store_ids):
    """Current ProductMapping bucket counts across the given stores."""
    if not store_ids:
        return {
            'synced': 0,
            'pending': 0,
            'scraped': 0,
            'failed': 0,
            'needs_attention': 0,
            'out_of_stock': 0,
            'total': 0,
        }
    rows = ProductMapping.objects.filter(store_id__in=store_ids).aggregate(
        total=Count('id'),
        synced=Count('id', filter=Q(sync_status='synced')),
        pending=Count('id', filter=Q(sync_status='pending')),
        scraped=Count('id', filter=Q(sync_status='scraped')),
        failed=Count('id', filter=Q(sync_status='failed')),
        needs_attention=Count('id', filter=Q(sync_status='needs_attention')),
        out_of_stock=Count('id', filter=Q(store_stock=0) | Q(store_stock__isnull=True)),
    )
    return {k: (rows.get(k) or 0) for k in (
        'synced', 'pending', 'scraped', 'failed', 'needs_attention', 'out_of_stock', 'total',
    )}


def _orders_by_day(store_ids, start_date, end_date):
    """Live order counts keyed by local date from MarketplaceOrder.created_at."""
    if not store_ids:
        return {}
    start_dt = timezone.make_aware(datetime.combine(start_date, time_cls.min))
    end_dt = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time_cls.min))
    rows = (
        MarketplaceOrder.objects.filter(
            store_id__in=store_ids,
            created_at__gte=start_dt,
            created_at__lt=end_dt,
        )
        .annotate(day=TruncDate('created_at'))
        .values('day')
        .annotate(
            orders_count=Count('id'),
            revenue_cents=Sum('total_amount_cents'),
        )
        .order_by('day')
    )
    out = {}
    for row in rows:
        day = row['day']
        if day is None:
            continue
        out[day] = {
            'orders_count': row.get('orders_count') or 0,
            'revenue': float((row.get('revenue_cents') or 0) / 100.0),
        }
    return out


def _chart_date_range(request):
    range_type = request.query_params.get('range', '30')
    end_date = timezone.now().date()
    if range_type == '7':
        start_date = end_date - timedelta(days=6)
    elif range_type == 'custom':
        end_date_param = request.query_params.get('end_date')
        if end_date_param:
            try:
                end_date = datetime.strptime(end_date_param, '%Y-%m-%d').date()
            except ValueError:
                pass
        start_date = end_date - timedelta(days=29)
        start_param = request.query_params.get('start_date')
        if start_param:
            try:
                start_date = datetime.strptime(start_param, '%Y-%m-%d').date()
            except (ValueError, TypeError):
                pass
    else:
        start_date = end_date - timedelta(days=29)
    return start_date, end_date


class DashboardSummaryView(APIView):
    """Ops KPIs: listings, sync buckets, orders. Optional: ?store_id=<uuid>"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        store_id = request.query_params.get('store_id')
        cache_key = f'analytics:dashboard:v4:user={user.id}:store={store_id or "all"}'
        cached = cache.get(cache_key)
        if cached is not None:
            return Response(cached)

        stores = Store.objects.filter(user=user).select_related('marketplace').defer(
            'api_token', 'kogan_service_account_json',
        )
        if store_id:
            stores = stores.filter(id=store_id)
        store_ids = list(stores.values_list('id', flat=True))

        mappings = ProductMapping.objects.filter(store_id__in=store_ids)
        total_products = mappings.count()
        out_of_stock_count = mappings.filter(
            Q(store_stock=0) | Q(store_stock__isnull=True)
        ).count()

        recent_since = timezone.now() - timedelta(days=30)
        order_totals = MarketplaceOrder.objects.filter(store_id__in=store_ids).aggregate(
            total_orders=Count('id'),
            orders_open_count=Count('id', filter=Q(status__in=OPEN_ORDER_STATUSES)),
            orders_in_shipping_count=Count('id', filter=Q(status__in=IN_SHIPPING_STATUSES)),
            orders_complete_count=Count('id', filter=Q(status__in=COMPLETE_ORDER_STATUSES)),
            orders_recent_count=Count('id', filter=Q(created_at__gte=recent_since)),
        )

        bd_rows = (
            mappings
            .values('store_id')
            .annotate(
                product_count=Count('id'),
                synced_count=Count('id', filter=Q(sync_status='synced')),
                needs_attention_count=Count('id', filter=Q(sync_status='needs_attention')),
                pending_count=Count('id', filter=Q(sync_status='pending')),
                failed_count=Count('id', filter=Q(sync_status='failed')),
                scraped_count=Count('id', filter=Q(sync_status='scraped')),
                out_of_stock_count=Count(
                    'id', filter=Q(store_stock=0) | Q(store_stock__isnull=True)
                ),
                last_sync_at=Max('last_sync_time'),
                last_scrape_at=Max('last_scrape_time'),
            )
        )
        bd_map = {row['store_id']: row for row in bd_rows}

        order_rows = (
            MarketplaceOrder.objects.filter(store_id__in=store_ids)
            .values('store_id')
            .annotate(
                order_count=Count('id'),
                orders_open_count=Count('id', filter=Q(status__in=OPEN_ORDER_STATUSES)),
                orders_in_shipping_count=Count('id', filter=Q(status__in=IN_SHIPPING_STATUSES)),
                orders_complete_count=Count('id', filter=Q(status__in=COMPLETE_ORDER_STATUSES)),
                orders_recent_count=Count('id', filter=Q(created_at__gte=recent_since)),
            )
        )
        order_map = {row['store_id']: row for row in order_rows}

        store_breakdown = []
        needs_attention_total = 0
        pending_total = 0
        failed_total = 0
        scraped_total = 0
        synced_total = 0
        for store in stores:
            row = bd_map.get(store.id, {})
            o_row = order_map.get(store.id, {})
            needs_attention = row.get('needs_attention_count', 0) or 0
            pending = row.get('pending_count', 0) or 0
            failed = row.get('failed_count', 0) or 0
            scraped = row.get('scraped_count', 0) or 0
            synced = row.get('synced_count', 0) or 0
            needs_attention_total += needs_attention
            pending_total += pending
            failed_total += failed
            scraped_total += scraped
            synced_total += synced
            last_sync = row.get('last_sync_at')
            last_scrape = row.get('last_scrape_at')
            latest = None
            for ts in (last_sync, last_scrape):
                if ts and (latest is None or ts > latest):
                    latest = ts
            store_breakdown.append({
                'store_id': str(store.id),
                'store_name': store.name,
                'marketplace_name': store.marketplace.name if store.marketplace_id else None,
                'connection_status': store.connection_status,
                'is_active': store.is_active,
                'product_count': row.get('product_count', 0) or 0,
                'synced_count': synced,
                'needs_attention_count': needs_attention,
                'pending_count': pending,
                'failed_count': failed,
                'scraped_count': scraped,
                'out_of_stock_count': row.get('out_of_stock_count', 0) or 0,
                'last_sync_at': latest.isoformat() if latest else None,
                'order_count': o_row.get('order_count', 0) or 0,
                'orders_open_count': o_row.get('orders_open_count', 0) or 0,
                'orders_in_shipping_count': o_row.get('orders_in_shipping_count', 0) or 0,
                'orders_complete_count': o_row.get('orders_complete_count', 0) or 0,
                'orders_recent_count': o_row.get('orders_recent_count', 0) or 0,
            })

        data = {
            'total_products': total_products,
            'catalog_count': total_products,
            'total_orders': order_totals.get('total_orders') or 0,
            'orders_open_count': order_totals.get('orders_open_count') or 0,
            'orders_in_shipping_count': order_totals.get('orders_in_shipping_count') or 0,
            'orders_complete_count': order_totals.get('orders_complete_count') or 0,
            'orders_recent_count': order_totals.get('orders_recent_count') or 0,
            'out_of_stock_count': out_of_stock_count,
            'needs_attention_count': needs_attention_total,
            'pending_count': pending_total,
            'failed_count': failed_total,
            'scraped_count': scraped_total,
            'synced_count': synced_total,
            'store_breakdown': store_breakdown,
        }
        serializer = DashboardSummarySerializer(data=data)
        serializer.is_valid(raise_exception=True)
        cache.set(cache_key, serializer.data, 45)
        return Response(serializer.data)


class LiveJobsView(APIView):
    """Active scrapes / pushes across the user's stores (server + desktop runners)."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        jobs = collect_live_jobs(request.user)
        return Response({
            'jobs': jobs,
            'checked_at': timezone.now().isoformat(),
        })


class AnalyticsChartView(APIView):
    """Time-series + current sync mix. Query params: store_id, range=7|30|custom."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from .models import DailyStoreMetrics

        user = request.user
        stores = Store.objects.filter(user=user)
        store_id = request.query_params.get('store_id')
        if store_id:
            stores = stores.filter(id=store_id)
        if not stores.exists():
            return Response({
                'daily_metrics': [],
                'orders': [],
                'revenue': [],
                'out_of_stock': [],
                'sync_health': [],
                'sync_mix': _live_sync_snapshot([]),
            })
        store_ids = list(stores.values_list('id', flat=True))
        start_date, end_date = _chart_date_range(request)
        live = _live_sync_snapshot(store_ids)
        live_orders = _orders_by_day(store_ids, start_date, end_date)
        today = timezone.now().date()

        metrics = DailyStoreMetrics.objects.filter(
            store_id__in=store_ids,
            date__gte=start_date,
            date__lte=end_date,
        )
        daily = list(
            metrics.values('date').annotate(
                orders_count=Sum('orders_count'),
                revenue=Sum('revenue'),
                out_of_stock_count=Sum('out_of_stock_count'),
                pending_count=Sum('pending_count'),
                failed_count=Sum('failed_count'),
                needs_attention_count=Sum('needs_attention_count'),
                synced_count=Sum('synced_count'),
                scraped_count=Sum('scraped_count'),
            ).order_by('date')
        )
        by_date = {row['date']: row for row in daily}

        cursor = start_date
        series = []
        while cursor <= end_date:
            row = by_date.get(cursor)
            order_live = live_orders.get(cursor) or {}
            # Prefer live MarketplaceOrder counts for the orders series.
            orders_count = order_live.get('orders_count')
            revenue = order_live.get('revenue')
            if orders_count is None and row:
                orders_count = row.get('orders_count') or 0
                revenue = float(row.get('revenue') or 0)
            if orders_count is None:
                orders_count = 0
            if revenue is None:
                revenue = 0.0

            if cursor == today or (row is None and cursor == end_date and end_date == today):
                point = {
                    'date': cursor,
                    'orders_count': orders_count,
                    'revenue': revenue,
                    'out_of_stock_count': live['out_of_stock'],
                    'pending_count': live['pending'],
                    'failed_count': live['failed'],
                    'needs_attention_count': live['needs_attention'],
                    'synced_count': live['synced'],
                    'scraped_count': live['scraped'],
                }
            elif row or order_live:
                point = {
                    'date': cursor,
                    'orders_count': orders_count,
                    'revenue': revenue,
                    'out_of_stock_count': (row or {}).get('out_of_stock_count') or 0,
                    'pending_count': (row or {}).get('pending_count') or 0,
                    'failed_count': (row or {}).get('failed_count') or 0,
                    'needs_attention_count': (row or {}).get('needs_attention_count') or 0,
                    'synced_count': (row or {}).get('synced_count') or 0,
                    'scraped_count': (row or {}).get('scraped_count') or 0,
                }
            else:
                point = None
            if point is not None:
                series.append(point)
            cursor += timedelta(days=1)

        if not series:
            series = [{
                'date': today,
                'orders_count': (live_orders.get(today) or {}).get('orders_count') or 0,
                'revenue': (live_orders.get(today) or {}).get('revenue') or 0,
                'out_of_stock_count': live['out_of_stock'],
                'pending_count': live['pending'],
                'failed_count': live['failed'],
                'needs_attention_count': live['needs_attention'],
                'synced_count': live['synced'],
                'scraped_count': live['scraped'],
            }]

        def _fmt(d):
            if isinstance(d, date_cls):
                return d.isoformat()
            return str(d)

        orders = [{'date': _fmt(m['date']), 'count': m['orders_count']} for m in series]
        revenue = [{'date': _fmt(m['date']), 'value': m['revenue']} for m in series]
        out_of_stock = [{'date': _fmt(m['date']), 'count': m['out_of_stock_count']} for m in series]
        sync_health = [{
            'date': _fmt(m['date']),
            'pending': m['pending_count'],
            'needs_attention': m['needs_attention_count'],
            'failed': m['failed_count'],
            'synced': m['synced_count'],
        } for m in series]

        return Response({
            'daily_metrics': [
                {**m, 'date': _fmt(m['date'])} for m in series
            ],
            'orders': orders,
            'revenue': revenue,
            'out_of_stock': out_of_stock,
            'sync_health': sync_health,
            'sync_mix': live,
        })
