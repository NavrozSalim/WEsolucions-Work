from django.contrib import admin
from django.urls import path, include
from .views import health, ready, metrics

urlpatterns = [
    path('health/', health),
    # Alias for monitors/scripts that assume health lives under the API prefix.
    path('api/v1/health/', health),
    path('ready/', ready),
    path('metrics/', metrics),
    path('admin/', admin.site.urls),
    
    # API endpoints v1
    path('api/v1/auth/', include('users.urls')),
    path('api/v1/', include('stores.urls')),
    path('api/v1/', include('marketplace.urls')),
    path('api/v1/', include('vendor.urls')),
    path('api/v1/', include('catalog.urls')),
    path('api/v1/', include('sync.urls')),
    path('api/v1/', include('analytics.urls')),
]
