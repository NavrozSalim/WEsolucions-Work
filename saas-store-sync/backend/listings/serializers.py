from rest_framework import serializers

from .models import ListingUpload, MarketplaceOrder, OrderShipment, StoreListing


class StoreListingSerializer(serializers.ModelSerializer):
    class Meta:
        model = StoreListing
        fields = [
            'id', 'store', 'external_product_key', 'external_variant_key',
            'title', 'description', 'brand', 'category', 'sku', 'barcode',
            'image_urls', 'inventory', 'infinite_quantity',
            'original_price', 'sale_price',
            'environment', 'action', 'status', 'validation_errors_json',
            'marketplace_response_json', 'last_uploaded_at',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'store', 'external_product_key', 'external_variant_key',
            'environment', 'action', 'status', 'validation_errors_json',
            'marketplace_response_json', 'last_uploaded_at',
            'created_at', 'updated_at',
        ]


class ListingUploadSerializer(serializers.ModelSerializer):
    user_name = serializers.SerializerMethodField()

    class Meta:
        model = ListingUpload
        fields = [
            'id', 'store', 'filename', 'source', 'action', 'status',
            'total_rows', 'success_rows', 'error_rows', 'rows_json',
            'message', 'user_name', 'created_at',
        ]

    def get_user_name(self, obj):
        user = obj.user
        full = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
        return full or getattr(user, 'username', '') or getattr(user, 'email', '')


class ListingInputSerializer(serializers.Serializer):
    """Payload for creating/updating a listing. Validation of business rules
    (required fields, price sanity) happens in the validator so errors are
    per-variant human strings, matching the bulk import flow."""
    product_key = serializers.CharField(required=False, allow_blank=True, default='')
    variant_key = serializers.CharField(required=False, allow_blank=True, default='')
    title = serializers.CharField(required=False, allow_blank=True, default='')
    description = serializers.CharField(required=False, allow_blank=True, default='')
    brand = serializers.CharField(required=False, allow_blank=True, default='')
    category = serializers.CharField(required=False, allow_blank=True, default='')
    sku = serializers.CharField(required=False, allow_blank=True, default='')
    barcode = serializers.CharField(required=False, allow_blank=True, default='')
    image_urls = serializers.CharField(required=False, allow_blank=True, default='')
    inventory = serializers.IntegerField(required=False, default=0)
    infinite_quantity = serializers.BooleanField(required=False, default=False)
    original_price = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=0)
    sale_price = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=0)
    action = serializers.ChoiceField(
        choices=['create', 'mapped'], required=False, default='create',
    )


class OrderShipmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderShipment
        fields = [
            'id', 'tracking_number', 'carrier', 'tracking_url',
            'shipped_at', 'status', 'created_at',
        ]


class MarketplaceOrderSerializer(serializers.ModelSerializer):
    shipments = OrderShipmentSerializer(many=True, read_only=True)

    class Meta:
        model = MarketplaceOrder
        fields = [
            'id', 'store', 'external_order_key', 'invoice_number',
            'customer_info_json', 'line_items_json',
            'status', 'shipping_status', 'total_amount_cents',
            'environment', 'shipments', 'created_at', 'updated_at',
        ]
