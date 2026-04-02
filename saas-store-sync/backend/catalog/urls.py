from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ProductMappingViewSet,
    CatalogUploadView,
    CatalogUploadListView,
    CatalogUploadDetailView,
    CatalogUploadDeleteView,
    CatalogUploadErrorFileView,
    StoreCatalogUploadView,
    CatalogSyncTriggerView,
    CatalogScrapeTriggerView,
    CatalogUpdateTriggerView,
    CatalogJobStatusView,
    CatalogSyncLogsView,
    CatalogUpdateLogsView,
    CatalogScrapeRunsView,
    CatalogClearView,
    CatalogSampleTemplateView,
    CatalogStoresView,
    CatalogPushListingsView,
    StoreCriticalZeroView,
)

router = DefaultRouter()
router.register(r'products', ProductMappingViewSet, basename='productmapping')

urlpatterns = [
    path('catalog/stores/', CatalogStoresView.as_view(), name='catalog-stores'),
    path('catalog/upload/', CatalogUploadView.as_view(), name='catalog-upload'),
    path('catalog/sample-template/', CatalogSampleTemplateView.as_view(), name='catalog-sample-template'),
    path('stores/<uuid:store_pk>/catalog/upload/', StoreCatalogUploadView.as_view(), name='store-catalog-upload'),
    path('stores/<uuid:store_pk>/catalog/uploads/', CatalogUploadListView.as_view(), name='store-catalog-uploads'),
    path('stores/<uuid:store_pk>/catalog/uploads/<uuid:upload_id>/', CatalogUploadDetailView.as_view(), name='store-catalog-upload-detail'),
    path('stores/<uuid:store_pk>/catalog/uploads/<uuid:upload_id>/delete/', CatalogUploadDeleteView.as_view(), name='store-catalog-upload-delete'),
    path('stores/<uuid:store_pk>/catalog/uploads/<uuid:upload_id>/errors/', CatalogUploadErrorFileView.as_view(), name='store-catalog-upload-errors'),
    path('stores/<uuid:store_pk>/catalog/sync/', CatalogSyncTriggerView.as_view(), name='store-catalog-sync'),
    path('stores/<uuid:store_pk>/catalog/scrape/', CatalogScrapeTriggerView.as_view(), name='store-catalog-scrape'),
    path('stores/<uuid:store_pk>/catalog/scrape/runs/', CatalogScrapeRunsView.as_view(), name='store-catalog-scrape-runs'),
    path('stores/<uuid:store_pk>/catalog/update/', CatalogUpdateTriggerView.as_view(), name='store-catalog-update'),
    path('stores/<uuid:store_pk>/catalog/sync/logs/', CatalogSyncLogsView.as_view(), name='store-catalog-sync-logs'),
    path('stores/<uuid:store_pk>/catalog/update/logs/', CatalogUpdateLogsView.as_view(), name='store-catalog-update-logs'),
    path('stores/<uuid:store_pk>/catalog/jobs/<str:job_id>/', CatalogJobStatusView.as_view(), name='store-catalog-job-status'),
    path('stores/<uuid:store_pk>/catalog/clear/', CatalogClearView.as_view(), name='catalog-clear'),
    path('stores/<uuid:store_pk>/catalog/push-listings/', CatalogPushListingsView.as_view(), name='catalog-push-listings'),
    path('stores/<uuid:store_pk>/catalog/critical-zero/', StoreCriticalZeroView.as_view(), name='catalog-critical-zero'),
    path('stores/<uuid:store_pk>/', include(router.urls)),
]
