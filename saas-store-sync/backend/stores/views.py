from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from stores.models import Store
from stores.serializers import StoreSerializer
from rest_framework.permissions import IsAuthenticated
from audit.utils import log_action
from core.throttles import ProgressReadRateThrottle
from users.drf_permissions import HasProductPermission
from users.org_scope import stores_for_user


class StoreViewSet(viewsets.ModelViewSet):
    serializer_class = StoreSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_permissions(self):
        # List/retrieve: any authenticated org member (catalog/orders need store lists).
        # Mutations: require the stores module permission.
        if getattr(self, 'action', None) in ('list', 'retrieve'):
            return [IsAuthenticated()]
        return [IsAuthenticated(), HasProductPermission()]

    required_product_permission = 'stores'

    def get_throttles(self):
        if getattr(self, 'action', None) in ('list', 'retrieve'):
            return [ProgressReadRateThrottle()]
        return super().get_throttles()

    def get_queryset(self):
        qs = stores_for_user(self.request.user)
        # List only needs metadata + nested settings; skip loading ciphertext columns.
        if getattr(self, 'action', None) == 'list':
            qs = qs.defer('api_token', 'kogan_service_account_json')
        return qs

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
        from stores.credentials import verify_store_connection

        valid, _msg = verify_store_connection(store)
        store.connection_status = 'connected' if valid else 'error'
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

    @action(detail=True, methods=['post'], url_path='nora-inventory')
    def upload_nora_inventory(self, request, pk=None):
        """Upload / overwrite the Nora Inventory Excel for this store.

        Expects multipart field ``file`` (.xlsx). Requires Nora Inventory to
        already be present in vendor_inventory_settings.
        """
        from django.utils import timezone
        from scrapers.nora_au_ingest import load_nora_stock_map_from_file
        from stores.models import StoreVendorInventorySettings
        from stores.nora import get_nora_inventory_settings

        store = self.get_object()
        upload = request.FILES.get('file')
        if not upload:
            return Response({'detail': 'Attach an Excel file as "file".'}, status=status.HTTP_400_BAD_REQUEST)
        name = (upload.name or '').lower()
        if not name.endswith(('.xlsx', '.xlsm', '.xls')):
            return Response(
                {'detail': 'Nora inventory must be an Excel file (.xlsx).'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        inv = get_nora_inventory_settings(store)
        if inv is None:
            # Auto-create inventory settings row if Nora vendor exists but wasn't added yet.
            from vendor.models import Vendor
            nora = Vendor.objects.filter(code='noraau').first()
            if not nora:
                return Response(
                    {'detail': 'Nora Inventory vendor is not seeded. Run migrations.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            inv = StoreVendorInventorySettings.objects.create(
                store=store,
                vendor=nora,
                rule_type='multiplier',
            )
            from stores.models import StoreInventoryRangeMultiplier
            from decimal import Decimal
            StoreInventoryRangeMultiplier.objects.create(
                inventory_settings=inv,
                from_value=Decimal('0'),
                to_value=Decimal('999999999'),
                range_type='multiplier',
                multiplier=Decimal('1'),
            )

        # Validate parse before saving
        try:
            stock_map = load_nora_stock_map_from_file(upload)
        except Exception as exc:
            return Response(
                {'detail': f'Could not parse Nora Excel: {exc}'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if hasattr(upload, 'seek'):
            upload.seek(0)

        if inv.nora_inventory_file:
            inv.nora_inventory_file.delete(save=False)
        inv.nora_inventory_file = upload
        inv.nora_inventory_original_name = upload.name or ''
        inv.nora_inventory_uploaded_at = timezone.now()
        inv.save(update_fields=[
            'nora_inventory_file',
            'nora_inventory_original_name',
            'nora_inventory_uploaded_at',
            'updated_at',
        ])
        return Response({
            'ok': True,
            'message': f'Nora inventory uploaded ({len(stock_map)} Vendor ID(s)).',
            'vendor_ids': len(stock_map),
            'file_name': inv.nora_inventory_original_name,
            'uploaded_at': inv.nora_inventory_uploaded_at,
        })

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

    def _run_connection_test(self, store):
        from django.utils import timezone as tz
        from stores.credentials import marketplace_kind, verify_store_connection

        valid, err_msg = verify_store_connection(store)
        store.last_validated_at = tz.now()
        if valid:
            store.connection_status = 'connected'
            store.save(update_fields=['connection_status', 'last_validated_at'])
            message = err_msg or 'Connection successful'
            kind = marketplace_kind(store.marketplace)
            if kind == 'walmart' and not err_msg:
                from store_adapters.walmart_adapter import MSG_WALMART_CONNECTED
                message = MSG_WALMART_CONNECTED
            elif kind == 'sears' and err_msg:
                message = err_msg
            return Response({
                'valid': True,
                'message': message,
                'connection_status': 'connected',
            })
        store.connection_status = 'error'
        store.save(update_fields=['connection_status', 'last_validated_at'])
        message = err_msg or 'Invalid or missing credentials'
        return Response(
            {'valid': False, 'message': message, 'connection_status': 'error'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    @action(detail=True, methods=['post'])
    def validate(self, request, pk=None):
        """Validate store API token / connection. Persists connection_status."""
        store = self.get_object()
        return self._run_connection_test(store)

    @action(detail=True, methods=['post'], url_path='test-connection')
    def test_connection(self, request, pk=None):
        """Alias for validate — Test Connection in the UI."""
        store = self.get_object()
        return self._run_connection_test(store)

    @action(detail=False, methods=['post'], url_path='test-walmart-connection')
    def test_walmart_connection(self, request):
        """
        Test Walmart credentials from JSON before saving a store (create flow).
        Body: { "api_token": "{...}", "region": "USA" }.
        """
        from rest_framework.exceptions import ValidationError as DRFValidationError
        from stores.credentials import (
            validate_api_token_shape,
            verify_walmart_credentials_from_token,
        )

        api_token = (request.data.get('api_token') or '').strip()
        if not api_token:
            return Response(
                {'valid': False, 'message': 'API credentials are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            mkt = type('M', (), {'code': 'walmart', 'name': 'Walmart'})()
            normalized = validate_api_token_shape(mkt, api_token)
        except DRFValidationError as exc:
            detail = exc.detail
            if isinstance(detail, dict):
                msg = detail.get('api_token')
                if isinstance(msg, list):
                    msg = msg[0] if msg else str(detail)
            else:
                msg = str(detail)
            return Response({'valid': False, 'message': str(msg)}, status=status.HTTP_400_BAD_REQUEST)

        region = (request.data.get('region') or 'USA').strip() or 'USA'
        valid, err_msg = verify_walmart_credentials_from_token(
            normalized,
            region=region,
            use_sandbox=bool(request.data.get('use_sandbox')),
        )
        if valid:
            from store_adapters.walmart_adapter import MSG_WALMART_CONNECTED
            return Response({'valid': True, 'message': MSG_WALMART_CONNECTED})
        return Response(
            {'valid': False, 'message': err_msg or 'Invalid Walmart API credentials.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    @action(detail=False, methods=['post'], url_path='test-sears-connection')
    def test_sears_connection(self, request):
        """
        Test Sears credentials from JSON before saving a store (create flow).
        Body: { "api_token": "{...}" }.
        """
        from rest_framework.exceptions import ValidationError as DRFValidationError
        from stores.credentials import (
            validate_api_token_shape,
            verify_sears_credentials_from_token,
        )

        api_token = (request.data.get('api_token') or '').strip()
        if not api_token:
            return Response(
                {'valid': False, 'message': 'API credentials are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            mkt = type('M', (), {'code': 'sears', 'name': 'Sears'})()
            normalized = validate_api_token_shape(mkt, api_token)
        except DRFValidationError as exc:
            detail = exc.detail
            if isinstance(detail, dict):
                msg = detail.get('api_token')
                if isinstance(msg, list):
                    msg = msg[0] if msg else str(detail)
            else:
                msg = str(detail)
            return Response({'valid': False, 'message': str(msg)}, status=status.HTTP_400_BAD_REQUEST)

        ok, msg = verify_sears_credentials_from_token(normalized)
        if ok:
            return Response({'valid': True, 'message': msg})
        return Response(
            {'valid': False, 'message': msg or 'Invalid Sears API credentials.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
