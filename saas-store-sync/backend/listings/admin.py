from django.contrib import admin

from .models import MarketplaceOrder, OrderShipment, StoreListing


@admin.register(StoreListing)
class StoreListingAdmin(admin.ModelAdmin):
    list_display = ('external_variant_key', 'title', 'store', 'status', 'environment', 'last_uploaded_at')
    list_filter = ('status', 'environment')
    search_fields = ('external_variant_key', 'sku', 'title')


@admin.register(MarketplaceOrder)
class MarketplaceOrderAdmin(admin.ModelAdmin):
    list_display = ('invoice_number', 'external_order_key', 'store', 'status', 'shipping_status', 'environment')
    list_filter = ('status', 'environment')
    search_fields = ('invoice_number', 'external_order_key')


@admin.register(OrderShipment)
class OrderShipmentAdmin(admin.ModelAdmin):
    list_display = ('tracking_number', 'carrier', 'order', 'status', 'created_at')
