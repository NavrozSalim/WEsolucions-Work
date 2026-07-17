"""DRF endpoints for managed-store listings (created products) and orders."""
import csv
import logging

from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from stores.models import Store

from core.throttles import ProgressReadRateThrottle
from store_adapters import get_adapter
from store_adapters.reverb_adapter import ReverbAPIError
from stores.credentials import marketplace_kind

from . import csv_import, export_xlsx, listing_service, order_service, photo_upload, shipping_service, ticket_service
from .errors import MarketplaceError
from .models import ListingAction, ListingStatus, ListingUpload, MarketplaceOrder, StoreListing, SupportTicket
from .photo_upload import PhotoUploadError
from .reverb import listings as reverb_listings
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

    sync_status = (request.query_params.get('sync_status') or '').strip().lower()
    if sync_status in ('pending', 'scraped', 'synced', 'failed'):
        qs = qs.filter(inventory_sync_status=sync_status)

    if request.query_params.get('errors') in ('1', 'true', 'yes'):
        qs = qs.filter(status=ListingStatus.VALIDATION_FAILED)

    search = (request.query_params.get('search') or '').strip()
    if search:
        from django.db.models import Q
        qs = qs.filter(
            Q(sku__icontains=search)
            | Q(title__icontains=search)
            | Q(external_variant_key__icontains=search)
            | Q(vendor_url__icontains=search)
        )
    return qs


class StoreListingListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get_throttles(self):
        if self.request.method == 'GET':
            return [ProgressReadRateThrottle()]
        return super().get_throttles()

    def get(self, request, store_pk):
        store = _get_store(request, store_pk)
        qs = (
            StoreListing.objects.filter(store=store, user=request.user)
            .select_related('store')
            .prefetch_related(
                'store__vendor_price_settings__range_margins__price_range',
            )
        )
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


class StoreListingPhotoUploadView(APIView):
    """Upload listing image files; returns public URLs for the photos field."""
    permission_classes = [IsAuthenticated]

    def post(self, request, store_pk):
        store = _get_store(request, store_pk)
        uploads = request.FILES.getlist('photos') or request.FILES.getlist('files')
        if not uploads and request.FILES.get('photo'):
            uploads = [request.FILES['photo']]
        try:
            results = photo_upload.save_listing_photos(request, store, uploads)
        except PhotoUploadError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({
            'photos': results,
            'urls': [r['url'] for r in results],
        }, status=status.HTTP_201_CREATED)


class StoreReverbCategoriesView(APIView):
    """Reverb category catalog for dropdown / name→UUID mapping."""
    permission_classes = [IsAuthenticated]

    def get(self, request, store_pk):
        store = _get_store(request, store_pk)
        if marketplace_kind(store.marketplace) != 'reverb':
            return Response(
                {'detail': 'Categories catalog is only available for Reverb stores.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        q = (request.query_params.get('q') or '').strip()
        try:
            adapter = get_adapter(store)
            raw = adapter.list_categories_flat()
        except (ReverbAPIError, Exception) as exc:  # noqa: BLE001
            logger.warning('Reverb categories fetch failed: %s', exc)
            return Response(
                {'detail': str(exc) or 'Could not load Reverb categories.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        categories = reverb_listings.normalize_categories_for_ui(raw, q=q)
        # Cap large unfiltered responses for the UI
        if not q and len(categories) > 500:
            categories = categories[:500]
        return Response({'categories': categories})


class StoreReverbConditionsView(APIView):
    """Reverb condition catalog for dropdown."""
    permission_classes = [IsAuthenticated]

    def get(self, request, store_pk):
        store = _get_store(request, store_pk)
        if marketplace_kind(store.marketplace) != 'reverb':
            return Response(
                {'detail': 'Conditions catalog is only available for Reverb stores.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            adapter = get_adapter(store)
            raw = adapter.list_listing_conditions()
        except (ReverbAPIError, Exception) as exc:  # noqa: BLE001
            logger.warning('Reverb conditions fetch failed: %s', exc)
            return Response(
                {'detail': str(exc) or 'Could not load Reverb conditions.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response({'conditions': reverb_listings.normalize_conditions_for_ui(raw)})


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
        store = _get_store(request, store_pk)
        action = (request.query_params.get('action') or 'create').strip().lower()
        resp = HttpResponse(
            csv_import.build_template_csv(action, store=store),
            content_type='text/csv',
        )
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
    """List managed listing activity.

    Query ``?scope=history`` (default): bulk Create/Mapped/Delete files and
    single Create/Delete. ``?scope=logs``: Edit / Publish / Push inventory and
    other non-history rows. ``?scope=all``: everything.
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [ProgressReadRateThrottle]

    def get(self, request, store_pk):
        store = _get_store(request, store_pk)
        scope = request.query_params.get('scope', 'history')
        qs = listing_service.filter_listing_uploads(
            ListingUpload.objects.filter(store=store, user=request.user),
            scope=scope,
        )
        return Response(ListingUploadSerializer(qs, many=True).data)


def _listing_upload_safe_name(upload: ListingUpload) -> str:
    name = (upload.filename or 'upload').rsplit('.', 1)[0]
    return ''.join(c if c.isalnum() or c in '-_' else '_' for c in name)[:80] or 'upload'


def _listing_upload_row_error_text(row: dict) -> str:
    errs = row.get('errors') or []
    if isinstance(errs, list):
        return '; '.join(str(e).replace('\n', ' ') for e in errs if e)
    return str(errs).replace('\n', ' ') if errs else ''


def _listing_upload_status_text(
    listing,
    *,
    row_ok: bool,
    imported: bool,
    err_text: str = '',
    upload_action: str = '',
) -> str:
    """Status cell for template-style export: Created / Mapped / Deleted / Error."""
    if not row_ok:
        reason = (err_text or 'Import failed.').strip()
        return f'Error: {reason}' if not reason.lower().startswith('error') else reason
    if listing is not None:
        if listing.status == ListingStatus.VALIDATION_FAILED:
            errs = listing.validation_errors_json or []
            if isinstance(errs, list):
                reason = '; '.join(str(e) for e in errs if e)
            else:
                reason = str(errs or 'Validation failed.')
            return f'Error: {reason}' if reason else 'Error: Validation failed.'
        if listing.status == ListingStatus.FAILED:
            return 'Error: Marketplace upload failed.'
    action = (upload_action or getattr(listing, 'action', '') or '').lower()
    if action == ListingAction.MAPPED:
        return 'Mapped'
    if action == ListingAction.DELETE:
        return 'Deleted'
    if imported or row_ok:
        return 'Created'
    return 'Skipped'


def _listing_upload_has_template_fields(rows: list) -> bool:
    return any(isinstance(r.get('fields'), dict) and r.get('fields') for r in rows)


def _listing_upload_csv_response(upload: ListingUpload, *, errors_only: bool) -> HttpResponse:
    """Export upload rows.

    Preferred format (Create/Mapped/Delete file uploads that stored full input):
      <same columns as input template> + Status

    Status values:
      Created | Mapped | Deleted | Error: <reason>

    Legacy / non-file activities fall back to Row, SKU, Status, Error Logs.
    """
    rows = upload.rows_json if isinstance(upload.rows_json, list) else []
    if errors_only:
        rows = [r for r in rows if not r.get('valid', True) or r.get('errors')]

    response = HttpResponse(content_type='text/csv')
    suffix = '_errors' if errors_only else '_export'
    safe = _listing_upload_safe_name(upload)
    response['Content-Disposition'] = f'attachment; filename="{safe}{suffix}.csv"'
    writer = csv.writer(response)

    if not rows:
        msg = (upload.message or '').replace('\n', ' ')
        writer.writerow(['Row', 'SKU', 'Status', 'Error Logs'])
        if errors_only and (upload.error_rows or upload.status == ListingUpload.Status.FAILED):
            writer.writerow(['', '', f'Error: {msg or "Upload failed"}', msg or 'Upload failed'])
        elif not errors_only:
            label = (
                f'Error: {msg}' if (upload.error_rows or upload.status == ListingUpload.Status.FAILED)
                else 'Created'
            )
            writer.writerow(['', upload.filename or '', label, msg])
        return response

    # Batch-load current listings so Status can show Uploaded vs Created.
    keys = set()
    for r in rows:
        for field in ('sku', 'variant_key'):
            val = (r.get(field) or '').strip()
            if val:
                keys.add(val)
        fields = r.get('fields') if isinstance(r.get('fields'), dict) else {}
        for field in ('sku', 'variant_key'):
            val = str(fields.get(field) or '').strip()
            if val:
                keys.add(val)
    listings_by_key = {}
    if keys:
        qs = StoreListing.objects.filter(store_id=upload.store_id).filter(
            Q(sku__in=keys) | Q(external_variant_key__in=keys)
        ).only('sku', 'external_variant_key', 'status', 'action', 'validation_errors_json')
        for listing in qs:
            for key in (listing.sku, listing.external_variant_key):
                k = (key or '').strip()
                if k:
                    listings_by_key[k] = listing

    use_template = _listing_upload_has_template_fields(rows)
    store = getattr(upload, 'store', None)
    if store is None and upload.store_id:
        store = Store.objects.filter(pk=upload.store_id).select_related('marketplace').first()
    field_specs = csv_import.export_field_specs(store) if use_template else []

    if use_template:
        writer.writerow([header for _, header in field_specs] + ['Status'])
    else:
        writer.writerow(['Row', 'SKU', 'Status', 'Error Logs'])

    for r in rows:
        fields = r.get('fields') if isinstance(r.get('fields'), dict) else {}
        sku = (r.get('sku') or fields.get('sku') or r.get('variant_key') or '').strip()
        variant = (r.get('variant_key') or fields.get('variant_key') or sku).strip()
        err_text = _listing_upload_row_error_text(r)
        row_ok = r.get('valid', True) and not err_text
        listing = listings_by_key.get(sku) or listings_by_key.get(variant)

        if listing and listing.status == ListingStatus.VALIDATION_FAILED and not err_text:
            errs = listing.validation_errors_json or []
            if isinstance(errs, list):
                err_text = '; '.join(str(e).replace('\n', ' ') for e in errs if e)
            row_ok = False
        if listing and listing.status == ListingStatus.FAILED and not err_text:
            err_text = 'Marketplace upload failed.'
            row_ok = False

        status_text = _listing_upload_status_text(
            listing,
            row_ok=row_ok,
            imported=bool(r.get('imported')),
            err_text=err_text,
            upload_action=upload.action or '',
        )

        if use_template:
            cells = []
            for key, _header in field_specs:
                val = fields.get(key, '')
                if key == 'action' and not val:
                    val = (upload.action or '').capitalize()
                if key == 'store_name' and not val and store is not None:
                    val = store.name or ''
                if key == 'marketplace_name' and not val and store is not None:
                    mp = getattr(store, 'marketplace', None)
                    val = getattr(mp, 'name', None) or ''
                if isinstance(val, bool):
                    val = 'true' if val else 'false'
                cells.append('' if val is None else str(val))
            cells.append(status_text)
            writer.writerow(cells)
        else:
            # Legacy summary format for older uploads / non-file activities.
            short = status_text
            if short.startswith('Error: '):
                short_status, short_err = 'Error', short[7:]
            else:
                short_status, short_err = short, err_text
            writer.writerow([
                r.get('row_number') or '',
                sku or variant,
                short_status,
                short_err or err_text,
            ])
    return response


class StoreListingUploadErrorFileView(APIView):
    """Download failed rows from a managed Upload history entry as CSV."""
    permission_classes = [IsAuthenticated]

    def get(self, request, store_pk, upload_id):
        store = _get_store(request, store_pk)
        upload = get_object_or_404(ListingUpload, id=upload_id, store=store, user=request.user)
        return _listing_upload_csv_response(upload, errors_only=True)


class StoreListingUploadExportView(APIView):
    """Export all rows from a managed Upload history entry as CSV."""
    permission_classes = [IsAuthenticated]

    def get(self, request, store_pk, upload_id):
        store = _get_store(request, store_pk)
        upload = get_object_or_404(ListingUpload, id=upload_id, store=store, user=request.user)
        return _listing_upload_csv_response(upload, errors_only=False)


class StoreListingUploadDeleteView(APIView):
    """Delete an Upload history entry; optionally remove listings from system / marketplace."""
    permission_classes = [IsAuthenticated]

    def delete(self, request, store_pk, upload_id):
        store = _get_store(request, store_pk)
        upload = get_object_or_404(ListingUpload, id=upload_id, store=store, user=request.user)
        # Accept JSON body or query params.
        data = request.data if isinstance(request.data, dict) else {}
        def _flag(name: str) -> bool:
            raw = data.get(name, request.query_params.get(name, False))
            if isinstance(raw, bool):
                return raw
            return str(raw).strip().lower() in ('1', 'true', 'yes', 'on')

        delete_system = _flag('delete_system')
        delete_marketplace = _flag('delete_marketplace')
        try:
            result = listing_service.delete_upload(
                request.user,
                store,
                upload,
                delete_system=delete_system,
                delete_marketplace=delete_marketplace,
            )
        except MarketplaceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result, status=status.HTTP_200_OK)


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


class StoreListingScrapeView(APIView):
    """Start managed listing scrape in the background (progress survives reload)."""
    permission_classes = [IsAuthenticated]

    def post(self, request, store_pk):
        store = _get_store(request, store_pk)
        listing_ids = request.data.get('listing_ids') or None
        try:
            result = listing_service.start_scrape_async(request.user, store, listing_ids)
        except MarketplaceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result, status=status.HTTP_200_OK)


class StoreListingScrapeProgressView(APIView):
    """Live scrape progress for managed Inventory management (processed/total).

    Counts come from listing statuses when a scrape is active (catalog-style).
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [ProgressReadRateThrottle]

    def get(self, request, store_pk):
        store = _get_store(request, store_pk)
        from . import scrape_progress as scrape_prog
        data = scrape_prog.enrich_progress_from_listings(store.id)
        data['store_id'] = str(store.id)
        return Response(data)


class StoreListingPushInventoryView(APIView):
    """Push local price/stock to marketplace for already-uploaded listings."""
    permission_classes = [IsAuthenticated]

    def post(self, request, store_pk):
        store = _get_store(request, store_pk)
        listing_ids = request.data.get('listing_ids') or None
        try:
            result = listing_service.push_inventory(request.user, store, listing_ids)
        except MarketplaceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        code = status.HTTP_200_OK if result.get('ok') else status.HTTP_502_BAD_GATEWAY
        if result.get('pushed'):
            listing_service.record_activity(
                request.user, store,
                action=ListingAction.CREATE,
                source=ListingUpload.Source.SINGLE,
                filename='Push inventory to marketplace',
                total=result.get('pushed', 0),
                success=result.get('pushed', 0),
                errors=result.get('failed', 0),
                message=result.get('message') or '',
            )
        return Response(result, status=code)


class StoreListingResetInventoryView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, store_pk):
        store = _get_store(request, store_pk)
        scope = (request.data.get('scope') or 'failed').strip().lower()
        try:
            result = listing_service.reset_inventory_status(request.user, store, scope=scope)
        except MarketplaceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


class StoreListingCriticalInventoryView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, store_pk):
        store = _get_store(request, store_pk)
        action = (request.data.get('action') or 'zero_inventory').strip().lower()
        if action != 'zero_inventory':
            return Response(
                {'detail': 'Supported action: zero_inventory'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            result = listing_service.critical_zero_inventory(request.user, store)
        except MarketplaceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        code = status.HTTP_200_OK if result.get('ok') else status.HTTP_502_BAD_GATEWAY
        return Response(result, status=code)


class StoreListingInventoryExportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, store_pk):
        store = _get_store(request, store_pk)
        sync_status = (request.query_params.get('sync_status') or '').strip()
        content = listing_service.export_inventory_xlsx(
            request.user, store, sync_status=sync_status,
        )
        resp = HttpResponse(
            content,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        resp['Content-Disposition'] = 'attachment; filename="managed_inventory.xlsx"'
        return resp


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
    """Download store orders as an Excel (.xlsx) file.

    Optional ``?status=paid`` (or any local OrderStatus value) filters rows.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, store_pk):
        store = _get_store(request, store_pk)
        qs = (
            MarketplaceOrder.objects.filter(store=store, user=request.user)
            .prefetch_related('shipments')
            .order_by('-created_at')
        )
        status_filter = (request.query_params.get('status') or '').strip().lower()
        if status_filter and status_filter not in ('all', '*'):
            qs = qs.filter(status=status_filter)
        orders = list(qs)

        content = export_xlsx.build_orders_xlsx(orders, store)
        safe_name = ''.join(c if c.isalnum() or c in '-_' else '_' for c in (store.name or 'store'))[:40]
        suffix = f'_{status_filter}' if status_filter and status_filter not in ('all', '*') else ''
        filename = f'orders_{safe_name}{suffix}.xlsx'
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
        content = export_xlsx.build_tickets_xlsx(
            tickets,
            order_id_label='Order ID' if (getattr(getattr(store, 'marketplace', None), 'code', '') or '').lower() == 'reverb' else 'Invoice',
        )
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
