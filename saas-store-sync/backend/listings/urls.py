from django.urls import path

from .views import (
    StoreListingBulkUploadView,
    StoreListingDetailView,
    StoreListingListCreateView,
    StoreListingPublishView,
    StoreListingTemplateView,
    StoreOrderShippingCompleteView,
    StoreOrderShippingView,
    StoreOrdersView,
    StoreOrderTestView,
)

urlpatterns = [
    path('stores/<uuid:store_pk>/listings/', StoreListingListCreateView.as_view(), name='store-listings'),
    path('stores/<uuid:store_pk>/listings/template/', StoreListingTemplateView.as_view(), name='store-listings-template'),
    path('stores/<uuid:store_pk>/listings/bulk-upload/', StoreListingBulkUploadView.as_view(), name='store-listings-bulk-upload'),
    path('stores/<uuid:store_pk>/listings/publish/', StoreListingPublishView.as_view(), name='store-listings-publish'),
    path('stores/<uuid:store_pk>/listings/<uuid:pk>/', StoreListingDetailView.as_view(), name='store-listing-detail'),
    path('stores/<uuid:store_pk>/orders/', StoreOrdersView.as_view(), name='store-orders'),
    path('stores/<uuid:store_pk>/orders/test/', StoreOrderTestView.as_view(), name='store-orders-test'),
    path('stores/<uuid:store_pk>/orders/<uuid:pk>/shipping/', StoreOrderShippingView.as_view(), name='store-order-shipping'),
    path('stores/<uuid:store_pk>/orders/<uuid:pk>/shipping/complete/', StoreOrderShippingCompleteView.as_view(), name='store-order-shipping-complete'),
]
