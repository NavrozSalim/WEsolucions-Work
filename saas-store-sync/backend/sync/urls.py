from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    StoreSyncRunViewSet,
    SyncScheduleView,
    SyncJobStatusView,
    TriggerManualSyncView,
    TriggerManualUpdateView,
)

router = DefaultRouter()
router.register(r'sync-runs', StoreSyncRunViewSet, basename='syncrun')

urlpatterns = [
    path('stores/<uuid:store_pk>/sync/schedule/', SyncScheduleView.as_view(), name='sync-schedule'),
    path('stores/<uuid:store_pk>/sync/manual/', TriggerManualSyncView.as_view(), name='trigger-manual-sync'),
    path('stores/<uuid:store_pk>/sync/update/', TriggerManualUpdateView.as_view(), name='trigger-manual-update'),
    path('stores/<uuid:store_pk>/sync/jobs/<str:job_id>/', SyncJobStatusView.as_view(), name='sync-job-status'),
    path('stores/<uuid:store_pk>/', include(router.urls)),
]
