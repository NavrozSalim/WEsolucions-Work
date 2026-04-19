from rest_framework import serializers
from catalog.models import ProductMapping, CatalogActivityLog
from stores.models import StoreVendorPriceSettings
from stores.pricing_tiers import resolve_margin_tier_for_raw_cost


def _pricing_settings_for_product(store, vendor_id):
    """Prefer vendor-specific settings; else first row for store. Uses prefetch when present."""
    first = None
    for ps in store.vendor_price_settings.all():
        if first is None:
            first = ps
        if ps.vendor_id == vendor_id:
            return ps
    if first is not None:
        return first
    try:
        return StoreVendorPriceSettings.objects.get(store=store, vendor_id=vendor_id)
    except StoreVendorPriceSettings.DoesNotExist:
        return StoreVendorPriceSettings.objects.filter(store=store).first()


class CatalogActivityLogSerializer(serializers.ModelSerializer):
    user_email = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = CatalogActivityLog
        fields = ('id', 'action_type', 'message', 'metadata', 'created_at', 'user_email')

    def get_user_email(self, obj):
        if obj.user_id and obj.user:
            return getattr(obj.user, 'email', None) or str(obj.user_id)
        return None


class ProductMappingSerializer(serializers.ModelSerializer):
    sku = serializers.SerializerMethodField(read_only=True)
    vendor_sku = serializers.CharField(source='product.vendor_sku', read_only=True)
    vendor_url = serializers.SerializerMethodField(read_only=True)
    vendor_name = serializers.SerializerMethodField(read_only=True)
    vendor_price = serializers.SerializerMethodField(read_only=True)
    margin_display = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ProductMapping
        fields = [f.name for f in ProductMapping._meta.fields] + [
            'sku',
            'vendor_sku',
            'vendor_url',
            'vendor_name',
            'vendor_price',
            'margin_display',
        ]
        read_only_fields = (
            'store_price', 'store_stock', 'sync_status',
            'last_sync_time', 'last_scrape_time', 'scrape_error',
        )

    def get_vendor_url(self, obj):
        try:
            if not obj.product:
                return None
            u = obj.product.vendor_url
            return u if u else None
        except Exception:
            return None

    def get_vendor_name(self, obj):
        try:
            if not obj.product or not obj.product.vendor:
                return None
            return obj.product.vendor.name or obj.product.vendor.code
        except Exception:
            return None

    def get_sku(self, obj):
        return (
            obj.marketplace_child_sku
            or obj.marketplace_parent_sku
            or (obj.product.vendor_sku if obj.product else None)
        )

    def get_vendor_price(self, obj):
        try:
            price = getattr(obj, 'latest_vendor_price', None)
            if price is not None:
                return float(price)
            vp = obj.product.vendor_prices.order_by('-scraped_at').first()
            return float(vp.price) if vp and vp.price else None
        except Exception:
            return None

    def get_margin_display(self, obj):
        """
        Show the tier's own configured margin. ``percentage`` / ``direct`` /
        ``fixed`` all read their value straight from
        ``StorePriceRangeMargin.margin_percentage`` — the same number used by
        ``sync.tasks._apply_pricing``, so the catalog column and the priced
        ``store_price`` can never drift apart.
        """
        try:
            price = getattr(obj, 'latest_vendor_price', None)
            if price is None:
                vp = obj.product.vendor_prices.order_by('-scraped_at').first()
                if not vp or vp.price is None:
                    return None
                price = vp.price
            cost = float(price)
            ps = _pricing_settings_for_product(obj.store, obj.product.vendor_id)
            if not ps:
                return None
            tier = resolve_margin_tier_for_raw_cost(ps, cost)
            if tier is None:
                return '—'
            m_type = getattr(tier, 'margin_type', 'percentage') or 'percentage'
            val = float(tier.margin_percentage or 0)
            if m_type == 'direct':
                return f'×{val:g}'
            if m_type == 'fixed':
                return f'+${val:.2f}'
            return f'+{val:g}%'
        except Exception:
            return None

    def validate(self, attrs):
        """Enforce ``pack_qty / prep_fees / shipping_fees`` when the store's
        matched pricing tier is ``fixed`` — the flat-profit + pack-qty
        formula cannot be evaluated without them. Applied on PATCH/PUT of
        existing mappings; the CSV upload path enforces the same rule in
        ``catalog.services.import_catalog_upload``.
        """
        instance = getattr(self, 'instance', None)
        if instance is None:
            return attrs

        def _resolve(field):
            if field in attrs:
                return attrs[field]
            return getattr(instance, field, None)

        store = getattr(instance, 'store', None)
        product = getattr(instance, 'product', None)
        vendor_id = getattr(product, 'vendor_id', None)
        if not store or not vendor_id:
            return attrs

        ps = _pricing_settings_for_product(store, vendor_id)
        if not ps:
            return attrs

        vp = None
        if product is not None:
            vp = product.vendor_prices.order_by('-scraped_at').first()
        cost = float(vp.price) if vp and vp.price is not None else None
        if cost is None:
            return attrs

        tier = resolve_margin_tier_for_raw_cost(ps, cost)
        if tier is None or getattr(tier, 'margin_type', '') != 'fixed':
            return attrs

        errors = {}
        for field in ('pack_qty', 'prep_fees', 'shipping_fees'):
            if _resolve(field) in (None, ''):
                errors[field] = 'Required for fixed pricing tier.'
        if errors:
            raise serializers.ValidationError(errors)
        return attrs
