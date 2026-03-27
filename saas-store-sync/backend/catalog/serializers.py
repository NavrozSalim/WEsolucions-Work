from rest_framework import serializers
from catalog.models import ProductMapping
from stores.models import StoreVendorPriceSettings
from stores.pricing_excel import excel_margin_tier_percent
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


class ProductMappingSerializer(serializers.ModelSerializer):
    sku = serializers.SerializerMethodField(read_only=True)
    vendor_sku = serializers.CharField(source='product.vendor_sku', read_only=True)
    vendor_url = serializers.URLField(source='product.vendor_url', read_only=True)
    vendor_name = serializers.CharField(source='product.vendor.name', read_only=True)
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
        read_only_fields = ('store_price', 'store_stock', 'sync_status', 'last_sync_time')

    def get_sku(self, obj):
        return (
            obj.marketplace_child_sku
            or obj.marketplace_parent_sku
            or (obj.product.vendor_sku if obj.product else None)
        )

    def get_vendor_price(self, obj):
        try:
            vp = obj.product.vendor_prices.order_by('-scraped_at').first()
            return float(vp.price) if vp and vp.price else None
        except Exception:
            return None

    def get_margin_display(self, obj):
        """
        Align catalog Margin column with Excel: for percentage tiers, F = tier(D) where D = vendor+tax.
        For fixed tiers, show the fixed add-on. Fallback tier: em dash.
        """
        try:
            vp = obj.product.vendor_prices.order_by('-scraped_at').first()
            if not vp or vp.price is None:
                return None
            cost = float(vp.price)
            ps = _pricing_settings_for_product(obj.store, obj.product.vendor_id)
            if not ps:
                return None
            tax_pct = float(ps.purchase_tax_percentage or 0)
            cost_with_tax = cost * (1 + tax_pct / 100)
            tier = resolve_margin_tier_for_raw_cost(ps, cost)
            if tier is None:
                return '—'
            m_type = getattr(tier, 'margin_type', 'percentage') or 'percentage'
            if m_type == 'direct':
                mult = float(tier.margin_percentage or 0)
                return f'×{mult:g}'
            if m_type == 'fixed':
                amt = float(tier.margin_percentage or 0)
                return f'+${amt:.2f}'
            f_pct = excel_margin_tier_percent(cost_with_tax)
            return f'+{f_pct:.0f}%'
        except Exception:
            return None
