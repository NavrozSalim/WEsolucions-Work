from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import StoreViewSet
from . import auto_order_bot_views as aob

router = DefaultRouter()
router.register(r'stores', StoreViewSet, basename='store')

_AOB = 'stores/<uuid:store_pk>/auto-order-bot/'

urlpatterns = [
    path(_AOB, aob.SettingsView.as_view(), name='auto-order-bot-settings'),
    path(_AOB + 'files/<str:kind>/', aob.FileView.as_view(), name='auto-order-bot-file'),
    path(_AOB + 'accounts/', aob.AccountsView.as_view(), name='auto-order-bot-accounts'),
    path(_AOB + 'accounts/<uuid:account_id>/', aob.AccountDetailView.as_view(), name='auto-order-bot-account'),
    path(_AOB + 'runs/', aob.RunsView.as_view(), name='auto-order-bot-runs'),
    path(_AOB + 'runs/<uuid:run_id>/', aob.RunDetailView.as_view(), name='auto-order-bot-run'),
    path(_AOB + 'runs/<uuid:run_id>/download/<str:kind>/', aob.RunDownloadView.as_view(), name='auto-order-bot-download'),
    path('', include(router.urls)),
]
