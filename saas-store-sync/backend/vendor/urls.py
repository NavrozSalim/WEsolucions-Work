from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import VendorViewSet
from .aliexpress_views import (
    AliExpressCallbackView,
    AliExpressConnectView,
    AliExpressDisconnectView,
    AliExpressStatusView,
)

router = DefaultRouter()
router.register(r'vendors', VendorViewSet, basename='vendor')

urlpatterns = [
    path('', include(router.urls)),
    path('vendors/aliexpress/connect/', AliExpressConnectView.as_view(), name='aliexpress_connect'),
    path('vendors/aliexpress/callback/', AliExpressCallbackView.as_view(), name='aliexpress_callback'),
    path('vendors/aliexpress/status/', AliExpressStatusView.as_view(), name='aliexpress_status'),
    path('vendors/aliexpress/disconnect/', AliExpressDisconnectView.as_view(), name='aliexpress_disconnect'),
]
