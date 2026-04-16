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
