from django.urls import path
from .views import DashboardSummaryView, AnalyticsChartView

urlpatterns = [
    path('analytics/dashboard/', DashboardSummaryView.as_view(), name='analytics-dashboard'),
    path('analytics/charts/', AnalyticsChartView.as_view(), name='analytics-charts'),
]
