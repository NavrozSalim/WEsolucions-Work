from django.urls import path, include, register_converter
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
    CatalogScrapeCancelView,
    CatalogScrapeProgressView,
    CatalogUpdateTriggerView,
    CatalogJobStatusView,
    CatalogSyncLogsView,
    CatalogUpdateLogsView,
    CatalogScrapeRunsView,
    CatalogClearView,
    CatalogSampleTemplateView,
    CatalogStoresView,
    CatalogPushListingsView,
    CatalogPushListingsProgressView,
    CatalogPushListingsCancelView,
    StoreCriticalZeroView,
    StoreFailedZeroInventoryView,
    CatalogResetListingsPendingView,
    CatalogActivityLogListView,
)
from .mydeal_views import (
    MydealTemplateStatusView,
    MydealTemplateUploadView,
    MydealTemplateExportView,
)
from .ingest_views import (
    HebIngestView,
    HebIngestUrlsView,
    HebIngestNextJobView,
    HebIngestJobStatusView,
    HebIngestCompleteJobView,
    VendorIngestView,
    VendorIngestUrlsView,
    VendorIngestNextJobView,
    VendorIngestJobStatusView,
    VendorIngestCompleteJobView,
)
from .excel_ingest_views import (
    ExcelIngestCompleteJobView,
    ExcelIngestJobFileView,
    ExcelIngestJobStatusView,
    ExcelIngestNextJobView,
    ExcelIngestResultView,
)
from .auto_bot_views import (
    AutoBotJobCancelView,
    AutoBotJobDetailView,
    AutoBotJobDownloadView,
    AutoBotJobListCreateView,
    AutoBotLaptopsView,
)


class ExcelVendorConverter:
    """Matches only the Auto Bot Excel vendors, so HEB/Costco keep the generic routes."""

    regex = 'reverb|etsy'

    def to_python(self, value):
        return value

    def to_url(self, value):
        return value


register_converter(ExcelVendorConverter, 'excel_vendor')

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
    path('stores/<uuid:store_pk>/catalog/scrape/cancel/', CatalogScrapeCancelView.as_view(), name='store-catalog-scrape-cancel'),
    # store_pk must be a valid UUID path segment — literals like "STORE_UUID" 404 before this view runs.
    path('stores/<uuid:store_pk>/catalog/scrape/progress/', CatalogScrapeProgressView.as_view(), name='store-catalog-scrape-progress'),
    path('stores/<uuid:store_pk>/catalog/scrape/runs/', CatalogScrapeRunsView.as_view(), name='store-catalog-scrape-runs'),
    path('stores/<uuid:store_pk>/catalog/update/', CatalogUpdateTriggerView.as_view(), name='store-catalog-update'),
    path('stores/<uuid:store_pk>/catalog/sync/logs/', CatalogSyncLogsView.as_view(), name='store-catalog-sync-logs'),
    path('stores/<uuid:store_pk>/catalog/update/logs/', CatalogUpdateLogsView.as_view(), name='store-catalog-update-logs'),
    path('stores/<uuid:store_pk>/catalog/jobs/<str:job_id>/', CatalogJobStatusView.as_view(), name='store-catalog-job-status'),
    path('stores/<uuid:store_pk>/catalog/clear/', CatalogClearView.as_view(), name='catalog-clear'),
    path('stores/<uuid:store_pk>/catalog/activity-logs/', CatalogActivityLogListView.as_view(), name='catalog-activity-logs'),
    path('stores/<uuid:store_pk>/catalog/push-listings/', CatalogPushListingsView.as_view(), name='catalog-push-listings'),
    path(
        'stores/<uuid:store_pk>/catalog/push-listings/progress/',
        CatalogPushListingsProgressView.as_view(),
        name='store-catalog-push-listings-progress',
    ),
    path(
        'stores/<uuid:store_pk>/catalog/push-listings/cancel/',
        CatalogPushListingsCancelView.as_view(),
        name='store-catalog-push-listings-cancel',
    ),
    path('stores/<uuid:store_pk>/catalog/critical-zero/', StoreCriticalZeroView.as_view(), name='catalog-critical-zero'),
    path('stores/<uuid:store_pk>/catalog/failed-zero/', StoreFailedZeroInventoryView.as_view(), name='catalog-failed-zero'),
    path('stores/<uuid:store_pk>/catalog/reset-pending/', CatalogResetListingsPendingView.as_view(), name='catalog-reset-pending'),
    path('stores/<uuid:store_pk>/mydeal/templates/status/', MydealTemplateStatusView.as_view(), name='mydeal-template-status'),
    path('stores/<uuid:store_pk>/mydeal/templates/upload/', MydealTemplateUploadView.as_view(), name='mydeal-template-upload'),
    path('stores/<uuid:store_pk>/mydeal/templates/export/', MydealTemplateExportView.as_view(), name='mydeal-template-export'),
    path('ingest/heb/', HebIngestView.as_view(), name='ingest-heb'),
    path('ingest/heb/urls/', HebIngestUrlsView.as_view(), name='ingest-heb-urls'),
    path('ingest/heb/next-job/', HebIngestNextJobView.as_view(), name='ingest-heb-next-job'),
    path(
        'ingest/heb/jobs/<uuid:job_id>/',
        HebIngestJobStatusView.as_view(),
        name='ingest-heb-job-status',
    ),
    path(
        'ingest/heb/jobs/<uuid:job_id>/complete/',
        HebIngestCompleteJobView.as_view(),
        name='ingest-heb-complete-job',
    ),
    # Auto Bot Excel jobs (Reverb / Etsy). Must sit before the generic <str:vendor> routes.
    path('ingest/<excel_vendor:vendor>/', ExcelIngestResultView.as_view(), name='ingest-excel-results'),
    path('ingest/<excel_vendor:vendor>/next-job/', ExcelIngestNextJobView.as_view(), name='ingest-excel-next-job'),
    path(
        'ingest/<excel_vendor:vendor>/jobs/<uuid:job_id>/',
        ExcelIngestJobStatusView.as_view(),
        name='ingest-excel-job-status',
    ),
    path(
        'ingest/<excel_vendor:vendor>/jobs/<uuid:job_id>/file/',
        ExcelIngestJobFileView.as_view(),
        name='ingest-excel-job-file',
    ),
    path(
        'ingest/<excel_vendor:vendor>/jobs/<uuid:job_id>/complete/',
        ExcelIngestCompleteJobView.as_view(),
        name='ingest-excel-complete-job',
    ),
    path('auto-bot/jobs/', AutoBotJobListCreateView.as_view(), name='auto-bot-jobs'),
    path('auto-bot/jobs/<uuid:job_id>/', AutoBotJobDetailView.as_view(), name='auto-bot-job-detail'),
    path('auto-bot/jobs/<uuid:job_id>/cancel/', AutoBotJobCancelView.as_view(), name='auto-bot-job-cancel'),
    path(
        'auto-bot/jobs/<uuid:job_id>/download/<str:kind>/',
        AutoBotJobDownloadView.as_view(),
        name='auto-bot-job-download',
    ),
    path('auto-bot/laptops/', AutoBotLaptopsView.as_view(), name='auto-bot-laptops'),
    path('ingest/<str:vendor>/', VendorIngestView.as_view(), name='ingest-vendor'),
    path(
        'ingest/<str:vendor>/urls/',
        VendorIngestUrlsView.as_view(),
        name='ingest-vendor-urls',
    ),
    path(
        'ingest/<str:vendor>/next-job/',
        VendorIngestNextJobView.as_view(),
        name='ingest-vendor-next-job',
    ),
    path(
        'ingest/<str:vendor>/jobs/<uuid:job_id>/',
        VendorIngestJobStatusView.as_view(),
        name='ingest-vendor-job-status',
    ),
    path(
        'ingest/<str:vendor>/jobs/<uuid:job_id>/complete/',
        VendorIngestCompleteJobView.as_view(),
        name='ingest-vendor-complete-job',
    ),
    path('stores/<uuid:store_pk>/', include(router.urls)),
]
