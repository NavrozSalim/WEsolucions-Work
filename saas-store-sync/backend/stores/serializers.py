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
            'multiplier', 'optional_fee', 'rounding_option', 'continuous_update',
            'range_margins',
        ]

    def get_range_margins(self, obj):
        return [
            {
                'id': str(m.id),
                'from_value': float(m.price_range.from_value),
                'to_value': float(m.price_range.to_value) if m.price_range.to_value else None,
                'margin_type': getattr(m, 'margin_type', 'percentage') or 'percentage',
                'margin_percentage': float(m.margin_percentage),
            }
            for m in obj.range_margins.select_related('price_range').all()
        ]


class StoreVendorInventorySettingsReadSerializer(serializers.ModelSerializer):
    vendor_code = serializers.CharField(source='vendor.code', read_only=True)
    vendor_name = serializers.CharField(source='vendor.name', read_only=True)
    range_multipliers = serializers.SerializerMethodField()

    class Meta:
        model = StoreVendorInventorySettings
        fields = [
            'id', 'vendor', 'vendor_code', 'vendor_name',
            'rule_type', 'default_multiplier', 'default_value', 'zero_if_low',
            'range_multipliers',
        ]

    def get_range_multipliers(self, obj):
        return [
            {
                'id': str(r.id),
                'from_value': float(r.from_value),
                'to_value': float(r.to_value) if r.to_value else None,
                'range_type': getattr(r, 'range_type', 'multiplier') or 'multiplier',
                'multiplier': float(r.multiplier),
                'fixed_value': r.fixed_value,
            }
            for r in obj.range_multipliers.all()
        ]


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
    vendor_price_settings = StoreVendorPriceSettingsReadSerializer(many=True, read_only=True)
    vendor_inventory_settings = StoreVendorInventorySettingsReadSerializer(many=True, read_only=True)
    sync_schedule = serializers.SerializerMethodField()

    class Meta:
        model = Store
        fields = [
            'id', 'name', 'region', 'api_token', 'marketplace', 'marketplace_id', 'marketplace_name',
            'connection_status', 'last_validated_at',
            'is_active', 'created_at', 'updated_at',
            'vendor_price_settings', 'vendor_inventory_settings',
            'sync_schedule',
        ]
        extra_kwargs = {
            'api_token': {'write_only': True},
            'marketplace': {'allow_null': True},
            'connection_status': {'read_only': True},
            'last_validated_at': {'read_only': True},
        }

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
        sched_raw = req.get('sync_schedule')
        if not sched_raw or not isinstance(sched_raw, dict) or not sched_raw.get('enabled', False):
            raise ValidationError({'sync_schedule': 'Scheduled updates are required. Choose frequency and time.'})

        price_settings_data = req.get('vendor_price_settings', [])
        inventory_settings_data = req.get('vendor_inventory_settings', [])
        store_data = {k: v for k, v in validated_data.items() if k in ('name', 'region', 'api_token', 'marketplace', 'is_active')}
        if store_data.get('name'):
            store_data['name'] = store_data['name'].strip()
        if mkt and Store.objects.filter(user=user, name=store_data.get('name', ''), marketplace=mkt).exists():
            raise ValidationError({
                'name': f'A store named "{store_data.get("name")}" already exists for this marketplace.',
            })
        try:
            store = Store.objects.create(user=user, **store_data)
        except IntegrityError as exc:
            if 'uq_store_user_name_marketplace' in str(exc) or 'UNIQUE constraint failed' in str(exc):
                raise ValidationError({
                    'name': 'A store with this name and marketplace already exists.',
                }) from None
            raise
        self._save_vendor_price_settings(store, price_settings_data, Vendor)
        self._save_vendor_inventory_settings(store, inventory_settings_data, Vendor)
        self._save_sync_schedule(store, req.get('sync_schedule'), SyncSchedule)
        return store

    def update(self, instance, validated_data):
        from vendor.models import Vendor
        from marketplace.models import Marketplace
        from sync.models import SyncSchedule
        req = self.context['request'].data
        for attr, value in validated_data.items():
            if attr in ('name', 'region', 'api_token', 'marketplace', 'is_active'):
                setattr(instance, attr, value)
        marketplace_id = req.get('marketplace_id') or req.get('marketplace')
        if marketplace_id is not None:
            try:
                mkt = Marketplace.objects.get(id=marketplace_id) if marketplace_id else None
                instance.marketplace = mkt
            except Marketplace.DoesNotExist:
                pass
        if instance.name:
            instance.name = instance.name.strip()
        if Store.objects.filter(
            user=instance.user, name=instance.name, marketplace=instance.marketplace,
        ).exclude(pk=instance.pk).exists():
            raise ValidationError({
                'name': f'A store named "{instance.name}" already exists for this marketplace.',
            })
        try:
            instance.save()
        except IntegrityError as exc:
            if 'uq_store_user_name_marketplace' in str(exc) or 'UNIQUE constraint failed' in str(exc):
                raise ValidationError({
                    'name': 'A store with this name and marketplace already exists.',
                }) from None
            raise
        if 'vendor_price_settings' in req:
            self._save_vendor_price_settings(instance, req['vendor_price_settings'], Vendor)
        if 'vendor_inventory_settings' in req:
            self._save_vendor_inventory_settings(instance, req['vendor_inventory_settings'], Vendor)
        if 'sync_schedule' in req:
            self._save_sync_schedule(instance, req['sync_schedule'], SyncSchedule)
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
            ps = StoreVendorPriceSettings.objects.create(
                store=store, vendor=vendor,
                purchase_tax_percentage=_c(item.get('purchase_tax_percentage', 0) or 0),
                marketplace_fees_percentage=_c(item.get('marketplace_fees_percentage', 0) or 0),
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
