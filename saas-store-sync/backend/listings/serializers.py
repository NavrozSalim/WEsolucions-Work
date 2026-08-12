from rest_framework import serializers

from .models import ListingUpload, MarketplaceOrder, OrderShipment, StoreListing, SupportTicket, TicketMessage
from .etsy import listings as etsy_listings
from .reverb import listings as reverb_listings


class StoreListingSerializer(serializers.ModelSerializer):
    make = serializers.SerializerMethodField()
    model = serializers.SerializerMethodField()
    finish = serializers.SerializerMethodField()
    year = serializers.SerializerMethodField()
    condition_uuid = serializers.SerializerMethodField()
    category_uuid = serializers.SerializerMethodField()
    currency = serializers.SerializerMethodField()
    upc_does_not_apply = serializers.SerializerMethodField()
    publish_status = serializers.SerializerMethodField()
    free_shipping = serializers.SerializerMethodField()
    margin_pct = serializers.SerializerMethodField()
    margin_display = serializers.SerializerMethodField()
    # MyDeal extra fields
    tags = serializers.SerializerMethodField()
    specifications = serializers.SerializerMethodField()
    condition = serializers.SerializerMethodField()
    gtin = serializers.SerializerMethodField()
    mpn = serializers.SerializerMethodField()
    weight = serializers.SerializerMethodField()
    weight_unit = serializers.SerializerMethodField()
    length = serializers.SerializerMethodField()
    height = serializers.SerializerMethodField()
    width = serializers.SerializerMethodField()
    dimension_unit = serializers.SerializerMethodField()
    shipping_cost_category = serializers.SerializerMethodField()
    shipping_cost_standard = serializers.SerializerMethodField()
    custom_freight_scheme_id = serializers.SerializerMethodField()
    is_direct_import = serializers.SerializerMethodField()
    max_days_for_delivery = serializers.SerializerMethodField()
    delivery_time = serializers.SerializerMethodField()
    has_48_hours_dispatch = serializers.SerializerMethodField()
    # Etsy extra fields
    taxonomy_id = serializers.SerializerMethodField()
    who_made = serializers.SerializerMethodField()
    when_made = serializers.SerializerMethodField()
    shipping_profile_id = serializers.SerializerMethodField()
    readiness_state_id = serializers.SerializerMethodField()

    class Meta:
        model = StoreListing
        fields = [
            'id', 'store', 'external_product_key', 'external_variant_key',
            'title', 'description', 'brand', 'category', 'sku', 'barcode', 'options',
            'option_1_name', 'option_1_value', 'option_2_name', 'option_2_value',
            'option_3_name', 'option_3_value', 'option_4_name', 'option_4_value',
            'variation_image_url',
            'vendor_url', 'vendor_id', 'source_vendor_code', 'image_urls', 'inventory', 'infinite_quantity',
            'original_price', 'sale_price', 'vendor_price',
            'make', 'model', 'finish', 'year',
            'condition_uuid', 'category_uuid', 'currency', 'upc_does_not_apply',
            'publish_status', 'free_shipping', 'margin_pct', 'margin_display',
            'tags', 'specifications', 'condition', 'gtin', 'mpn',
            'weight', 'weight_unit', 'length', 'height', 'width', 'dimension_unit',
            'shipping_cost_category', 'shipping_cost_standard', 'custom_freight_scheme_id',
            'is_direct_import', 'max_days_for_delivery', 'delivery_time', 'has_48_hours_dispatch',
            'taxonomy_id', 'who_made', 'when_made', 'shipping_profile_id', 'readiness_state_id',
            'inventory_sync_status', 'last_scrape_at', 'last_scrape_error',
            'environment', 'action', 'status', 'validation_errors_json',
            'marketplace_response_json', 'last_uploaded_at',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'store', 'external_product_key', 'external_variant_key',
            'environment', 'action', 'status', 'validation_errors_json',
            'marketplace_response_json', 'last_uploaded_at',
            'created_at', 'updated_at',
            'make', 'model', 'finish', 'year',
            'condition_uuid', 'category_uuid', 'currency', 'upc_does_not_apply',
            'publish_status', 'free_shipping', 'margin_pct', 'margin_display',
            'tags', 'specifications', 'condition', 'gtin', 'mpn',
            'weight', 'weight_unit', 'length', 'height', 'width', 'dimension_unit',
            'shipping_cost_category', 'shipping_cost_standard', 'custom_freight_scheme_id',
            'is_direct_import', 'max_days_for_delivery', 'delivery_time', 'has_48_hours_dispatch',
            'taxonomy_id', 'who_made', 'when_made', 'shipping_profile_id', 'readiness_state_id',
            'vendor_price', 'inventory_sync_status', 'last_scrape_at', 'last_scrape_error',
        ]

    def _extras(self, obj):
        return reverb_listings.parse_extras(obj)

    def _etsy_extras(self, obj):
        return etsy_listings.parse_extras(obj)

    def _mydeal_extras(self, obj):
        from .mydeal import products as mydeal_products

        return mydeal_products.parse_extras(obj)

    def get_make(self, obj):
        return self._extras(obj).get('make') or obj.brand or ''

    def get_model(self, obj):
        return self._extras(obj).get('model') or ''

    def get_finish(self, obj):
        return self._extras(obj).get('finish') or ''

    def get_year(self, obj):
        return self._extras(obj).get('year') or ''

    def get_condition_uuid(self, obj):
        return self._extras(obj).get('condition_uuid') or ''

    def get_category_uuid(self, obj):
        return self._extras(obj).get('category_uuid') or obj.category or ''

    def get_currency(self, obj):
        return self._extras(obj).get('currency') or 'USD'

    def get_upc_does_not_apply(self, obj):
        return bool(self._extras(obj).get('upc_does_not_apply', False))

    def get_publish_status(self, obj):
        extras = self._extras(obj)
        if extras.get('publish_status') is not None or extras.get('marketplace') == 'reverb':
            return reverb_listings.normalize_publish_status(extras.get('publish_status'))
        etsy = self._etsy_extras(obj)
        if etsy:
            return etsy_listings.normalize_publish_status(etsy.get('publish_status'))
        return reverb_listings.normalize_publish_status(None)

    def get_free_shipping(self, obj):
        return reverb_listings.free_shipping_enabled(
            self._extras(obj).get('free_shipping')
        )

    def get_taxonomy_id(self, obj):
        return self._etsy_extras(obj).get('taxonomy_id') or obj.category or ''

    def get_who_made(self, obj):
        return self._etsy_extras(obj).get('who_made') or ''

    def get_when_made(self, obj):
        return self._etsy_extras(obj).get('when_made') or ''

    def get_shipping_profile_id(self, obj):
        return self._etsy_extras(obj).get('shipping_profile_id') or ''

    def get_readiness_state_id(self, obj):
        return self._etsy_extras(obj).get('readiness_state_id') or ''

    def get_tags(self, obj):
        return self._mydeal_extras(obj).get('tags') or ''

    def get_specifications(self, obj):
        return self._mydeal_extras(obj).get('specifications') or ''

    def get_condition(self, obj):
        return self._mydeal_extras(obj).get('condition') or 'New'

    def get_gtin(self, obj):
        return self._mydeal_extras(obj).get('gtin') or obj.barcode or ''

    def get_mpn(self, obj):
        return self._mydeal_extras(obj).get('mpn') or ''

    def get_weight(self, obj):
        return self._mydeal_extras(obj).get('weight') or ''

    def get_weight_unit(self, obj):
        return self._mydeal_extras(obj).get('weight_unit') or 'kg'

    def get_length(self, obj):
        return self._mydeal_extras(obj).get('length') or ''

    def get_height(self, obj):
        return self._mydeal_extras(obj).get('height') or ''

    def get_width(self, obj):
        return self._mydeal_extras(obj).get('width') or ''

    def get_dimension_unit(self, obj):
        return self._mydeal_extras(obj).get('dimension_unit') or 'cm'

    def get_shipping_cost_category(self, obj):
        return self._mydeal_extras(obj).get('shipping_cost_category') or 'Flat'

    def get_shipping_cost_standard(self, obj):
        return self._mydeal_extras(obj).get('shipping_cost_standard') or '0'

    def get_custom_freight_scheme_id(self, obj):
        return self._mydeal_extras(obj).get('custom_freight_scheme_id') or ''

    def get_is_direct_import(self, obj):
        return bool(self._mydeal_extras(obj).get('is_direct_import', False))

    def get_max_days_for_delivery(self, obj):
        return self._mydeal_extras(obj).get('max_days_for_delivery') or '10'

    def get_delivery_time(self, obj):
        return self._mydeal_extras(obj).get('delivery_time') or '5-10 business days'

    def get_has_48_hours_dispatch(self, obj):
        return bool(self._mydeal_extras(obj).get('has_48_hours_dispatch', False))

    @staticmethod
    def _format_price_multiplier(sale, vendor):
        """Sale ÷ vendor as ×N (e.g. 2× cost → ×2). Fallback when no tier is configured."""
        try:
            sale_f = float(sale or 0)
            vendor_f = float(vendor) if vendor is not None else None
        except (TypeError, ValueError):
            return None
        if vendor_f is None or vendor_f <= 0 or sale_f <= 0:
            return None
        return f'×{round(sale_f / vendor_f, 2):g}'

    def get_margin_pct(self, obj):
        """Legacy: (sale − vendor) / sale × 100. Prefer margin_display."""
        try:
            sale = float(obj.sale_price or 0)
            vendor = float(obj.vendor_price) if obj.vendor_price is not None else None
        except (TypeError, ValueError):
            return None
        if vendor is None or sale <= 0:
            return None
        return round(((sale - vendor) / sale) * 100, 1)

    def get_margin_display(self, obj):
        """Configured tier: direct → ×N, fixed → +$N, percentage → N%."""
        try:
            if obj.vendor_price is None:
                return None
            cost = float(obj.vendor_price)
            store = obj.store
            if store is None:
                return self._format_price_multiplier(obj.sale_price, obj.vendor_price)
            from stores.models import StoreVendorPriceSettings
            from stores.pricing_tiers import resolve_margin_tier_for_raw_cost

            ps = None
            # Prefer prefetched rows when list view select_related/prefetch is used.
            settings_qs = getattr(store, 'vendor_price_settings', None)
            if settings_qs is not None:
                rows = list(settings_qs.all()) if hasattr(settings_qs, 'all') else []
                if rows:
                    ps = rows[0]
            if ps is None:
                ps = StoreVendorPriceSettings.objects.filter(store=store).first()
            if not ps:
                return self._format_price_multiplier(obj.sale_price, obj.vendor_price)
            tier = resolve_margin_tier_for_raw_cost(ps, cost)
            if tier is None:
                return self._format_price_multiplier(obj.sale_price, obj.vendor_price)
            m_type = getattr(tier, 'margin_type', 'percentage') or 'percentage'
            val = float(tier.margin_percentage or 0)
            if m_type == 'direct':
                return f'×{val:g}'
            if m_type == 'fixed':
                return f'+${val:.2f}'
            return f'{val:g}%'
        except Exception:  # noqa: BLE001
            return self._format_price_multiplier(
                getattr(obj, 'sale_price', None),
                getattr(obj, 'vendor_price', None),
            )


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
    options = serializers.CharField(required=False, allow_blank=True, default='', max_length=500)
    option_1_name = serializers.CharField(required=False, allow_blank=True, default='', max_length=100)
    option_1_value = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    option_2_name = serializers.CharField(required=False, allow_blank=True, default='', max_length=100)
    option_2_value = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    option_3_name = serializers.CharField(required=False, allow_blank=True, default='', max_length=100)
    option_3_value = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    option_4_name = serializers.CharField(required=False, allow_blank=True, default='', max_length=100)
    option_4_value = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    variation_image_url = serializers.CharField(required=False, allow_blank=True, default='', max_length=1000)
    vendor_url = serializers.CharField(required=False, allow_blank=True, default='', max_length=1000)
    vendor_id = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    source_vendor_code = serializers.CharField(required=False, allow_blank=True, default='', max_length=50)
    vendor_name = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    marketplace_name = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    store_name = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    image_urls = serializers.CharField(required=False, allow_blank=True, default='')
    inventory = serializers.IntegerField(required=False, default=0)
    infinite_quantity = serializers.BooleanField(required=False, default=False)
    original_price = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=0)
    sale_price = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=0)
    action = serializers.ChoiceField(
        choices=['create', 'mapped'], required=False, default='create',
    )
    # Reverb-specific (ignored by Lasoo validator / mapper)
    make = serializers.CharField(required=False, allow_blank=True, default='')
    model = serializers.CharField(required=False, allow_blank=True, default='')
    finish = serializers.CharField(required=False, allow_blank=True, default='')
    year = serializers.CharField(required=False, allow_blank=True, default='')
    condition_uuid = serializers.CharField(required=False, allow_blank=True, default='')
    category_uuid = serializers.CharField(required=False, allow_blank=True, default='')
    currency = serializers.CharField(required=False, allow_blank=True, default='USD')
    upc_does_not_apply = serializers.BooleanField(required=False, default=False)
    publish_status = serializers.ChoiceField(
        choices=['draft', 'live'], required=False, default='draft',
    )
    free_shipping = serializers.BooleanField(required=False, default=True)
    # MyDeal / WMP optional ProductGroup fields (stored in external_data_object_json)
    tags = serializers.CharField(required=False, allow_blank=True, default='')
    specifications = serializers.CharField(required=False, allow_blank=True, default='')
    gtin = serializers.CharField(required=False, allow_blank=True, default='')
    mpn = serializers.CharField(required=False, allow_blank=True, default='')
    weight = serializers.CharField(required=False, allow_blank=True, default='')
    weight_unit = serializers.CharField(required=False, allow_blank=True, default='')
    length = serializers.CharField(required=False, allow_blank=True, default='')
    height = serializers.CharField(required=False, allow_blank=True, default='')
    width = serializers.CharField(required=False, allow_blank=True, default='')
    dimension_unit = serializers.CharField(required=False, allow_blank=True, default='')
    shipping_cost_category = serializers.CharField(required=False, allow_blank=True, default='')
    shipping_cost_standard = serializers.CharField(required=False, allow_blank=True, default='')
    custom_freight_scheme_id = serializers.CharField(required=False, allow_blank=True, default='')
    is_direct_import = serializers.BooleanField(required=False, default=False)
    max_days_for_delivery = serializers.CharField(required=False, allow_blank=True, default='')
    delivery_time = serializers.CharField(required=False, allow_blank=True, default='')
    has_48_hours_dispatch = serializers.BooleanField(required=False, default=False)
    condition = serializers.CharField(required=False, allow_blank=True, default='')
    # Etsy-specific (stored in external_data_object_json)
    taxonomy_id = serializers.CharField(required=False, allow_blank=True, default='')
    who_made = serializers.CharField(required=False, allow_blank=True, default='')
    when_made = serializers.CharField(required=False, allow_blank=True, default='')
    shipping_profile_id = serializers.CharField(required=False, allow_blank=True, default='')
    readiness_state_id = serializers.CharField(required=False, allow_blank=True, default='')


class OrderShipmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderShipment
        fields = [
            'id', 'tracking_number', 'carrier', 'tracking_url',
            'shipped_at', 'status', 'created_at',
        ]


class MarketplaceOrderSerializer(serializers.ModelSerializer):
    shipments = OrderShipmentSerializer(many=True, read_only=True)
    details = serializers.SerializerMethodField()
    related_tickets = serializers.SerializerMethodField()

    class Meta:
        model = MarketplaceOrder
        fields = [
            'id', 'store', 'external_order_key', 'invoice_number',
            'customer_info_json', 'line_items_json',
            'status', 'shipping_status', 'total_amount_cents',
            'environment', 'shipments', 'raw_response_json',
            'details', 'related_tickets', 'created_at', 'updated_at',
        ]

    def get_details(self, obj):
        from .order_service import build_order_details, enrich_order_line_items

        details = build_order_details(
            obj.raw_response_json,
            customer_info=obj.customer_info_json,
            line_items=obj.line_items_json,
            total_cents=obj.total_amount_cents,
        )
        return enrich_order_line_items(details, obj.store)

    def get_related_tickets(self, obj):
        """Tickets whose related_order_key matches this order's invoice/key."""
        by_key = self.context.get('tickets_by_order_key') or {}
        keys = []
        for candidate in (obj.invoice_number, obj.external_order_key):
            text = str(candidate or '').strip().lower()
            if text and text not in keys:
                keys.append(text)
        seen = set()
        out = []
        for key in keys:
            for ticket in by_key.get(key, []):
                tid = ticket.get('id')
                if tid and tid not in seen:
                    seen.add(tid)
                    out.append(ticket)
        return out


class TicketMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = TicketMessage
        fields = [
            'id', 'external_message_key', 'direction', 'body',
            'sender_name', 'sender_type', 'delivered_to_marketplace',
            'sent_at', 'created_at',
        ]


class SupportTicketSerializer(serializers.ModelSerializer):
    messages = TicketMessageSerializer(many=True, read_only=True)

    class Meta:
        model = SupportTicket
        fields = [
            'id', 'store', 'external_ticket_key', 'subject',
            'customer_name', 'customer_email', 'related_order_key',
            'status', 'unread_count', 'last_message_at',
            'last_customer_message_at', 'environment',
            'messages', 'created_at', 'updated_at',
        ]
