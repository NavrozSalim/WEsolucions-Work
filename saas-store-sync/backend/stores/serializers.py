from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from decimal import Decimal

from django.db import IntegrityError

from stores.models import (
    Store,
    StorePriceRange, StoreVendorPriceSettings, StorePriceRangeMargin,
    StoreVendorInventorySettings, StoreInventoryRangeMultiplier,
)


class StoreVendorPriceSettingsReadSerializer(serializers.ModelSerializer):
    vendor_code = serializers.CharField(source='vendor.code', read_only=True)
    vendor_name = serializers.CharField(source='vendor.name', read_only=True)
    range_margins = serializers.SerializerMethodField()

    class Meta:
        model = StoreVendorPriceSettings
        fields = [
            'id', 'vendor', 'vendor_code', 'vendor_name',
            'purchase_tax_percentage', 'marketplace_fees_percentage',
            'mydeal_rrp_margin_percentage',
            'kogan_price_margin_percentage',
            'multiplier', 'optional_fee', 'rounding_option', 'continuous_update',
            'range_margins',
        ]

    def get_range_margins(self, obj):
        return [
            {
                'id': str(m.id),
                'from_value': float(m.price_range.from_value),
                'to_value': float(m.price_range.to_value) if m.price_range.to_value is not None else None,
                'margin_type': getattr(m, 'margin_type', 'percentage') or 'percentage',
                'margin_percentage': float(m.margin_percentage),
            }
            for m in obj.range_margins.select_related('price_range').all()
        ]


class StoreVendorInventorySettingsReadSerializer(serializers.ModelSerializer):
    vendor_code = serializers.CharField(source='vendor.code', read_only=True)
    vendor_name = serializers.CharField(source='vendor.name', read_only=True)
    range_multipliers = serializers.SerializerMethodField()
    nora_inventory_file_name = serializers.SerializerMethodField()
    has_nora_inventory_file = serializers.SerializerMethodField()

    class Meta:
        model = StoreVendorInventorySettings
        fields = [
            'id', 'vendor', 'vendor_code', 'vendor_name',
            'rule_type', 'default_multiplier', 'default_value', 'zero_if_low',
            'range_multipliers',
            'nora_inventory_file_name', 'has_nora_inventory_file',
            'nora_inventory_uploaded_at',
        ]

    def get_range_multipliers(self, obj):
        return [
            {
                'id': str(r.id),
                'from_value': float(r.from_value),
                'to_value': float(r.to_value) if r.to_value is not None else None,
                'range_type': getattr(r, 'range_type', 'multiplier') or 'multiplier',
                'multiplier': float(r.multiplier),
                'fixed_value': r.fixed_value,
            }
            for r in obj.range_multipliers.all()
        ]

    def get_nora_inventory_file_name(self, obj):
        return (obj.nora_inventory_original_name or '').strip() or (
            obj.nora_inventory_file.name.rsplit('/', 1)[-1] if obj.nora_inventory_file else ''
        )

    def get_has_nora_inventory_file(self, obj):
        return bool(obj.nora_inventory_file)


class SyncScheduleInlineSerializer(serializers.Serializer):
    """Lightweight serializer for embedding schedule in store responses and accepting it on create."""
    enabled = serializers.BooleanField(default=False)
    schedule_type = serializers.ChoiceField(choices=[('crontab', 'Crontab'), ('interval', 'Interval')], default='crontab')
    crontab_hour = serializers.CharField(default='10', required=False)
    crontab_minute = serializers.CharField(default='0', required=False)
    crontab_day_of_week = serializers.CharField(default='*', required=False)
    interval_seconds = serializers.IntegerField(default=None, required=False, allow_null=True)
    timezone = serializers.CharField(default='UTC', required=False)


class StoreSerializer(serializers.ModelSerializer):
    marketplace_id = serializers.UUIDField(source='marketplace.id', read_only=True, allow_null=True)
    marketplace_name = serializers.CharField(source='marketplace.name', read_only=True, allow_null=True)
    marketplace_code = serializers.CharField(source='marketplace.code', read_only=True, allow_null=True)
    vendor_price_settings = StoreVendorPriceSettingsReadSerializer(many=True, read_only=True)
    vendor_inventory_settings = StoreVendorInventorySettingsReadSerializer(many=True, read_only=True)
    sync_schedule = serializers.SerializerMethodField()
    shopify_connected = serializers.SerializerMethodField()
    shopify_has_credentials = serializers.SerializerMethodField()

    class Meta:
        model = Store
        fields = [
            'id', 'name', 'region', 'management_mode', 'api_token', 'marketplace', 'marketplace_id', 'marketplace_name',
            'marketplace_code',
            'kogan_service_account_json', 'kogan_sheet_id', 'kogan_tab_name',
            'kogan_sku_column', 'kogan_stock_column', 'kogan_price_column', 'kogan_rrp_column', 'kogan_first_price_column',
            'mydeal_setup_method',
            'mydeal_environment',
            'mydeal_sandbox_base_url', 'mydeal_production_base_url',
            'mydeal_sandbox_client_id', 'mydeal_sandbox_client_secret',
            'mydeal_sandbox_seller_id', 'mydeal_sandbox_seller_token',
            'mydeal_production_client_id', 'mydeal_production_client_secret',
            'mydeal_production_seller_id', 'mydeal_production_seller_token',
            'lasoo_environment', 'lasoo_staging_base_url', 'lasoo_production_base_url',
            'lasoo_staging_auth_key', 'lasoo_production_auth_key',
            'bunnings_environment', 'bunnings_staging_base_url', 'bunnings_production_base_url',
            'bunnings_staging_shop_key', 'bunnings_production_shop_key',
            'shopify_enabled', 'shopify_shop_domain', 'shopify_location_id',
            'shopify_client_id', 'shopify_client_secret',
            'shopify_connected', 'shopify_has_credentials',
            'connection_status', 'last_validated_at',
            'is_active', 'created_at', 'updated_at',
            'vendor_price_settings', 'vendor_inventory_settings',
            'sync_schedule',
        ]
        extra_kwargs = {
            # Keep token hidden in responses; accept blank/missing at serializer layer.
            # Non-Kogan marketplaces are validated explicitly in create().
            'api_token': {'write_only': True, 'required': False, 'allow_blank': True},
            'kogan_service_account_json': {'write_only': True},
            'lasoo_staging_auth_key': {'write_only': True, 'required': False, 'allow_blank': True, 'allow_null': True},
            'lasoo_production_auth_key': {'write_only': True, 'required': False, 'allow_blank': True, 'allow_null': True},
            'bunnings_staging_shop_key': {'write_only': True, 'required': False, 'allow_blank': True, 'allow_null': True},
            'bunnings_production_shop_key': {'write_only': True, 'required': False, 'allow_blank': True, 'allow_null': True},
            'shopify_client_id': {'write_only': True, 'required': False, 'allow_blank': True, 'allow_null': True},
            'shopify_client_secret': {'write_only': True, 'required': False, 'allow_blank': True, 'allow_null': True},
            'mydeal_sandbox_client_id': {'write_only': True, 'required': False, 'allow_blank': True, 'allow_null': True},
            'mydeal_sandbox_client_secret': {'write_only': True, 'required': False, 'allow_blank': True, 'allow_null': True},
            'mydeal_sandbox_seller_id': {'write_only': True, 'required': False, 'allow_blank': True, 'allow_null': True},
            'mydeal_sandbox_seller_token': {'write_only': True, 'required': False, 'allow_blank': True, 'allow_null': True},
            'mydeal_production_client_id': {'write_only': True, 'required': False, 'allow_blank': True, 'allow_null': True},
            'mydeal_production_client_secret': {'write_only': True, 'required': False, 'allow_blank': True, 'allow_null': True},
            'mydeal_production_seller_id': {'write_only': True, 'required': False, 'allow_blank': True, 'allow_null': True},
            'mydeal_production_seller_token': {'write_only': True, 'required': False, 'allow_blank': True, 'allow_null': True},
            'marketplace': {'allow_null': True},
            'connection_status': {'read_only': True},
            'last_validated_at': {'read_only': True},
        }

    def get_shopify_connected(self, obj):
        return bool(
            getattr(obj, 'shopify_enabled', False)
            and (getattr(obj, 'shopify_shop_domain', None) or '').strip()
            and (getattr(obj, 'shopify_client_id', None) or '').strip()
            and (getattr(obj, 'shopify_client_secret', None) or '').strip()
        )

    def get_shopify_has_credentials(self, obj):
        return bool(
            (getattr(obj, 'shopify_client_id', None) or '').strip()
            and (getattr(obj, 'shopify_client_secret', None) or '').strip()
        )

    def _apply_shopify_fields(self, validated_data, *, instance=None, marketplace=None, management_mode=''):
        from listings.shopify.client import normalize_location_id, normalize_shop_domain
        from listings.shopify.orders import SHOPIFY_ORDER_MARKETPLACES
        from stores.credentials import marketplace_kind

        req = self.context['request'].data
        touched = any(
            key in req
            for key in (
                'shopify_enabled',
                'shopify_shop_domain',
                'shopify_client_id',
                'shopify_client_secret',
                'shopify_location_id',
            )
        )
        if not touched and instance is None:
            validated_data['shopify_enabled'] = False
            return
        if not touched:
            return

        enabled_raw = req.get('shopify_enabled', validated_data.get('shopify_enabled'))
        if enabled_raw is None and instance is not None:
            enabled = bool(instance.shopify_enabled)
        elif isinstance(enabled_raw, str):
            enabled = enabled_raw.strip().lower() in ('1', 'true', 'yes', 'on')
        else:
            enabled = bool(enabled_raw)

        kind = marketplace_kind(marketplace)
        if enabled:
            if (management_mode or '') != 'full_store':
                raise ValidationError({
                    'shopify_enabled': 'Shopify order sync is only available for managed stores.',
                })
            if kind not in SHOPIFY_ORDER_MARKETPLACES:
                raise ValidationError({
                    'shopify_enabled': 'Shopify order sync is only available for Reverb, Lasoo, MyDeal, Etsy, and Bunnings.',
                })

        if 'shopify_shop_domain' in req:
            domain = normalize_shop_domain(req.get('shopify_shop_domain') or '')
            validated_data['shopify_shop_domain'] = domain
        else:
            domain = normalize_shop_domain(
                validated_data.get('shopify_shop_domain')
                or (instance.shopify_shop_domain if instance else '')
                or ''
            )

        if 'shopify_location_id' in req:
            validated_data['shopify_location_id'] = normalize_location_id(req.get('shopify_location_id') or '')

        incoming_id = (req.get('shopify_client_id') or '').strip()
        incoming_secret = (req.get('shopify_client_secret') or '').strip()
        if incoming_id:
            validated_data['shopify_client_id'] = incoming_id
        elif instance is None:
            validated_data.pop('shopify_client_id', None)
        else:
            validated_data.pop('shopify_client_id', None)
        if incoming_secret:
            validated_data['shopify_client_secret'] = incoming_secret
        else:
            validated_data.pop('shopify_client_secret', None)

        existing_id = incoming_id or ((instance.shopify_client_id or '').strip() if instance else '')
        existing_secret = incoming_secret or ((instance.shopify_client_secret or '').strip() if instance else '')
        if enabled:
            if not domain.endswith('.myshopify.com'):
                raise ValidationError({
                    'shopify_shop_domain': 'Enter the shop .myshopify.com domain (not the public website).',
                })
            if not existing_id:
                raise ValidationError({
                    'shopify_client_id': 'Shopify Client ID is required when Connect Shopify is on.',
                })
            if not existing_secret:
                raise ValidationError({
                    'shopify_client_secret': 'Shopify Client secret is required when Connect Shopify is on.',
                })
        validated_data['shopify_enabled'] = enabled
        if incoming_id or incoming_secret:
            validated_data['shopify_access_token'] = ''
            validated_data['shopify_token_expires_at'] = None

    def get_sync_schedule(self, obj):
        try:
            sched = obj.sync_schedule
        except Exception:
            return None
        return {
            'id': str(sched.id),
            'enabled': sched.is_active,
            'schedule_type': sched.schedule_type,
            'crontab_hour': sched.crontab_hour,
            'crontab_minute': sched.crontab_minute,
            'crontab_day_of_week': sched.crontab_day_of_week,
            'interval_seconds': sched.interval_seconds,
            'timezone': sched.timezone,
            'last_run': sched.last_run.isoformat() if sched.last_run else None,
        }

    def create(self, validated_data):
        from vendor.models import Vendor
        from marketplace.models import Marketplace
        from sync.models import SyncSchedule
        user = self.context['request'].user
        req = self.context['request'].data
        marketplace_id = req.get('marketplace_id') or req.get('marketplace')
        mkt = None
        if marketplace_id:
            try:
                mkt = Marketplace.objects.get(id=marketplace_id)
                validated_data['marketplace'] = mkt
            except Marketplace.DoesNotExist:
                pass
        from stores.credentials import (
            requires_structured_credentials,
            validate_api_token_shape,
            verify_store_connection,
        )

        is_kogan = bool(mkt and (str(mkt.code or '').strip().lower() == 'kogan' or str(mkt.name or '').strip().lower() == 'kogan'))
        is_mydeal = bool(mkt and (str(mkt.code or '').strip().lower() == 'mydeal' or str(mkt.name or '').strip().lower() == 'mydeal'))
        is_lasoo = bool(mkt and (str(mkt.code or '').strip().lower() == 'lasoo' or str(mkt.name or '').strip().lower() == 'lasoo'))
        is_bunnings = bool(mkt and (str(mkt.code or '').strip().lower() == 'bunnings' or str(mkt.name or '').strip().lower() == 'bunnings'))
        is_structured = bool(mkt and requires_structured_credentials(mkt))
        management_mode = (req.get('management_mode') or validated_data.get('management_mode') or 'inventory_only').strip()
        if management_mode not in ('inventory_only', 'full_store'):
            raise ValidationError({'management_mode': 'Management mode must be inventory_only or full_store.'})
        validated_data['management_mode'] = management_mode
        if management_mode == 'full_store':
            from stores.credentials import marketplace_kind
            kind = marketplace_kind(mkt)
            if kind not in ('reverb', 'lasoo', 'mydeal', 'etsy', 'bunnings'):
                raise ValidationError({
                    'marketplace': 'Managed stores are only available for Reverb, Lasoo, MyDeal, Etsy, and Bunnings right now.',
                })
            if kind == 'mydeal':
                method = (req.get('mydeal_setup_method') or validated_data.get('mydeal_setup_method') or 'upload').strip()
                if method != 'api':
                    raise ValidationError({
                        'mydeal_setup_method': 'MyDeal managed store requires API connection (not upload templates).',
                    })
        self._apply_shopify_fields(
            validated_data,
            marketplace=mkt,
            management_mode=management_mode,
        )
        if is_lasoo:
            # Lasoo uses per-environment AuthKeys, not Store.api_token.
            staging_key = (req.get('lasoo_staging_auth_key') or '').strip()
            if not staging_key:
                raise ValidationError({'lasoo_staging_auth_key': 'Lasoo staging AuthKey is required.'})
            validated_data.setdefault('api_token', '')
        if is_bunnings:
            from listings.bunnings.client import DEFAULT_PRODUCTION_BASE_URL

            env = (req.get('bunnings_environment') or validated_data.get('bunnings_environment') or 'production').strip()
            if env not in ('staging', 'production'):
                env = 'production'
            validated_data['bunnings_environment'] = env
            if env == 'production':
                base_url = (
                    (req.get('bunnings_production_base_url') or validated_data.get('bunnings_production_base_url') or '')
                    .strip()
                    or DEFAULT_PRODUCTION_BASE_URL
                )
                shop_key = (req.get('bunnings_production_shop_key') or validated_data.get('bunnings_production_shop_key') or '').strip()
                if not shop_key:
                    raise ValidationError({'bunnings_production_shop_key': 'Bunnings production SHOP_KEY is required.'})
                validated_data['bunnings_production_base_url'] = base_url
                validated_data['bunnings_production_shop_key'] = shop_key
            else:
                base_url = (req.get('bunnings_staging_base_url') or validated_data.get('bunnings_staging_base_url') or '').strip()
                shop_key = (req.get('bunnings_staging_shop_key') or validated_data.get('bunnings_staging_shop_key') or '').strip()
                if not base_url:
                    raise ValidationError({'bunnings_staging_base_url': 'Bunnings staging base URL is required.'})
                if not shop_key:
                    raise ValidationError({'bunnings_staging_shop_key': 'Bunnings staging SHOP_KEY is required.'})
                validated_data['bunnings_staging_base_url'] = base_url
                validated_data['bunnings_staging_shop_key'] = shop_key
            validated_data.setdefault('api_token', '')
        if is_mydeal:
            method = (req.get('mydeal_setup_method') or 'upload').strip()
            if method not in ('upload', 'api'):
                raise ValidationError({'mydeal_setup_method': 'Mydeal setup must be upload or api.'})
            validated_data['mydeal_setup_method'] = method
            if method == 'api':
                env = (req.get('mydeal_environment') or validated_data.get('mydeal_environment') or 'sandbox').strip()
                if env not in ('sandbox', 'production'):
                    env = 'sandbox'
                validated_data['mydeal_environment'] = env
                prefix = 'mydeal_sandbox' if env == 'sandbox' else 'mydeal_production'
                required = {
                    f'{prefix}_base_url': 'base URL',
                    f'{prefix}_client_id': 'ClientID',
                    f'{prefix}_client_secret': 'ClientSecret',
                    f'{prefix}_seller_id': 'SellerID',
                    f'{prefix}_seller_token': 'SellerToken',
                }
                for field, label in required.items():
                    val = (req.get(field) or validated_data.get(field) or '').strip()
                    if not val:
                        raise ValidationError({field: f'MyDeal {env} {label} is required for API connection.'})
                    validated_data[field] = val
            validated_data.setdefault('api_token', '')
        if is_kogan:
            # Kogan uses Google Sheets service account JSON + spreadsheet details, not Store.api_token.
            if not (req.get('kogan_sheet_id') or '').strip():
                raise ValidationError({'kogan_sheet_id': 'Spreadsheet ID is required for Kogan.'})
            if not (req.get('kogan_tab_name') or '').strip():
                raise ValidationError({'kogan_tab_name': 'Tab name is required for Kogan.'})
            # Accept either explicit kogan JSON field or legacy api_token containing JSON.
            has_json = bool((req.get('kogan_service_account_json') or '').strip() or (validated_data.get('kogan_service_account_json') or '').strip())
            if not has_json and not (req.get('api_token') or '').strip():
                raise ValidationError({'kogan_service_account_json': 'Upload service account JSON for Kogan (or paste it into API token).'})
        elif is_mydeal or is_lasoo or is_bunnings:
            pass
        else:
            token_raw = (req.get('api_token') or '').strip()
            if not token_raw:
                raise ValidationError({'api_token': 'API key is required.'})
            if is_structured:
                validated_data['api_token'] = validate_api_token_shape(mkt, token_raw)
        sched_raw = req.get('sync_schedule')
        if not sched_raw or not isinstance(sched_raw, dict) or not sched_raw.get('enabled', False):
            raise ValidationError({'sync_schedule': 'Scheduled updates are required. Choose frequency and time.'})

        price_settings_data = req.get('vendor_price_settings', [])
        inventory_settings_data = req.get('vendor_inventory_settings', [])
        store_data = {
            k: v
            for k, v in validated_data.items()
            if k in (
                'name',
                'region',
                'management_mode',
                'api_token',
                'marketplace',
                'is_active',
                # Kogan
                'kogan_service_account_json',
                'kogan_sheet_id',
                'kogan_tab_name',
                'kogan_sku_column',
                'kogan_stock_column',
                'kogan_price_column',
                'kogan_rrp_column',
                'kogan_first_price_column',
                'mydeal_setup_method',
                'mydeal_environment',
                'mydeal_sandbox_base_url',
                'mydeal_production_base_url',
                'mydeal_sandbox_client_id',
                'mydeal_sandbox_client_secret',
                'mydeal_sandbox_seller_id',
                'mydeal_sandbox_seller_token',
                'mydeal_production_client_id',
                'mydeal_production_client_secret',
                'mydeal_production_seller_id',
                'mydeal_production_seller_token',
                # Lasoo (managed stores)
                'lasoo_environment',
                'lasoo_staging_base_url',
                'lasoo_production_base_url',
                'lasoo_staging_auth_key',
                'lasoo_production_auth_key',
                # Bunnings (managed stores)
                'bunnings_environment',
                'bunnings_staging_base_url',
                'bunnings_production_base_url',
                'bunnings_staging_shop_key',
                'bunnings_production_shop_key',
                'shopify_enabled',
                'shopify_shop_domain',
                'shopify_client_id',
                'shopify_client_secret',
                'shopify_location_id',
                'shopify_access_token',
                'shopify_token_expires_at',
            )
        }
        if store_data.get('name'):
            store_data['name'] = store_data['name'].strip()
        if mkt and Store.objects.filter(
            user=user, name=store_data.get('name', ''), marketplace=mkt, orphaned_at__isnull=True,
        ).exists():
            raise ValidationError({
                'name': f'A store named "{store_data.get("name")}" already exists for this marketplace.',
            })

        from stores.orphan import (
            find_orphaned_store,
            fingerprint_from_create_payload,
            reclaim_store,
            re_orphan_store,
        )

        fingerprint = fingerprint_from_create_payload(marketplace=mkt, store_data=store_data)
        orphan = find_orphaned_store(marketplace=mkt, fingerprint=fingerprint)
        if orphan is not None:
            store = reclaim_store(orphan, user, store_data=store_data)
            self._validate_inventory_covers_price_vendors(price_settings_data, inventory_settings_data)
            self._save_vendor_price_settings(store, price_settings_data, Vendor)
            self._save_vendor_inventory_settings(store, inventory_settings_data, Vendor)
            self._save_sync_schedule(store, req.get('sync_schedule'), SyncSchedule)
            if is_structured or (is_mydeal and store.mydeal_setup_method == 'api') or is_lasoo or is_bunnings:
                ok, err_msg = verify_store_connection(store)
                if not ok:
                    re_orphan_store(store)
                    if is_mydeal:
                        field = 'mydeal_setup_method'
                    elif is_bunnings:
                        field = 'bunnings_production_shop_key' if (store.bunnings_environment or 'production') == 'production' else 'bunnings_staging_shop_key'
                    elif is_lasoo:
                        field = 'lasoo_staging_auth_key'
                    else:
                        field = 'api_token'
                    raise ValidationError({
                        field: err_msg or 'Marketplace rejected these credentials.',
                    })
            return store

        try:
            store = Store.objects.create(user=user, **store_data)
        except IntegrityError as exc:
            if 'uq_store_user_name_marketplace' in str(exc) or 'UNIQUE constraint failed' in str(exc):
                raise ValidationError({
                    'name': 'A store with this name and marketplace already exists.',
                }) from None
            raise
        self._validate_inventory_covers_price_vendors(price_settings_data, inventory_settings_data)
        self._save_vendor_price_settings(store, price_settings_data, Vendor)
        self._save_vendor_inventory_settings(store, inventory_settings_data, Vendor)
        self._save_sync_schedule(store, req.get('sync_schedule'), SyncSchedule)

        if is_structured or (is_mydeal and store.mydeal_setup_method == 'api') or is_lasoo or is_bunnings:
            ok, err_msg = verify_store_connection(store)
            if not ok:
                store.delete()
                if is_mydeal:
                    field = 'mydeal_setup_method'
                elif is_bunnings:
                    field = 'bunnings_production_shop_key' if (store.bunnings_environment or 'production') == 'production' else 'bunnings_staging_shop_key'
                elif is_lasoo:
                    field = 'lasoo_staging_auth_key'
                else:
                    field = 'api_token'
                raise ValidationError({
                    field: err_msg or 'Marketplace rejected these credentials.',
                })

        return store

    def update(self, instance, validated_data):
        from vendor.models import Vendor
        from marketplace.models import Marketplace
        from sync.models import SyncSchedule
        req = self.context['request'].data
        MYDEAL_SECRET_FIELDS = (
            'mydeal_sandbox_client_id',
            'mydeal_sandbox_client_secret',
            'mydeal_sandbox_seller_id',
            'mydeal_sandbox_seller_token',
            'mydeal_production_client_id',
            'mydeal_production_client_secret',
            'mydeal_production_seller_id',
            'mydeal_production_seller_token',
        )
        BUNNINGS_SECRET_FIELDS = (
            'bunnings_staging_shop_key',
            'bunnings_production_shop_key',
        )
        self._apply_shopify_fields(
            validated_data,
            instance=instance,
            marketplace=instance.marketplace,
            management_mode=req.get('management_mode') or instance.management_mode,
        )
        for attr, value in validated_data.items():
            if attr in (
                'name',
                'region',
                'management_mode',
                'api_token',
                'marketplace',
                'is_active',
                # Kogan
                'kogan_service_account_json',
                'kogan_sheet_id',
                'kogan_tab_name',
                'kogan_sku_column',
                'kogan_stock_column',
                'kogan_price_column',
                'kogan_rrp_column',
                'kogan_first_price_column',
                'mydeal_setup_method',
                'mydeal_environment',
                'mydeal_sandbox_base_url',
                'mydeal_production_base_url',
                *MYDEAL_SECRET_FIELDS,
                # Lasoo (managed stores)
                'lasoo_environment',
                'lasoo_staging_base_url',
                'lasoo_production_base_url',
                'lasoo_staging_auth_key',
                'lasoo_production_auth_key',
                # Bunnings (managed stores)
                'bunnings_environment',
                'bunnings_staging_base_url',
                'bunnings_production_base_url',
                'bunnings_staging_shop_key',
                'bunnings_production_shop_key',
                'shopify_enabled',
                'shopify_shop_domain',
                'shopify_client_id',
                'shopify_client_secret',
                'shopify_location_id',
                'shopify_access_token',
                'shopify_token_expires_at',
            ):
                if attr in (
                    'lasoo_staging_auth_key',
                    'lasoo_production_auth_key',
                    'shopify_client_id',
                    'shopify_client_secret',
                    *MYDEAL_SECRET_FIELDS,
                    *BUNNINGS_SECRET_FIELDS,
                ) and not (value or '').strip() and attr != 'shopify_access_token':
                    # Blank secret in a PATCH means "keep the existing value".
                    continue
                setattr(instance, attr, value)
        marketplace_id = req.get('marketplace_id') or req.get('marketplace')
        if marketplace_id is not None:
            try:
                mkt = Marketplace.objects.get(id=marketplace_id) if marketplace_id else None
                instance.marketplace = mkt
            except Marketplace.DoesNotExist:
                pass
        from stores.credentials import requires_structured_credentials, validate_api_token_shape, verify_store_connection

        mkt_now = instance.marketplace
        is_kogan = bool(mkt_now and (str(mkt_now.code or '').strip().lower() == 'kogan' or str(mkt_now.name or '').strip().lower() == 'kogan'))
        is_mydeal = bool(mkt_now and (str(mkt_now.code or '').strip().lower() == 'mydeal' or str(mkt_now.name or '').strip().lower() == 'mydeal'))
        is_bunnings = bool(mkt_now and (str(mkt_now.code or '').strip().lower() == 'bunnings' or str(mkt_now.name or '').strip().lower() == 'bunnings'))
        is_structured = bool(mkt_now and requires_structured_credentials(mkt_now))
        token_in_request = 'api_token' in validated_data or bool((req.get('api_token') or '').strip())
        if is_mydeal and 'mydeal_setup_method' in req:
            method = (req.get('mydeal_setup_method') or '').strip()
            if method and method not in ('upload', 'api'):
                raise ValidationError({'mydeal_setup_method': 'Mydeal setup must be upload or api.'})
            if method == 'api':
                env = (req.get('mydeal_environment') or instance.mydeal_environment or 'sandbox').strip()
                if env not in ('sandbox', 'production'):
                    env = 'sandbox'
                instance.mydeal_environment = env
                prefix = 'mydeal_sandbox' if env == 'sandbox' else 'mydeal_production'
                base_url = (req.get(f'{prefix}_base_url') or getattr(instance, f'{prefix}_base_url', '') or '').strip()
                if not base_url:
                    raise ValidationError({f'{prefix}_base_url': f'MyDeal {env} base URL is required for API connection.'})
                for suffix, label in (
                    ('client_id', 'ClientID'),
                    ('client_secret', 'ClientSecret'),
                    ('seller_id', 'SellerID'),
                    ('seller_token', 'SellerToken'),
                ):
                    field = f'{prefix}_{suffix}'
                    incoming = (req.get(field) or '').strip()
                    existing = (getattr(instance, field, None) or '').strip()
                    if not incoming and not existing:
                        raise ValidationError({field: f'MyDeal {env} {label} is required for API connection.'})
            if method:
                instance.mydeal_setup_method = method
            if method == 'api' and (instance.management_mode or '') == 'full_store':
                pass  # allowed
            if method == 'upload' and (req.get('management_mode') or instance.management_mode) == 'full_store':
                raise ValidationError({
                    'mydeal_setup_method': 'MyDeal managed store requires API connection (not upload templates).',
                })
        if is_kogan:
            if 'kogan_sheet_id' in req and not (req.get('kogan_sheet_id') or '').strip():
                raise ValidationError({'kogan_sheet_id': 'Spreadsheet ID is required for Kogan.'})
            if 'kogan_tab_name' in req and not (req.get('kogan_tab_name') or '').strip():
                raise ValidationError({'kogan_tab_name': 'Tab name is required for Kogan.'})
        verify_new_credentials = False
        if is_bunnings and any(k in req for k in ('bunnings_environment', 'bunnings_staging_base_url', 'bunnings_production_base_url', *BUNNINGS_SECRET_FIELDS)):
            verify_new_credentials = True
        if is_structured and token_in_request:
            token_raw = (validated_data.get('api_token') or req.get('api_token') or '').strip()
            if token_raw:
                verify_new_credentials = True
                normalized = validate_api_token_shape(mkt_now, token_raw)
                instance.api_token = normalized
                validated_data['api_token'] = normalized
        if instance.name:
            instance.name = instance.name.strip()
        if Store.objects.filter(
            user=instance.user, name=instance.name, marketplace=instance.marketplace,
        ).exclude(pk=instance.pk).exists():
            raise ValidationError({
                'name': f'A store named "{instance.name}" already exists for this marketplace.',
            })

        def _persist_store_changes():
            try:
                instance.save()
            except IntegrityError as exc:
                if 'uq_store_user_name_marketplace' in str(exc) or 'UNIQUE constraint failed' in str(exc):
                    raise ValidationError({
                        'name': 'A store with this name and marketplace already exists.',
                    }) from None
                raise
            price_in = 'vendor_price_settings' in req
            inv_in = 'vendor_inventory_settings' in req
            if price_in and inv_in:
                self._validate_inventory_covers_price_vendors(
                    req['vendor_price_settings'], req['vendor_inventory_settings'],
                )
            elif inv_in and not price_in:
                price_snapshot = [
                    {'vendor_id': str(x.vendor_id)}
                    for x in instance.vendor_price_settings.all()
                ]
                self._validate_inventory_covers_price_vendors(price_snapshot, req['vendor_inventory_settings'])
            elif price_in and not inv_in:
                inv_snapshot = [
                    {'vendor_id': str(x.vendor_id)}
                    for x in instance.vendor_inventory_settings.all()
                ]
                self._validate_inventory_covers_price_vendors(req['vendor_price_settings'], inv_snapshot)

            if price_in:
                self._save_vendor_price_settings(instance, req['vendor_price_settings'], Vendor)
            if inv_in:
                self._save_vendor_inventory_settings(instance, req['vendor_inventory_settings'], Vendor)
            if 'sync_schedule' in req:
                self._save_sync_schedule(instance, req['sync_schedule'], SyncSchedule)

        if verify_new_credentials:
            from django.db import transaction

            with transaction.atomic():
                _persist_store_changes()
                ok, err_msg = verify_store_connection(instance)
                if not ok:
                    if is_bunnings:
                        env = instance.bunnings_environment or 'production'
                        field = 'bunnings_production_shop_key' if env == 'production' else 'bunnings_staging_shop_key'
                    else:
                        field = 'api_token'
                    raise ValidationError({
                        field: err_msg or 'Marketplace rejected these credentials.',
                    })
        else:
            _persist_store_changes()

        return instance

    @staticmethod
    def _clamp_non_negative(val, default=0, as_type=Decimal):
        try:
            v = as_type(str(val))
        except Exception:
            return as_type(str(default))
        return max(v, as_type(str(0)))

    _PRICE_TIER_MAX = Decimal('999999999')
    _PRICE_TIER_EPS = Decimal('0.000001')

    def _validate_price_settings_payload(self, data):
        """Match frontend priceRangeValidation: continuous tiers, last To = 999999999."""
        if not isinstance(data, list):
            return
        _c = self._clamp_non_negative
        max_v = self._PRICE_TIER_MAX
        eps = self._PRICE_TIER_EPS

        for item in data:
            vendor_id = item.get('vendor_id') or item.get('vendor')
            if not vendor_id:
                continue
            ranges = item.get('range_margins') or []
            if not ranges:
                raise ValidationError({'vendor_price_settings': 'Each vendor needs at least one price tier.'})

            from_vals = []
            to_vals = []

            for ri, r in enumerate(ranges):
                from_v = _c(r.get('from_value', 0) or 0)
                to_raw = r.get('to_value')
                try:
                    if to_raw in (None, '', 'MAX') or str(to_raw).strip().upper() == 'MAX':
                        to_dec = None
                    else:
                        to_dec = Decimal(str(to_raw))
                        to_dec = max(to_dec, Decimal('0'))
                except Exception:
                    to_dec = None

                margin = _c(r.get('margin_percentage', 0) or 0)

                if from_v < 0:
                    raise ValidationError({'vendor_price_settings': f'Price tier {ri + 1}: "From" must be non-negative.'})
                if to_dec is not None and to_dec < 0:
                    raise ValidationError({'vendor_price_settings': f'Price tier {ri + 1}: "To" must be non-negative.'})
                if to_dec is not None and from_v > to_dec:
                    raise ValidationError({'vendor_price_settings': f'Price tier {ri + 1}: "From" cannot be greater than "To".'})
                if margin < 0:
                    raise ValidationError({'vendor_price_settings': f'Price tier {ri + 1}: Margin must be zero or greater.'})

                from_vals.append(from_v)
                to_vals.append(to_dec)

            for i in range(len(ranges) - 1):
                if to_vals[i] is None:
                    raise ValidationError({
                        'vendor_price_settings': (
                            f'Price tiers must be continuous: tier {i + 1} needs a maximum before starting tier {i + 2}.'
                        ),
                    })

            for i in range(1, len(ranges)):
                prev_to = to_vals[i - 1]
                curr_from = from_vals[i]
                if prev_to is not None and curr_from is not None and abs(curr_from - prev_to) > eps:
                    raise ValidationError({
                        'vendor_price_settings': (
                            f'Price ranges must be continuous: after a tier ending at {prev_to}, '
                            f'the next tier must start at {prev_to} (not {curr_from}).'
                        ),
                    })

            last_to = to_vals[-1]
            if last_to is None or abs(last_to - max_v) > eps:
                raise ValidationError({
                    'vendor_price_settings': f'The last price tier "To" must be {max_v}.',
                })

    @staticmethod
    def _validate_inventory_covers_price_vendors(price_data, inventory_data):
        """Every vendor with price settings must have inventory settings (same request or DB)."""
        if not isinstance(price_data, list):
            price_data = []
        if not isinstance(inventory_data, list):
            inventory_data = []
        price_vendors = set()
        for item in price_data:
            vid = item.get('vendor_id') or item.get('vendor')
            if vid:
                price_vendors.add(str(vid))
        inv_vendors = set()
        for item in inventory_data:
            vid = item.get('vendor_id') or item.get('vendor')
            if vid:
                inv_vendors.add(str(vid))
        missing = price_vendors - inv_vendors
        if not missing:
            return
        raise ValidationError({
            'vendor_inventory_settings': (
                'Each vendor configured under Price must also have Inventory ranges. '
                f'Missing vendor id(s): {", ".join(sorted(missing))}.'
            ),
        })

    def _save_vendor_price_settings(self, store, data, Vendor):
        if not isinstance(data, list):
            return
        self._validate_price_settings_payload(data)
        _c = self._clamp_non_negative
        StoreVendorPriceSettings.objects.filter(store=store).delete()
        for item in data:
            vendor_id = item.get('vendor_id') or item.get('vendor')
            if not vendor_id:
                continue
            try:
                vendor = Vendor.objects.get(id=vendor_id)
            except Vendor.DoesNotExist:
                continue
            rrp_margin = item.get('mydeal_rrp_margin_percentage')
            rrp_margin_dec = None
            if rrp_margin not in (None, ''):
                rrp_margin_dec = _c(rrp_margin, default=0)
            kogan_price_margin = item.get('kogan_price_margin_percentage')
            kogan_price_margin_dec = None
            if kogan_price_margin not in (None, ''):
                kogan_price_margin_dec = _c(kogan_price_margin, default=0)
            ps = StoreVendorPriceSettings.objects.create(
                store=store, vendor=vendor,
                purchase_tax_percentage=_c(item.get('purchase_tax_percentage', 0) or 0),
                marketplace_fees_percentage=_c(item.get('marketplace_fees_percentage', 0) or 0),
                mydeal_rrp_margin_percentage=rrp_margin_dec,
                kogan_price_margin_percentage=kogan_price_margin_dec,
                multiplier=max(0.0, float(item.get('multiplier', 1) or 1)),
                optional_fee=max(0.0, float(item.get('optional_fee', 0) or 0)),
                rounding_option=str(item.get('rounding_option', 'none') or 'none'),
                continuous_update=bool(item.get('continuous_update')),
            )
            for rm in item.get('range_margins', []):
                to_val = rm.get('to_value')
                try:
                    to_value = Decimal(str(to_val)) if to_val not in (None, '', 'MAX') and str(to_val).upper() != 'MAX' else None
                except Exception:
                    to_value = None
                from_val = _c(rm.get('from_value', 0) or 0)
                if to_value is not None:
                    to_value = max(to_value, Decimal('0'))
                pr = StorePriceRange.objects.create(
                    from_value=from_val,
                    to_value=to_value,
                )
                margin_type = str(rm.get('margin_type', 'percentage') or 'percentage')
                if margin_type not in ('percentage', 'fixed', 'direct'):
                    margin_type = 'percentage'
                margin_val = _c(rm.get('margin_percentage', 0) or 0)
                StorePriceRangeMargin.objects.create(
                    price_settings=ps, price_range=pr,
                    margin_type=margin_type,
                    margin_percentage=margin_val,
                    minimum_margin_cents=0,
                    dont_pay_discount_percentage=None,
                )

    def _save_vendor_inventory_settings(self, store, data, Vendor):
        if not isinstance(data, list):
            return
        _c = self._clamp_non_negative
        valid_items = [i for i in data if (i.get('vendor_id') or i.get('vendor')) and (i.get('range_multipliers') or [])]
        if data and not valid_items:
            raise ValidationError({'vendor_inventory_settings': 'Add at least one vendor with inventory ranges (multiplier or fixed value).'})
        # Preserve Nora Excel files across delete/recreate of inventory settings.
        existing_nora = {}
        for old in StoreVendorInventorySettings.objects.filter(store=store).select_related('vendor'):
            if old.nora_inventory_file:
                existing_nora[str(old.vendor_id)] = {
                    'file': old.nora_inventory_file.name,
                    'uploaded_at': old.nora_inventory_uploaded_at,
                    'original_name': old.nora_inventory_original_name or '',
                }
        StoreVendorInventorySettings.objects.filter(store=store).delete()
        for item in data:
            vendor_id = item.get('vendor_id') or item.get('vendor')
            if not vendor_id:
                continue
            try:
                vendor = Vendor.objects.get(id=vendor_id)
            except Vendor.DoesNotExist:
                continue
            inv = StoreVendorInventorySettings.objects.create(
                store=store, vendor=vendor,
                rule_type=str(item.get('rule_type', 'multiplier') or 'multiplier'),
                default_multiplier=_c(item.get('default_multiplier', 1) or 1),
                default_value=max(0, int(item.get('default_value', 1) or 1)),
                zero_if_low=item.get('zero_if_low', True) if item.get('zero_if_low') is not False else False,
            )
            preserved = existing_nora.get(str(vendor.id))
            if preserved and preserved.get('file'):
                inv.nora_inventory_file.name = preserved['file']
                inv.nora_inventory_uploaded_at = preserved.get('uploaded_at')
                inv.nora_inventory_original_name = preserved.get('original_name') or ''
                inv.save(update_fields=[
                    'nora_inventory_file',
                    'nora_inventory_uploaded_at',
                    'nora_inventory_original_name',
                ])
            for rm in item.get('range_multipliers', []):
                to_val = rm.get('to_value')
                try:
                    to_value = Decimal(str(to_val)) if to_val not in (None, '', 'MAX') and str(to_val).upper() != 'MAX' else None
                except Exception:
                    to_value = None
                from_val = _c(rm.get('from_value', 0) or 0)
                if to_value is not None:
                    to_value = max(to_value, Decimal('0'))
                range_type = str(rm.get('range_type', 'multiplier') or 'multiplier')
                fixed_val = rm.get('fixed_value')
                if fixed_val is not None and fixed_val != '':
                    try:
                        fixed_val = max(0, int(fixed_val))
                    except (ValueError, TypeError):
                        fixed_val = None
                else:
                    fixed_val = None
                StoreInventoryRangeMultiplier.objects.create(
                    inventory_settings=inv,
                    from_value=from_val,
                    to_value=to_value,
                    range_type=range_type,
                    multiplier=_c(rm.get('multiplier', 1) or 1),
                    fixed_value=fixed_val,
                )

    def _save_sync_schedule(self, store, data, SyncSchedule):
        if not data or not isinstance(data, dict):
            return
        enabled = data.get('enabled', False)
        if not enabled:
            SyncSchedule.objects.filter(store=store).delete()
            return
        defaults = {
            'schedule_type': data.get('schedule_type', 'crontab'),
            'crontab_hour': str(data.get('crontab_hour', '10')),
            'crontab_minute': str(data.get('crontab_minute', '0')),
            'crontab_day_of_week': str(data.get('crontab_day_of_week', '*')),
            'crontab_day_of_month': '*',
            'crontab_month_of_year': '*',
            'interval_seconds': data.get('interval_seconds'),
            'timezone': data.get('timezone', 'UTC'),
            'is_active': True,
        }
        SyncSchedule.objects.update_or_create(store=store, defaults=defaults)
