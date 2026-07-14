from django.contrib import admin

from .models import MarketplaceOrder, OrderShipment, StoreListing, SupportTicket, TicketMessage


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


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ('subject', 'customer_name', 'store', 'status', 'unread_count', 'environment', 'last_message_at')
    list_filter = ('status', 'environment')
    search_fields = ('subject', 'customer_name', 'customer_email', 'external_ticket_key')


@admin.register(TicketMessage)
class TicketMessageAdmin(admin.ModelAdmin):
    list_display = ('ticket', 'direction', 'sender_name', 'delivered_to_marketplace', 'sent_at')
    list_filter = ('direction', 'delivered_to_marketplace')
