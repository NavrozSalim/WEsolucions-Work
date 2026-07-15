"""DRF endpoints for managed-store listings (created products) and orders."""
import logging

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from stores.models import Store

from . import csv_import, export_xlsx, listing_service, order_service, shipping_service, ticket_service
from .errors import MarketplaceError
from .models import ListingAction, ListingStatus, ListingUpload, MarketplaceOrder, StoreListing, SupportTicket
from .serializers import (
    ListingInputSerializer,
    ListingUploadSerializer,
    MarketplaceOrderSerializer,
    StoreListingSerializer,
    SupportTicketSerializer,
)

logger = logging.getLogger("listings")

INVENTORY_STATUSES = (
    ListingStatus.UPLOADED_STAGING,
    ListingStatus.UPLOADED_PRODUCTION,
)
CREATED_STATUSES = (
    ListingStatus.DRAFT,
    ListingStatus.VALIDATION_FAILED,
    ListingStatus.READY,
    ListingStatus.FAILED,
)


def _get_store(request, store_pk) -> Store:
    return get_object_or_404(Store, pk=store_pk, user=request.user)


def _get_listing(request, store, pk) -> StoreListing:
    return get_object_or_404(StoreListing, pk=pk, store=store, user=request.user)


def _filter_listings(qs, request):
    view = (request.query_params.get('view') or '').strip().lower()
    if view == 'inventory':
        qs = qs.filter(status__in=INVENTORY_STATUSES)
    elif view == 'created':
        qs = qs.filter(status__in=CREATED_STATUSES)

    status_filter = request.query_params.get('status')
    if status_filter:
        qs = qs.filter(status=status_filter)

    if request.query_params.get('errors') in ('1', 'true', 'yes'):
        qs = qs.filter(status=ListingStatus.VALIDATION_FAILED)

    search = (request.query_params.get('search') or '').strip()
    if search:
        from django.db.models import Q
        qs = qs.filter(
            Q(sku__icontains=search)
            | Q(title__icontains=search)
            | Q(external_variant_key__icontains=search)
        )
    return qs


class StoreListingListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, store_pk):
        store = _get_store(request, store_pk)
        qs = StoreListing.objects.filter(store=store, user=request.user)
        qs = _filter_listings(qs, request)
        return Response(StoreListingSerializer(qs, many=True).data)

    def post(self, request, store_pk):
        store = _get_store(request, store_pk)
        ser = ListingInputSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        action = data.pop('action', ListingAction.CREATE)
        try:
            listing = listing_service.create(request.user, store, data, action=action)
        except MarketplaceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        has_errors = listing.status == ListingStatus.VALIDATION_FAILED
        listing_service.record_activity(
            request.user, store,
            action=action,
            source=ListingUpload.Source.SINGLE,
            filename='Single listing',
            total=1,
            success=0 if has_errors else 1,
            errors=1 if has_errors else 0,
            rows=[{
                'sku': listing.sku,
                'variant_key': listing.external_variant_key,
                'valid': not has_errors,
                'errors': listing.validation_errors_json or [],
            }],
            message=f'Created listing {listing.external_variant_key}.',
        )
        return Response(StoreListingSerializer(listing).data, status=status.HTTP_201_CREATED)


class StoreListingDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, store_pk, pk):
        store = _get_store(request, store_pk)
        listing = _get_listing(request, store, pk)
        return Response(StoreListingSerializer(listing).data)

    def put(self, request, store_pk, pk):
        store = _get_store(request, store_pk)
        listing = _get_listing(request, store, pk)
        ser = ListingInputSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        data.pop('action', None)
        listing = listing_service.update(listing, data)
        has_errors = listing.status == ListingStatus.VALIDATION_FAILED
        listing_service.record_activity(
            request.user, store,
            action=listing.action,
            source=ListingUpload.Source.SINGLE,
            filename=f'Edit {listing.external_variant_key}',
            total=1,
            success=0 if has_errors else 1,
            errors=1 if has_errors else 0,
            message=f'Updated listing {listing.external_variant_key}.',
        )
        return Response(StoreListingSerializer(listing).data)

    def delete(self, request, store_pk, pk):
        store = _get_store(request, store_pk)
        listing = _get_listing(request, store, pk)
        variant_key = listing.external_variant_key
        try:
            listing_service.delete(request.user, store, listing)
        except MarketplaceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        listing_service.record_activity(
            request.user, store,
            action=ListingAction.DELETE,
            source=ListingUpload.Source.SINGLE,
            filename=f'Delete {variant_key}',
            total=1, success=1, errors=0,
            message=f'Deleted listing {variant_key}.',
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class StoreListingTemplateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, store_pk):
        _get_store(request, store_pk)
        action = (request.query_params.get('action') or 'create').strip().lower()
        resp = HttpResponse(csv_import.build_template_csv(action), content_type='text/csv')
        name = f'listing_template_{action}.csv'
        resp['Content-Disposition'] = f'attachment; filename="{name}"'
        return resp


class StoreListingBulkUploadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, store_pk):
        store = _get_store(request, store_pk)
        upload = request.FILES.get('file')
        if not upload:
            return Response({'detail': 'Attach a CSV or XLSX file as "file".'},
                            status=status.HTTP_400_BAD_REQUEST)
        action = (request.data.get('action') or request.POST.get('action') or '').strip().lower()
        try:
            result = listing_service.bulk_import(
                request.user, store, upload.name, upload.read(), action=action,
            )
        except MarketplaceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


class StoreListingUploadHistoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, store_pk):
        store = _get_store(request, store_pk)
        qs = ListingUpload.objects.filter(store=store, user=request.user)
        return Response(ListingUploadSerializer(qs, many=True).data)


class StoreListingPublishView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, store_pk):
        store = _get_store(request, store_pk)
        listing_ids = request.data.get('listing_ids') or None
        try:
            result = listing_service.publish(request.user, store, listing_ids)
        except MarketplaceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        code = status.HTTP_200_OK if result.get('ok') else status.HTTP_502_BAD_GATEWAY
        if result.get('published'):
            listing_service.record_activity(
                request.user, store,
                action=ListingAction.CREATE,
                source=ListingUpload.Source.SINGLE,
                filename='Publish to marketplace',
                total=result.get('published', 0),
                success=result.get('published', 0),
                message=result.get('message') or '',
            )
        return Response(result, status=code)


class StoreOrdersView(APIView):
    """List orders for a store. ``?refresh=1`` pulls fresh orders from the marketplace first."""
    permission_classes = [IsAuthenticated]

    def get(self, request, store_pk):
        store = _get_store(request, store_pk)
        refresh_result = None
        if request.query_params.get('refresh') in ('1', 'true', 'yes'):
            try:
                refresh_result = order_service.fetch(request.user, store)
            except MarketplaceError as exc:
                refresh_result = {'ok': False, 'message': str(exc), 'fetched': 0}
        orders = list(
            MarketplaceOrder.objects.filter(store=store, user=request.user)
            .prefetch_related('shipments')
        )
        tickets_by_order_key: dict[str, list] = {}
        for ticket in SupportTicket.objects.filter(store=store, user=request.user).exclude(
            related_order_key='',
        ).only(
            'id', 'subject', 'status', 'unread_count', 'customer_name', 'related_order_key',
        ):
            key = (ticket.related_order_key or '').strip().lower()
            if not key:
                continue
            tickets_by_order_key.setdefault(key, []).append({
                'id': str(ticket.id),
                'subject': ticket.subject or 'Customer message',
                'status': ticket.status,
                'unread_count': ticket.unread_count or 0,
                'customer_name': ticket.customer_name or '',
            })
        return Response({
            'refresh': refresh_result,
            'orders': MarketplaceOrderSerializer(
                orders,
                many=True,
                context={'tickets_by_order_key': tickets_by_order_key},
            ).data,
        })


class StoreOrdersExportView(APIView):
    """Download store orders as an Excel (.xlsx) file."""
    permission_classes = [IsAuthenticated]

    def get(self, request, store_pk):
        store = _get_store(request, store_pk)
        orders = list(
            MarketplaceOrder.objects.filter(store=store, user=request.user)
            .prefetch_related('shipments')
            .order_by('-created_at')
        )
        tickets_by_order_key: dict[str, list] = {}
        for ticket in SupportTicket.objects.filter(store=store, user=request.user).exclude(
            related_order_key='',
        ).only('id', 'subject', 'related_order_key'):
            key = (ticket.related_order_key or '').strip().lower()
            if not key:
                continue
            tickets_by_order_key.setdefault(key, []).append({
                'id': str(ticket.id),
                'subject': ticket.subject or 'Customer message',
            })
        for order in orders:
            keys = []
            for candidate in (order.invoice_number, order.external_order_key):
                text = str(candidate or '').strip().lower()
                if text and text not in keys:
                    keys.append(text)
            related = []
            seen = set()
            for key in keys:
                for ticket in tickets_by_order_key.get(key, []):
                    tid = ticket.get('id')
                    if tid and tid not in seen:
                        seen.add(tid)
                        related.append(ticket)
            order._related_tickets_export = related  # noqa: SLF001

        content = export_xlsx.build_orders_xlsx(orders, store)
        safe_name = ''.join(c if c.isalnum() or c in '-_' else '_' for c in (store.name or 'store'))[:40]
        filename = f'orders_{safe_name}.xlsx'
        response = HttpResponse(
            content,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


class StoreOrderTestView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, store_pk):
        store = _get_store(request, store_pk)
        try:
            result = order_service.create_test_order(request.user, store)
        except MarketplaceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        code = status.HTTP_200_OK if result.get('ok') else status.HTTP_502_BAD_GATEWAY
        return Response(result, status=code)


class StoreOrderShippingView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, store_pk, pk):
        store = _get_store(request, store_pk)
        order = get_object_or_404(MarketplaceOrder, pk=pk, store=store, user=request.user)
        tracking_number = (request.data.get('tracking_number') or '').strip()
        carrier = (request.data.get('carrier') or '').strip()
        if not tracking_number or not carrier:
            return Response(
                {'detail': 'tracking_number and carrier are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            result = shipping_service.submit(
                order,
                tracking_number=tracking_number,
                carrier=carrier,
                tracking_url=(request.data.get('tracking_url') or '').strip(),
                shipped_date=(request.data.get('shipped_date') or '').strip(),
                status=(request.data.get('status') or '').strip(),
            )
        except MarketplaceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        code = status.HTTP_200_OK if result.get('ok') else status.HTTP_502_BAD_GATEWAY
        return Response(result, status=code)


class StoreOrderShippingCompleteView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, store_pk, pk):
        store = _get_store(request, store_pk)
        order = get_object_or_404(MarketplaceOrder, pk=pk, store=store, user=request.user)
        try:
            result = shipping_service.complete(order)
        except MarketplaceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        code = status.HTTP_200_OK if result.get('ok') else status.HTTP_502_BAD_GATEWAY
        return Response(result, status=code)


class StoreOrderCancelView(APIView):
    """Cancel an order (Lasoo Refunds_Create) and mark it cancelled locally."""
    permission_classes = [IsAuthenticated]

    def post(self, request, store_pk, pk):
        store = _get_store(request, store_pk)
        order = get_object_or_404(MarketplaceOrder, pk=pk, store=store, user=request.user)
        reason = (request.data.get('reason') or '').strip()
        try:
            result = order_service.cancel(order, reason=reason)
        except MarketplaceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        code = status.HTTP_200_OK if result.get('ok') else status.HTTP_502_BAD_GATEWAY
        return Response(result, status=code)


class StoreOrderCancelReasonsView(APIView):
    """Marketplace-provided cancel/refund reason options for the Orders UI."""
    permission_classes = [IsAuthenticated]

    def get(self, request, store_pk):
        store = _get_store(request, store_pk)
        try:
            result = order_service.cancel_reasons(store)
        except MarketplaceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


class StoreTicketsView(APIView):
    """List support tickets. ``?refresh=1`` pulls from the marketplace first."""
    permission_classes = [IsAuthenticated]

    def get(self, request, store_pk):
        store = _get_store(request, store_pk)
        refresh_result = None
        if request.query_params.get('refresh') in ('1', 'true', 'yes'):
            try:
                refresh_result = ticket_service.fetch(request.user, store)
            except MarketplaceError as exc:
                refresh_result = {'ok': False, 'message': str(exc), 'fetched': 0}
        tickets = (
            SupportTicket.objects.filter(store=store, user=request.user)
            .prefetch_related('messages')
        )
        return Response({
            'refresh': refresh_result,
            'tickets': SupportTicketSerializer(tickets, many=True).data,
        })


class StoreTicketsExportView(APIView):
    """Download store tickets as an Excel (.xlsx) file."""
    permission_classes = [IsAuthenticated]

    def get(self, request, store_pk):
        store = _get_store(request, store_pk)
        tickets = list(
            SupportTicket.objects.filter(store=store, user=request.user)
            .order_by('-last_message_at', '-created_at')
        )
        content = export_xlsx.build_tickets_xlsx(tickets)
        safe_name = ''.join(c if c.isalnum() or c in '-_' else '_' for c in (store.name or 'store'))[:40]
        filename = f'tickets_{safe_name}.xlsx'
        response = HttpResponse(
            content,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


class StoreTicketTestView(APIView):
    """Create a local sample ticket for UI testing (staging)."""
    permission_classes = [IsAuthenticated]

    def post(self, request, store_pk):
        store = _get_store(request, store_pk)
        try:
            result = ticket_service.create_test_ticket(request.user, store)
        except MarketplaceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


class StoreTicketReplyView(APIView):
    """Reply to a ticket; attempts marketplace delivery to the customer."""
    permission_classes = [IsAuthenticated]

    def post(self, request, store_pk, pk):
        store = _get_store(request, store_pk)
        ticket = get_object_or_404(SupportTicket, pk=pk, store=store, user=request.user)
        body = (request.data.get('body') or request.data.get('message') or '').strip()
        if not body:
            return Response({'detail': 'body is required.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            result = ticket_service.reply(
                ticket,
                body=body,
                sender_name=(
                    f"{getattr(request.user, 'first_name', '')} {getattr(request.user, 'last_name', '')}".strip()
                    or getattr(request.user, 'email', '')
                    or 'Seller'
                ),
            )
        except MarketplaceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        code = status.HTTP_200_OK if result.get('ok') else status.HTTP_502_BAD_GATEWAY
        return Response(result, status=code)
