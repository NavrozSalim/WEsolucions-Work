"""DRF endpoints for managed-store listings (created products) and orders."""
import logging

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from stores.models import Store

from . import csv_import, listing_service, order_service, shipping_service
from .errors import MarketplaceError
from .models import MarketplaceOrder, StoreListing
from .serializers import (
    ListingInputSerializer,
    MarketplaceOrderSerializer,
    StoreListingSerializer,
)

logger = logging.getLogger("listings")


def _get_store(request, store_pk) -> Store:
    return get_object_or_404(Store, pk=store_pk, user=request.user)


def _get_listing(request, store, pk) -> StoreListing:
    return get_object_or_404(StoreListing, pk=pk, store=store, user=request.user)


class StoreListingListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, store_pk):
        store = _get_store(request, store_pk)
        qs = StoreListing.objects.filter(store=store, user=request.user)
        status_filter = request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        search = (request.query_params.get('search') or '').strip()
        if search:
            from django.db.models import Q
            qs = qs.filter(
                Q(sku__icontains=search)
                | Q(title__icontains=search)
                | Q(external_variant_key__icontains=search)
            )
        return Response(StoreListingSerializer(qs, many=True).data)

    def post(self, request, store_pk):
        store = _get_store(request, store_pk)
        ser = ListingInputSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            listing = listing_service.create(request.user, store, ser.validated_data)
        except MarketplaceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
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
        listing = listing_service.update(listing, ser.validated_data)
        return Response(StoreListingSerializer(listing).data)

    def delete(self, request, store_pk, pk):
        store = _get_store(request, store_pk)
        listing = _get_listing(request, store, pk)
        listing.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class StoreListingTemplateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, store_pk):
        _get_store(request, store_pk)
        resp = HttpResponse(csv_import.build_template_csv(), content_type='text/csv')
        resp['Content-Disposition'] = 'attachment; filename="listing_template.csv"'
        return resp


class StoreListingBulkUploadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, store_pk):
        store = _get_store(request, store_pk)
        upload = request.FILES.get('file')
        if not upload:
            return Response({'detail': 'Attach a CSV or XLSX file as "file".'},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            result = listing_service.bulk_import(
                request.user, store, upload.name, upload.read(),
            )
        except MarketplaceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


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
        orders = (
            MarketplaceOrder.objects.filter(store=store, user=request.user)
            .prefetch_related('shipments')
        )
        return Response({
            'refresh': refresh_result,
            'orders': MarketplaceOrderSerializer(orders, many=True).data,
        })


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
