from django.urls import path

from .views import (
    DiscoveryJobCancelView,
    DiscoveryJobClearView,
    DiscoveryJobResumeView,
    DiscoveryJobContinueView,
    DiscoveryJobDetailView,
    DiscoveryJobDownloadView,
    DiscoveryJobIdsDownloadView,
    DiscoveryJobListCreateView,
    DiscoveryJobRowsView,
    DiscoveryOptionsView,
    DiscoveryTemplateView,
)

urlpatterns = [
    path('discovery/options/', DiscoveryOptionsView.as_view(), name='discovery-options'),
    path('discovery/template/', DiscoveryTemplateView.as_view(), name='discovery-template'),
    path('discovery/jobs/', DiscoveryJobListCreateView.as_view(), name='discovery-jobs'),
    path('discovery/jobs/clear/', DiscoveryJobClearView.as_view(), name='discovery-jobs-clear'),
    path('discovery/jobs/<uuid:job_id>/', DiscoveryJobDetailView.as_view(), name='discovery-job'),
    path('discovery/jobs/<uuid:job_id>/cancel/', DiscoveryJobCancelView.as_view(), name='discovery-job-cancel'),
    path('discovery/jobs/<uuid:job_id>/resume/', DiscoveryJobResumeView.as_view(), name='discovery-job-resume'),
    path('discovery/jobs/<uuid:job_id>/download/', DiscoveryJobDownloadView.as_view(), name='discovery-job-download'),
    path('discovery/jobs/<uuid:job_id>/ids/', DiscoveryJobIdsDownloadView.as_view(), name='discovery-job-ids'),
    path('discovery/jobs/<uuid:job_id>/rows/', DiscoveryJobRowsView.as_view(), name='discovery-job-rows'),
    path('discovery/jobs/<uuid:job_id>/continue/', DiscoveryJobContinueView.as_view(), name='discovery-job-continue'),
]
