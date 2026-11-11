from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import StoreViewSet
from . import auto_order_bot_views as aob
from . import auto_order_bot_ingest as aoi

router = DefaultRouter()
router.register(r'stores', StoreViewSet, basename='store')

_AOB = 'stores/<uuid:store_pk>/auto-order-bot/'

urlpatterns = [
    path(_AOB, aob.SettingsView.as_view(), name='auto-order-bot-settings'),
    path(_AOB + 'files/<str:kind>/', aob.FileView.as_view(), name='auto-order-bot-file'),
    path(_AOB + 'accounts/', aob.AccountsView.as_view(), name='auto-order-bot-accounts'),
    path(_AOB + 'accounts/<uuid:account_id>/', aob.AccountDetailView.as_view(), name='auto-order-bot-account'),
    path(_AOB + 'runs/', aob.RunsView.as_view(), name='auto-order-bot-runs'),
    path(_AOB + 'runs/<uuid:run_id>/cancel/', aob.RunCancelView.as_view(), name='auto-order-bot-cancel'),
    path(_AOB + 'runs/<uuid:run_id>/', aob.RunDetailView.as_view(), name='auto-order-bot-run'),
    path(_AOB + 'runs/<uuid:run_id>/download/<str:kind>/', aob.RunDownloadView.as_view(), name='auto-order-bot-download'),
    # Laptop agent (ingest bearer token with scope "order-bot")
    path('ingest/order-bot/next-job/', aoi.NextJobView.as_view(), name='order-bot-next-job'),
    path('ingest/order-bot/jobs/<uuid:run_id>/', aoi.JobStatusView.as_view(), name='order-bot-job-status'),
    path('ingest/order-bot/jobs/<uuid:run_id>/file/<str:kind>/', aoi.JobFileView.as_view(), name='order-bot-job-file'),
    path('ingest/order-bot/jobs/<uuid:run_id>/progress/', aoi.JobProgressView.as_view(), name='order-bot-job-progress'),
    path('ingest/order-bot/jobs/<uuid:run_id>/orders-file/', aoi.JobOrdersFileView.as_view(), name='order-bot-job-orders-file'),
    path('ingest/order-bot/jobs/<uuid:run_id>/complete/', aoi.JobCompleteView.as_view(), name='order-bot-job-complete'),
    path('', include(router.urls)),
]
