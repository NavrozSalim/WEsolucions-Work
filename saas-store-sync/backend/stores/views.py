from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from stores.models import Store
from stores.serializers import StoreSerializer
from rest_framework.permissions import IsAuthenticated
from audit.utils import log_action


class StoreViewSet(viewsets.ModelViewSet):
    serializer_class = StoreSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        # Admin users need cross-account visibility in Store Settings.
        if getattr(user, 'is_superuser', False) or getattr(user, 'is_staff', False):
            return Store.objects.all()
        return Store.objects.filter(user=user)

    def create(self, request, *args, **kwargs):
        """Override to catch errors and return JSON instead of 500."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            self.perform_create(serializer)
            headers = self.get_success_headers(serializer.data)
            return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
        except ValidationError as e:
            return Response(e.detail if hasattr(e, 'detail') else {'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({'detail': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def destroy(self, request, *args, **kwargs):
        """Override to catch errors and return JSON instead of 500."""
        try:
            instance = self.get_object()
        except Exception as e:
            return Response({'detail': str(e)}, status=status.HTTP_404_NOT_FOUND)
        try:
            self.perform_destroy(instance)
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Exception as e:
            from django.db import IntegrityError, ProtectedError
            err_msg = str(e)
            if isinstance(e, (ProtectedError, IntegrityError)):
                err_msg = f"Cannot delete store: {err_msg}"
                return Response({'detail': err_msg}, status=status.HTTP_400_BAD_REQUEST)
            return Response({'detail': err_msg}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def perform_create(self, serializer):
        store = serializer.save()
        self._auto_validate_store_connection(store)
        try:
            log_action(self.request.user, 'store_created', 'store', store.id, {'name': store.name}, self.request)
        except Exception:
            pass  # Don't block create if audit fails

    def perform_update(self, serializer):
        token_updated = 'api_token' in serializer.validated_data
        marketplace_updated = 'marketplace' in serializer.validated_data
        kogan_updated = any(
            k in serializer.validated_data
            for k in (
                'kogan_service_account_json',
                'kogan_sheet_id',
                'kogan_tab_name',
                'kogan_sku_column',
                'kogan_stock_column',
                'kogan_price_column',
                'kogan_rrp_column',
                'kogan_first_price_column',
            )
        )
        store = serializer.save()
        if token_updated or marketplace_updated or kogan_updated:
            self._auto_validate_store_connection(store)
        if token_updated:
            try:
                log_action(self.request.user, 'store_token_updated', 'store', store.id, {'name': store.name}, self.request)
            except Exception:
                pass

    @staticmethod
    def _auto_validate_store_connection(store):
        """
        Best-effort connection validation after create/token update so users do not
        need a separate manual "Connect" click for supported marketplaces.
        """
        from django.utils import timezone as tz
        from store_adapters import get_adapter

        try:
            adapter = get_adapter(store)
            valid = getattr(
                adapter,
                'validate_connection',
                lambda: bool(store.api_token and len(str(store.api_token or '')) > 10),
            )()
            store.connection_status = 'connected' if valid else 'error'
        except Exception:
            store.connection_status = 'error'
        store.last_validated_at = tz.now()
        store.save(update_fields=['connection_status', 'last_validated_at'])

    def perform_destroy(self, instance):
        from django.db import transaction
        oid, name = str(instance.id), instance.name
        with transaction.atomic():
            instance.delete()
        try:
            log_action(self.request.user, 'store_deleted', 'store', oid, {'name': name}, self.request)
        except Exception:
            pass  # Don't block delete if audit fails

    @action(detail=True, methods=['post'], url_path='duplicate-vendor-settings')
    def duplicate_vendor_settings(self, request, pk=None):
        """Clone an existing per-vendor pricing+inventory setup to another vendor.

        Body: ``{"from_vendor_id": "<uuid>", "to_vendor_id": "<uuid>"}``.
        Copies ``StoreVendorPriceSettings`` (with every ``StorePriceRangeMargin``)
        and ``StoreVendorInventorySettings`` onto ``to_vendor_id`` without touching
        ``from_vendor_id``. Overwrites any existing settings for the target vendor
        so the user can re-clone after tweaking.
        """
        from django.db import transaction
        from stores.models import (
            StorePriceRangeMargin,
            StoreVendorInventorySettings,
            StoreVendorPriceSettings,
        )
        from vendor.models import Vendor

        store = self.get_object()
        from_vendor_id = request.data.get('from_vendor_id')
        to_vendor_id = request.data.get('to_vendor_id')
        if not from_vendor_id or not to_vendor_id:
            return Response(
                {'detail': 'from_vendor_id and to_vendor_id are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if str(from_vendor_id) == str(to_vendor_id):
            return Response(
                {'detail': 'Source and target vendor must differ.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            from_vendor = Vendor.objects.get(id=from_vendor_id)
            to_vendor = Vendor.objects.get(id=to_vendor_id)
        except Vendor.DoesNotExist:
            return Response({'detail': 'Vendor not found.'}, status=status.HTTP_404_NOT_FOUND)

        src = StoreVendorPriceSettings.objects.filter(store=store, vendor=from_vendor).first()
        if not src:
            return Response(
                {'detail': f'No price settings configured for vendor "{from_vendor.name}".'},
                status=status.HTTP_404_NOT_FOUND,
            )

        with transaction.atomic():
            StoreVendorPriceSettings.objects.filter(store=store, vendor=to_vendor).delete()
            target = StoreVendorPriceSettings.objects.create(
                store=store,
                vendor=to_vendor,
                purchase_tax_percentage=src.purchase_tax_percentage,
                marketplace_fees_percentage=src.marketplace_fees_percentage,
                multiplier=src.multiplier,
                optional_fee=src.optional_fee,
                rounding_option=src.rounding_option,
                continuous_update=src.continuous_update,
            )
            for m in src.range_margins.all():
                StorePriceRangeMargin.objects.create(
                    price_settings=target,
                    price_range=m.price_range,
                    margin_type=m.margin_type,
                    margin_percentage=m.margin_percentage,
                    minimum_margin_cents=m.minimum_margin_cents,
                    dont_pay_discount_percentage=m.dont_pay_discount_percentage,
                )

            src_inv = StoreVendorInventorySettings.objects.filter(
                store=store, vendor=from_vendor,
            ).first()
            if src_inv:
                StoreVendorInventorySettings.objects.filter(
                    store=store, vendor=to_vendor,
                ).delete()
                new_inv = StoreVendorInventorySettings.objects.create(
                    store=store,
                    vendor=to_vendor,
                    rule_type=src_inv.rule_type,
                    default_multiplier=src_inv.default_multiplier,
                    default_value=src_inv.default_value,
                    zero_if_low=src_inv.zero_if_low,
                )
                for rm in src_inv.range_multipliers.all():
                    new_inv.range_multipliers.create(
                        from_value=rm.from_value,
                        to_value=rm.to_value,
                        range_type=rm.range_type,
                        multiplier=rm.multiplier,
                        fixed_value=rm.fixed_value,
                    )

        try:
            log_action(
                request.user,
                'vendor_settings_duplicated',
                'store', store.id,
                {
                    'store': store.name,
                    'from_vendor': from_vendor.code,
                    'to_vendor': to_vendor.code,
                },
                request,
            )
        except Exception:
            pass

        return Response(
            {
                'store_id': str(store.id),
                'from_vendor_id': str(from_vendor.id),
                'to_vendor_id': str(to_vendor.id),
                'tiers_copied': src.range_margins.count(),
                'inventory_copied': bool(src_inv),
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['post'])
    def validate(self, request, pk=None):
        """Validate store API token / connection. Persists connection_status."""
        store = self.get_object()
        try:
            from store_adapters import get_adapter
            from django.utils import timezone as tz
            adapter = get_adapter(store)
            valid = getattr(adapter, 'validate_connection', lambda: bool(store.api_token and len(str(store.api_token or '')) > 10))()
            store.last_validated_at = tz.now()
            if valid:
                store.connection_status = 'connected'
                store.save(update_fields=['connection_status', 'last_validated_at'])
                return Response({'valid': True, 'message': 'Connection successful', 'connection_status': 'connected'})
            store.connection_status = 'error'
            store.save(update_fields=['connection_status', 'last_validated_at'])
            return Response({'valid': False, 'message': 'Invalid or missing token', 'connection_status': 'error'}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            from django.utils import timezone as tz
            store.connection_status = 'error'
            store.last_validated_at = tz.now()
            store.save(update_fields=['connection_status', 'last_validated_at'])
            return Response({'valid': False, 'message': str(e), 'connection_status': 'error'}, status=status.HTTP_400_BAD_REQUEST)
