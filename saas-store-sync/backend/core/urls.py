from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.views.static import serve
from .views import health, ready, metrics

urlpatterns = [
    path('health/', health),
    path('ready/', ready),
    path('metrics/', metrics),
    path('admin/', admin.site.urls),

    # API endpoints v1
    path('api/v1/auth/', include('users.urls')),
    path('api/v1/', include('stores.urls')),
    path('api/v1/', include('marketplace.urls')),
    path('api/v1/', include('vendor.urls')),
    path('api/v1/', include('catalog.urls')),
    path('api/v1/', include('listings.urls')),
    path('api/v1/', include('sync.urls')),
    path('api/v1/', include('analytics.urls')),
]

# Public media for listing photos (Reverb must fetch these without auth).
# Catalog ingest files under other media/ paths are not exposed here.
urlpatterns += [
    re_path(
        r'^media/listing_photos/(?P<path>.*)$',
        serve,
        {'document_root': settings.MEDIA_ROOT / 'listing_photos'},
    ),
]
