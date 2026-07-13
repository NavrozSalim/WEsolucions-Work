import csv
import io
import logging
import uuid
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.db import transaction

from core.throttles import ProgressReadRateThrottle
from catalog.models import (
    ProductMapping,
    CatalogUpload,
    CatalogUploadRow,
    CatalogSyncLog,
    ReverbUpdateLog,
    CatalogActivityLog,
    HebScrapeJob,
    StoreCatalogCeleryScrapeState,
)
from catalog.celery_scrape_state import (
    clear_celery_scrape_state,
    mark_celery_scrape_worker_started,
    set_celery_scrape_state,
)
from catalog.serializers import ProductMappingSerializer, CatalogActivityLogSerializer
from catalog.pagination import CatalogProductPagination
from catalog.services import create_upload_file_and_queue
from catalog.marketplace_templates import (
    export_headers_for_store,
    sample_template_filename_for_kind,
    sample_template_rows_for_kind,
    store_marketplace_kind,
    template_kind_from_store_adapter,
    upload_row_to_cells,
)
from products.models import Product
from stores.models import Store
from rest_framework.permissions import IsAuthenticated
from audit.utils import log_action
from django.db.models import Count, Q, Prefetch, OuterRef, Subquery
from stores.models import StoreVendorPriceSettings
from vendor.models import VendorPrice

logger = logging.getLogger(__name__)


def _upload_action_reason_from_rows(rows):
    """Summarize Add / Update / Delete mix from upload rows (prefetched)."""
    from collections import Counter

    labels = []
    for r in rows:
        raw = (r.action_raw or 'Add').strip().lower()
        if 'delete' in raw:
            labels.append('Delete')
        elif 'update' in raw:
            labels.append('Update')
        else:
            labels.append('Add')
    if not labels:
        return '—'
    cnt = Counter(labels)
    if len(cnt) == 1:
        return list(cnt.keys())[0]
    return ', '.join(f'{k} ({v})' for k, v in sorted(cnt.items(), key=lambda x: (-x[1], x[0])))


class CatalogStoresView(APIView):
    """List user's stores with product count. Optional filter: marketplace_id."""
    permission_classes = [IsAuthenticated]
    throttle_classes = [ProgressReadRateThrottle]

    def get(self, request):
        from sync.models import SyncSchedule
        from stores.models import StorePriceRangeMargin

        stores = (
            Store.objects.filter(user=request.user)
            .defer('api_token', 'kogan_service_account_json')
            .select_related('marketplace')
            .annotate(
                product_count=Count('products', filter=Q(products__is_active=True)),
            )
            .order_by('name')
        )
        marketplace_id = request.query_params.get('marketplace_id')
        if marketplace_id:
            stores = stores.filter(marketplace_id=marketplace_id)
        store_ids = [s.id for s in stores]
        sched_map = {
            str(s.store_id): s
            for s in SyncSchedule.objects.filter(store_id__in=store_ids)
        }
        # Stores that have at least one fixed-margin tier on any vendor —
        # those stores need the catalog UI to surface the Pack QTY / Prep
        # Fees / Shipping Fees columns.
        fixed_tier_store_ids = set(
            StorePriceRangeMargin.objects
            .filter(
                price_settings__store_id__in=store_ids,
                margin_type='fixed',
            )
            .values_list('price_settings__store_id', flat=True)
            .distinct()
        )
        from catalog.mydeal_templates import store_is_mydeal, template_status

        mydeal_store_ids = [
            s.id for s in stores
            if store_is_mydeal(s)
        ]
        mydeal_status_map = {}
        if mydeal_store_ids:
            from catalog.models import MydealTemplateRow
            for sid in mydeal_store_ids:
                st = next(x for x in stores if x.id == sid)
                mydeal_status_map[str(sid)] = template_status(st)

        data = []
        for s in stores:
            sch = sched_map.get(str(s.id))
            row = {
                'id': str(s.id),
                'name': s.name,
                'marketplace_id': str(s.marketplace_id) if s.marketplace_id else None,
                'marketplace_name': s.marketplace.name if s.marketplace else None,
                'marketplace_code': (s.marketplace.code or '').strip() if s.marketplace else None,
                'management_mode': s.management_mode,
                'product_count': s.product_count,
                'schedule_active': sch.is_active if sch else None,
                'has_fixed_tier': s.id in fixed_tier_store_ids,
            }
            if store_is_mydeal(s):
                row['mydeal_setup_method'] = getattr(s, 'mydeal_setup_method', None) or 'upload'
                row['mydeal_templates'] = mydeal_status_map.get(str(s.id), {})
            data.append(row)
        return Response(data)


class ProductMappingViewSet(viewsets.ModelViewSet):
    serializer_class = ProductMappingSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = CatalogProductPagination

    def get_queryset(self):
        store_id = self.kwargs.get('store_pk')
        if not store_id:
            return ProductMapping.objects.none()
        latest_vp = VendorPrice.objects.filter(
            product=OuterRef('product_id')
        ).order_by('-scraped_at')
        qs = ProductMapping.objects.filter(is_active=True).select_related(
            'product',
            'product__vendor',
            'store',
        ).annotate(
            latest_vendor_price=Subquery(latest_vp.values('price')[:1]),
            latest_vendor_stock=Subquery(latest_vp.values('stock')[:1]),
            latest_vendor_at=Subquery(latest_vp.values('scraped_at')[:1]),
        ).prefetch_related(
            Prefetch(
                'store__vendor_price_settings',
                queryset=StoreVendorPriceSettings.objects.prefetch_related(
                    'range_margins__price_range',
                ),
            ),
        )
        if store_id:
            qs = qs.filter(store_id=store_id, store__user=self.request.user)
        else:
            qs = qs.filter(store__user=self.request.user)

        # Server-side filters so the catalog page doesn't have to load every
        # row just to search / filter. Used by the frontend when the user
        # types in the search box or picks a sync-status pill.
        params = getattr(self.request, 'query_params', None) or {}
        status_filter = (params.get('sync_status') or '').strip()
        if status_filter:
            qs = qs.filter(sync_status=status_filter)

        search = (params.get('q') or params.get('search') or '').strip()
        if search:
            qs = qs.filter(
                Q(product__vendor_sku__icontains=search)
                | Q(marketplace_child_sku__icontains=search)
                | Q(marketplace_parent_sku__icontains=search)
                | Q(title__icontains=search)
                | Q(product__vendor__name__icontains=search)
                | Q(product__vendor__code__icontains=search)
            )

        # Deterministic ordering so client-side pagination never shows the
        # same row twice across pages.
        return qs.order_by('product__vendor_sku', 'id')

    @action(detail=True, methods=['post'])
    def reset_sync_status(self, request, store_pk=None, pk=None):
        """Reset failed_sync_count, sync_status and scrape_error so the next
        catalog scrape retries this product cleanly. Does **not** trigger a
        scrape on its own — the UI should follow up with the standard
        'Scrape data' flow."""
        pm = self.get_object()
        pm.failed_sync_count = 0
        pm.sync_status = 'pending'
        pm.scrape_error = None
        pm.save(update_fields=['failed_sync_count', 'sync_status', 'scrape_error'])
        return Response({'status': 'reset', 'message': f'Ready to retry sync for {pm.product.vendor_sku}'})

    @action(detail=False, methods=['get'])
    def export(self, request, store_pk=None):
        """Download product mappings as CSV. Optional ?sync_status=failed|synced|..."""
        store = get_object_or_404(Store, id=store_pk, user=request.user)
        qs = ProductMapping.objects.filter(store=store, is_active=True).select_related(
            'product', 'product__vendor',
        ).prefetch_related('product__vendor_prices').order_by('product__vendor_sku')
        st = (request.query_params.get('sync_status') or '').strip()
        if st:
            qs = qs.filter(sync_status=st)
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="catalog_products_{store_pk}.csv"'
        writer = csv.writer(response)
        writer.writerow([
            'SKU', 'Title', 'Vendor', 'Vendor URL', 'Vendor price', 'Vendor inventory', 'Store price', 'Store stock',
            'Sync status', 'Marketplace ID', 'Last sync', 'Last scrape',
        ])
        for pm in qs:
            sku = (
                pm.marketplace_child_sku
                or pm.marketplace_parent_sku
                or (pm.product.vendor_sku if pm.product else '')
            )
            vp = None
            if pm.product_id:
                vp = pm.product.vendor_prices.order_by('-scraped_at').first()
            vprice = ''
            vinventory = ''
            if vp and vp.price is not None:
                vprice = str(vp.price)
            if vp and vp.stock is not None:
                vinventory = str(vp.stock)
            writer.writerow([
                sku or '',
                (pm.title or '')[:500],
                pm.product.vendor.name if pm.product and pm.product.vendor else '',
                pm.product.vendor_url if pm.product else '',
                vprice,
                vinventory,
                str(pm.store_price) if pm.store_price is not None else '',
                pm.store_stock if pm.store_stock is not None else '',
                pm.sync_status or '',
                pm.marketplace_id or '',
                pm.last_sync_time.isoformat() if pm.last_sync_time else '',
                pm.last_scrape_time.isoformat() if pm.last_scrape_time else '',
            ])
        return response

    def perform_destroy(self, instance):
        sku = instance.product.vendor_sku
        store_id = str(instance.store_id)
        log_action(
            self.request.user, 'product_deleted', 'product_mapping', str(instance.id),
            metadata={'sku': sku, 'store_id': store_id}, request=self.request
        )
        instance.delete()


class CatalogClearView(APIView):
    """Delete all products (catalog) for a store."""
    permission_classes = [IsAuthenticated]

    def delete(self, request, store_pk):
        try:
            store = Store.objects.get(id=store_pk, user=request.user)
        except Store.DoesNotExist:
            return Response({"error": "Store not found"}, status=status.HTTP_404_NOT_FOUND)
        count, _ = ProductMapping.objects.filter(store=store).delete()
        log_action(
            request.user, 'catalog_cleared', 'store', str(store.id),
            metadata={'name': store.name, 'deleted_count': count}, request=request
        )
        return Response({"message": f"Deleted {count} product(s)."}, status=status.HTTP_200_OK)


def _parse_upload_rows(file_obj, filename):
    """Parse XLSX or CSV and return list of rows (legacy, for backward compat)."""
    from catalog.services import parse_upload_file
    return parse_upload_file(file_obj, filename)


class StoreCatalogUploadView(APIView):
    """
    Store-scoped catalog upload. Creates CatalogUpload + CatalogUploadRow.
    Sync step applies Add/Update/Delete. Preserves raw values including 'N/A'.
    """
    permission_classes = [IsAuthenticated]
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request, store_pk):
        store = get_object_or_404(
            Store.objects.defer('api_token', 'kogan_service_account_json'),
            id=store_pk,
            user=request.user,
        )
        file_obj = request.data.get('file')
        if not file_obj:
            return Response({"error": "No file provided"}, status=status.HTTP_400_BAD_REQUEST)

        filename = getattr(file_obj, 'name', 'upload.csv')
        upload, err = create_upload_file_and_queue(
            user=request.user,
            store=store,
            file_obj=file_obj,
            filename=filename,
        )
        if upload is None:
            return Response({"error": err or "Upload failed"}, status=status.HTTP_400_BAD_REQUEST)

        from catalog.tasks import catalog_ingest_upload_file_task

        try:
            catalog_ingest_upload_file_task.delay(str(upload.id))
        except Exception as enqueue_exc:
            logger.exception('Failed to enqueue catalog ingest for upload %s', upload.id)
            upload.status = CatalogUpload.Status.FAILED
            upload.error_summary = (
                f'Could not queue file processing ({enqueue_exc!s}). '
                'Confirm Celery workers listen to the ingest queue and Redis is reachable, then retry.'
            )[:2000]
            upload.save(update_fields=['status', 'error_summary'])
            return Response(
                {
                    'error': upload.error_summary,
                    'upload_id': str(upload.id),
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response({
            "upload_id": str(upload.id),
            "total_rows": upload.total_rows,
            "status": upload.status,
            "message": "File received; row ingest is running in the background. Use upload detail or list to see progress.",
        }, status=status.HTTP_202_ACCEPTED)


class CatalogUploadListView(APIView):
    """List catalog uploads for a store (upload history)."""
    permission_classes = [IsAuthenticated]

    def get(self, request, store_pk):
        store = get_object_or_404(
            Store.objects.defer('api_token', 'kogan_service_account_json'),
            id=store_pk,
            user=request.user,
        )
        uploads = (
            CatalogUpload.objects.filter(store=store)
            .select_related('user', 'store', 'store__marketplace')
            .defer('store__api_token', 'store__kogan_service_account_json')
            .prefetch_related(
                Prefetch('rows', queryset=CatalogUploadRow.objects.only('action_raw')),
            )
            .order_by('-created_at')[:50]
        )
        data = []
        for u in uploads:
            vendor_raw = list(
                CatalogUploadRow.objects.filter(catalog_upload=u)
                .values_list('vendor_name_raw', flat=True)
                .distinct()
            )
            vendor_source = next((x for x in vendor_raw if x), None)
            has_errors = (
                u.status in (CatalogUpload.Status.FAILED, CatalogUpload.Status.PARTIAL)
                or bool(u.error_summary)
                or u.rows.filter(sync_status=CatalogUploadRow.SyncStatus.ERROR).exists()
            )
            error_row_count = u.rows.filter(sync_status=CatalogUploadRow.SyncStatus.ERROR).count() if has_errors else 0
            data.append({
                "id": str(u.id),
                "original_filename": u.original_filename,
                "total_rows": u.total_rows,
                "processed_rows": u.processed_rows,
                "status": u.status,
                "error_summary": u.error_summary,
                "created_at": u.created_at.isoformat() if u.created_at else None,
                "user_name": getattr(u.user, 'email', None) or getattr(u.user, 'username', None) if u.user else None,
                "marketplace": store.marketplace.name if store.marketplace else None,
                "vendor_source": vendor_source,
                "has_errors": has_errors,
                "error_row_count": error_row_count,
                "reason": _upload_action_reason_from_rows(u.rows.all()),
            })
        return Response(data)


class CatalogUploadDeleteView(APIView):
    """Delete a catalog upload and all product mappings created from it."""
    permission_classes = [IsAuthenticated]

    def delete(self, request, store_pk, upload_id):
        store = get_object_or_404(Store, id=store_pk, user=request.user)
        upload = get_object_or_404(CatalogUpload, id=upload_id, store=store)
        rows = list(upload.rows.select_related('product_mapping', 'product').all())
        pm_ids = {r.product_mapping_id for r in rows if r.product_mapping_id}
        product_ids = {r.product_id for r in rows if r.product_id}
        mapping_filter = Q()
        if pm_ids:
            mapping_filter |= Q(pk__in=pm_ids)
        if product_ids:
            mapping_filter |= Q(product_id__in=product_ids)
        deleted_count = 0
        if pm_ids or product_ids:
            qs = ProductMapping.objects.filter(store=store).filter(mapping_filter)
            deleted_count = qs.count()
            qs.delete()
        upload.delete()
        log_action(
            request.user, 'catalog_upload_deleted', 'catalog_upload', str(upload_id),
            metadata={'store_id': str(store.id), 'deleted_mappings': deleted_count}, request=request
        )
        return Response({"message": "Upload and linked product mappings deleted."}, status=status.HTTP_200_OK)


class CatalogUploadErrorFileView(APIView):
    """Download failed rows as CSV in the same format as the original upload + Error Logs column."""
    permission_classes = [IsAuthenticated]

    def get(self, request, store_pk, upload_id):
        store = get_object_or_404(Store, id=store_pk, user=request.user)
        upload = get_object_or_404(CatalogUpload, id=upload_id, store=store)
        failed_rows = upload.rows.filter(
            sync_status=CatalogUploadRow.SyncStatus.ERROR,
        ).order_by('row_number')
        response = HttpResponse(content_type='text/csv')
        safe_name = (upload.original_filename or 'upload').rsplit('.', 1)[0]
        response['Content-Disposition'] = f'attachment; filename="{safe_name}_errors.csv"'
        writer = csv.writer(response)
        hdr = export_headers_for_store(store)
        writer.writerow([*hdr, 'Error Logs'])
        for r in failed_rows:
            cells = upload_row_to_cells(r, store)
            writer.writerow([*cells, (r.sync_error or '').replace('\n', ' ')])
        if not failed_rows.exists() and upload.error_summary:
            writer.writerow([''] * len(hdr) + [upload.error_summary.replace('\n', ' ')])
        return response


class CatalogUploadDetailView(APIView):
    """Get upload detail with paginated rows. Pass ?action=download to get original file as CSV."""
    permission_classes = [IsAuthenticated]

    def get(self, request, store_pk, upload_id):
        store = get_object_or_404(Store, id=store_pk, user=request.user)
        upload = get_object_or_404(CatalogUpload, id=upload_id, store=store)

        if request.query_params.get('action') == 'download':
            return self._download_csv(upload)

        page = int(request.query_params.get('page', 1))
        per_page = min(int(request.query_params.get('per_page', 50)), 200)
        offset = (page - 1) * per_page

        rows = upload.rows.all()[offset : offset + per_page]
        data = {
            "id": str(upload.id),
            "original_filename": upload.original_filename,
            "total_rows": upload.total_rows,
            "processed_rows": upload.processed_rows,
            "status": upload.status,
            "error_summary": upload.error_summary,
            "created_at": upload.created_at.isoformat() if upload.created_at else None,
            "rows": [
                {
                    "id": str(r.id),
                    "row_number": r.row_number,
                    "vendor_name_raw": r.vendor_name_raw,
                    "marketplace_child_sku_raw": r.marketplace_child_sku_raw,
                    "marketplace_id_raw": r.marketplace_id_raw,
                    "vendor_sku_raw": r.vendor_sku_raw,
                    "action_raw": r.action_raw,
                    "sync_status": r.sync_status,
                }
                for r in rows
            ],
        }
        return Response(data)

    @staticmethod
    def _download_csv(upload):
        store = upload.store
        rows = upload.rows.select_related('product_mapping').order_by('row_number')
        response = HttpResponse(content_type='text/csv')
        safe_name = (upload.original_filename or 'catalog').rsplit('.', 1)[0]
        response['Content-Disposition'] = f'attachment; filename="{safe_name}.csv"'
        writer = csv.writer(response)
        hdr = export_headers_for_store(store, include_posted=True)
        writer.writerow(hdr)
        for r in rows:
            pm = r.product_mapping
            posted_price = ''
            posted_inventory = ''
            if pm:
                posted_price = str(pm.store_price) if pm.store_price is not None else ''
                posted_inventory = str(pm.store_stock) if pm.store_stock is not None else ''
            cells = upload_row_to_cells(
                r,
                store,
                include_posted=True,
                posted_price=posted_price,
                posted_inventory=posted_inventory,
            )
            writer.writerow(cells)
        return response


class CatalogUploadView(APIView):
    """
    Legacy global catalog upload (no store scope). Kept for backward compat.
    Prefer StoreCatalogUploadView for new Reverb workflow.
    """
    permission_classes = [IsAuthenticated]
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request, *args, **kwargs):
        return Response(
            {"error": "Use POST /api/v1/stores/{store_id}/catalog/upload/ for catalog upload"},
            status=status.HTTP_400_BAD_REQUEST,
        )


class CatalogSyncTriggerView(APIView):
    """Trigger catalog sync (background job)."""
    permission_classes = [IsAuthenticated]

    def post(self, request, store_pk):
        from catalog.tasks import catalog_sync_task
        store = get_object_or_404(Store, id=store_pk, user=request.user)
        upload_id = request.data.get('upload_id')
        if upload_id:
            upload = get_object_or_404(CatalogUpload, id=upload_id, store=store)
        else:
            upload = (
                CatalogUpload.objects.filter(
                    store=store,
                    status=CatalogUpload.Status.VALIDATED,
                )
                .order_by('-created_at')
                .first()
            )
        if not upload:
            return Response(
                {
                    "error": (
                        "No validated upload found. Provide upload_id or wait until file ingest "
                        "finishes and status is validated."
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if upload.status == CatalogUpload.Status.INGESTING:
            return Response(
                {
                    "error": "File ingest is still running. Try again when status is validated.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        run_inline = request.data.get('run_inline') or request.query_params.get('inline') == '1'
        auto_scrape = request.data.get('auto_scrape', True)
        if isinstance(auto_scrape, str):
            auto_scrape = auto_scrape.lower() in ('1', 'true', 'yes')
        replace_store_catalog = request.data.get('replace_store_catalog', False)
        if isinstance(replace_store_catalog, str):
            replace_store_catalog = replace_store_catalog.lower() in ('1', 'true', 'yes')

        if run_inline:
            from catalog.tasks import run_catalog_sync, run_catalog_scrape
            result = run_catalog_sync(
                str(upload.id),
                replace_store_catalog=bool(replace_store_catalog),
            )
            upload.refresh_from_db()
            if auto_scrape and upload.status in (
                CatalogUpload.Status.SYNCED,
                CatalogUpload.Status.PARTIAL,
            ):
                try:
                    scrape_result = run_catalog_scrape(str(upload.id))
                    result['scrape'] = scrape_result
                except Exception as scrape_exc:
                    result['scrape'] = {'error': str(scrape_exc)}
            elif auto_scrape:
                result['scrape'] = {'skipped': True, 'reason': f'upload_status_{upload.status}'}
            return Response(result, status=status.HTTP_200_OK)

        if auto_scrape:
            from catalog.tasks import catalog_scrape_task
            async_result = catalog_sync_task.apply_async(
                args=[str(upload.id)],
                kwargs={'replace_store_catalog': bool(replace_store_catalog)},
                link=catalog_scrape_task.si(str(upload.id)),
            )
            return Response({
                "job_id": async_result.id,
                "upload_id": str(upload.id),
                "status": "queued",
                "scrape_after_sync": True,
            }, status=status.HTTP_202_ACCEPTED)

        task = catalog_sync_task.delay(
            str(upload.id),
            replace_store_catalog=bool(replace_store_catalog),
        )
        return Response({
            "job_id": task.id,
            "upload_id": str(upload.id),
            "status": "queued",
            "replace_store_catalog": bool(replace_store_catalog),
        }, status=status.HTTP_202_ACCEPTED)


def _vendor_db_ids_for(vendor_code: str) -> list:
    """Resolve a desktop-runner ``vendor_code`` (e.g. 'heb', 'costco') into
    the matching ``Vendor.id`` list in the DB. Uses the registry declared in
    ``catalog.ingest_views.SUPPORTED_VENDORS`` so the catalog + ingest layers
    stay in sync.
    """
    from catalog.ingest_views import SUPPORTED_VENDORS
    from vendor.models import Vendor

    cfg = SUPPORTED_VENDORS.get(vendor_code)
    if not cfg:
        return []
    codes = list(cfg.get('vendor_db_codes') or [])
    prefix = cfg.get('vendor_db_code_prefix')
    q = Q(code__in=codes) if codes else Q()
    if prefix:
        q = q | Q(code__istartswith=prefix)
    return list(Vendor.objects.filter(q).values_list('id', flat=True))


def _store_has_vendor_products(store, vendor_code: str) -> bool:
    """True when ``store`` has at least one active ProductMapping whose product
    belongs to a desktop-runner vendor identified by ``vendor_code``."""
    vendor_ids = _vendor_db_ids_for(vendor_code)
    if not vendor_ids:
        return False
    return ProductMapping.objects.filter(
        store=store,
        is_active=True,
        product__vendor_id__in=vendor_ids,
    ).exists()


def _store_has_pending_vendor_products(store, vendor_code: str) -> bool:
    """True when ``store`` has at least one active pending listing for ``vendor_code``."""
    vendor_ids = _vendor_db_ids_for(vendor_code)
    if not vendor_ids:
        return False
    return ProductMapping.objects.filter(
        store=store,
        is_active=True,
        sync_status='pending',
        product__vendor_id__in=vendor_ids,
    ).exists()


def _store_has_heb_products(store) -> bool:
    """Legacy alias retained for any external callers. Prefer
    ``_store_has_vendor_products(store, 'heb')`` in new code."""
    return _store_has_vendor_products(store, 'heb')


def _dispatch_server_vendor_job(vendor_code: str, store, job) -> None:
    """Fire the Celery task for a server-side vendor (e.g. VevorAU) and flip
    the tracking ``HebScrapeJob`` row into the ``CLAIMED`` state so the UI
    progress strip shows "running" instead of staying in "queued" forever.
    """
    from django.utils import timezone as _tz

    if vendor_code == 'vevor':
        try:
            from catalog.tasks import vevor_au_ingest_task
            vevor_au_ingest_task.delay(str(store.id), str(job.id))
            job.status = HebScrapeJob.Status.CLAIMED
            job.claimed_at = _tz.now()
            job.save(update_fields=['status', 'claimed_at'])
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                'Failed to dispatch VevorAU Celery task for job %s', job.id,
            )


class CatalogScrapeTriggerView(APIView):
    """Trigger catalog scrape (fetch vendor price/stock, apply rules)."""
    permission_classes = [IsAuthenticated]

    @staticmethod
    def _maybe_enqueue_vendor_job(store, user, vendor_code: str) -> HebScrapeJob | None:
        """Create (or return existing) ``HebScrapeJob`` row for ``vendor_code``
        if ``store`` actually has products for that vendor.

        For vendors whose ``runner`` is ``'server'`` (e.g. VevorAU), the Celery
        task is dispatched here so the job starts immediately instead of
        waiting for a desktop poller to claim it.

        Returns ``None`` if the store has nothing for this vendor — callers
        should silently skip that vendor in that case.
        """
        from catalog.ingest_views import SUPPORTED_VENDORS

        if not _store_has_vendor_products(store, vendor_code):
            return None
        existing = HebScrapeJob.objects.filter(
            store=store,
            vendor_code=vendor_code,
            status__in=[HebScrapeJob.Status.PENDING, HebScrapeJob.Status.CLAIMED],
        ).order_by('-requested_at').first()
        if existing:
            return existing
        job = HebScrapeJob.objects.create(
            store=store,
            requested_by=user,
            vendor_code=vendor_code,
        )
        runner = (SUPPORTED_VENDORS.get(vendor_code) or {}).get('runner', 'desktop')
        if runner == 'server':
            _dispatch_server_vendor_job(vendor_code, store, job)
        return job

    @classmethod
    def _maybe_enqueue_desktop_jobs(cls, store, user) -> list:
        """Walk every supported non-live runner vendor and enqueue a job for
        each one that has products in ``store``. Returns a list of
        ``(vendor_code, job)`` tuples for the ones that got queued (new or
        pre-existing pending/claimed).

        Vendors with ``runner='live'`` (e.g. Costco AU when residential proxies
        are configured) are intentionally skipped — they're scraped through the
        same ``catalog_scrape_store_task`` browser path as Amazon/eBay and don't
        need their own ``HebScrapeJob`` row.
        """
        from catalog.ingest_views import SUPPORTED_VENDORS
        jobs: list = []
        for vendor_code, cfg in SUPPORTED_VENDORS.items():
            if cls._vendor_runs_live(vendor_code, cfg):
                continue
            job = cls._maybe_enqueue_vendor_job(store, user, vendor_code)
            if job is not None:
                jobs.append((vendor_code, job))
        return jobs

    @staticmethod
    def _vendor_runs_live(vendor_code: str, cfg: dict) -> bool:
        """True when a vendor is currently routed through the live server-scrape path.

        Static ``runner='live'`` always counts. ``runner='desktop'`` for Costco
        also counts when residential proxies are configured, because the server
        scrape path takes over and a desktop job would never get claimed.
        """
        runner = (cfg or {}).get('runner', 'desktop')
        if runner == 'live':
            return True
        if vendor_code == 'costco':
            try:
                from scrapers.costco_au_proxies import load_proxy_urls
                return bool(load_proxy_urls())
            except Exception:
                return False
        return False

    @classmethod
    def _maybe_enqueue_heb_job(cls, store, user) -> HebScrapeJob | None:
        """Backward-compat shim for the HEB-specific helper."""
        return cls._maybe_enqueue_vendor_job(store, user, 'heb')

    @staticmethod
    def _reject_if_server_scrape_active(store) -> Response | None:
        """Block a second server-side catalog scrape while one is queued/running (same store).

        If Celery state was left behind with no worker start for 30+ minutes, clear it
        and allow a new scrape (recovery from crashed worker).
        """
        from datetime import timedelta

        st = StoreCatalogCeleryScrapeState.objects.filter(store=store).first()
        if not st:
            return None
        now = timezone.now()
        if st.first_worker_started_at is None and (now - st.enqueued_at) > timedelta(minutes=30):
            clear_celery_scrape_state(str(store.id))
            return None
        return Response(
            {
                'error': 'catalog_scrape_already_running',
                'detail': (
                    'A vendor scrape is already queued or running for this store. Wait for it '
                    'to finish, check Activity, or use Stop scraping and try again.'
                ),
                'active_task_id': (st.root_task_id or '')[:128],
            },
            status=status.HTTP_409_CONFLICT,
        )

    def post(self, request, store_pk):
        from catalog.activity_log import append_catalog_log
        from catalog.tasks import (
            catalog_scrape_store_task,
            catalog_scrape_task,
            run_catalog_scrape,
            run_store_wide_catalog_scrape,
            store_has_scrapeable_pending_mappings,
        )
        store = get_object_or_404(Store, id=store_pk, user=request.user)
        dup = self._reject_if_server_scrape_active(store)
        if dup is not None:
            append_catalog_log(
                store.id,
                'Vendor scrape not started: one is already queued or running for this store.',
                action_type='scrape_rejected_duplicate',
                user_id=request.user.id,
            )
            return dup
        append_catalog_log(
            store.id,
            'You requested a vendor scrape from the catalog page.',
            action_type='user_action',
            user_id=request.user.id,
        )

        desktop_jobs_payload: list[dict] = []

        def _serialize_desktop_jobs(jobs: list) -> list[dict]:
            return [
                {
                    'vendor': vendor_code,
                    'job_id': str(vendor_job.id),
                    'status': vendor_job.status,
                }
                for vendor_code, vendor_job in jobs
            ]

        def log_desktop_vendor_jobs() -> list:
            """HEB/Costco desktop runner jobs — fast DB inserts only."""
            desktop_jobs = self._maybe_enqueue_desktop_jobs(store, request.user)
            for vendor_code, vendor_job in desktop_jobs:
                append_catalog_log(
                    store.id,
                    f'Queued {vendor_code.upper()} scrape job {vendor_job.id} for the desktop runner.',
                    action_type=f'{vendor_code}_scrape_queued',
                    user_id=request.user.id,
                    metadata={'job_id': str(vendor_job.id), 'vendor': vendor_code},
                )
            return desktop_jobs

        from django.db import close_old_connections

        def schedule_desktop_jobs_after_commit():
            """Enqueue desktop jobs on commit (sync) so progress API sees them immediately."""

            def run_desktop():
                close_old_connections()
                try:
                    jobs = log_desktop_vendor_jobs()
                    desktop_jobs_payload.extend(_serialize_desktop_jobs(jobs))
                except Exception:
                    logger.exception('Catalog scrape: enqueue desktop runner jobs failed after commit')
                finally:
                    close_old_connections()

            transaction.on_commit(run_desktop)

        upload_id = request.data.get('upload_id')
        scope_upload = (request.data.get('scope') or '').strip().lower() == 'upload'

        run_inline = request.data.get('run_inline') or request.query_params.get('inline') == '1'

        if upload_id:
            upload = get_object_or_404(CatalogUpload, id=upload_id, store=store)
            if run_inline:
                inline_jobs = log_desktop_vendor_jobs()
                result = run_catalog_scrape(str(upload.id))
                if result.get('error'):
                    return Response(
                        {'detail': result['error'], **result},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    )
                result['desktop_jobs'] = _serialize_desktop_jobs(inline_jobs)
                return Response(result, status=status.HTTP_200_OK)
            celery_task_id = str(uuid.uuid4())
            with transaction.atomic():
                set_celery_scrape_state(
                    store,
                    task_id=celery_task_id,
                    scope=StoreCatalogCeleryScrapeState.Scope.UPLOAD,
                    upload=upload,
                )
                mark_celery_scrape_worker_started(str(store.id))
                schedule_desktop_jobs_after_commit()
            try:
                catalog_scrape_task.apply_async(
                    args=[str(upload.id)],
                    task_id=celery_task_id,
                )
            except Exception:
                clear_celery_scrape_state(str(store.id))
                raise
            return Response({
                "job_id": celery_task_id,
                "upload_id": str(upload.id),
                "scope": "upload",
                "status": "accepted",
                "desktop_jobs": desktop_jobs_payload,
            }, status=status.HTTP_202_ACCEPTED)

        if scope_upload:
            upload = (
                CatalogUpload.objects.filter(
                    store=store,
                    status__in=[CatalogUpload.Status.SYNCED, CatalogUpload.Status.PARTIAL],
                )
                .order_by('-created_at')
                .first()
            )
            if not upload:
                return Response(
                    {"error": "No synced upload found. Run sync first, or omit scope=upload for full-store scrape."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if run_inline:
                inline_jobs = log_desktop_vendor_jobs()
                result = run_catalog_scrape(str(upload.id))
                if result.get('error'):
                    return Response(
                        {'detail': result['error'], **result},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    )
                result['desktop_jobs'] = _serialize_desktop_jobs(inline_jobs)
                return Response(result, status=status.HTTP_200_OK)
            celery_task_id = str(uuid.uuid4())
            with transaction.atomic():
                set_celery_scrape_state(
                    store,
                    task_id=celery_task_id,
                    scope=StoreCatalogCeleryScrapeState.Scope.UPLOAD,
                    upload=upload,
                )
                mark_celery_scrape_worker_started(str(store.id))
                schedule_desktop_jobs_after_commit()
            try:
                catalog_scrape_task.apply_async(
                    args=[str(upload.id)],
                    task_id=celery_task_id,
                )
            except Exception:
                clear_celery_scrape_state(str(store.id))
                raise
            return Response({
                "job_id": celery_task_id,
                "upload_id": str(upload.id),
                "scope": "upload",
                "status": "accepted",
                "desktop_jobs": desktop_jobs_payload,
            }, status=status.HTTP_202_ACCEPTED)

        # Default: all active ProductMappings (same scrape path as scheduled store update)
        if run_inline and not settings.CATALOG_ALLOW_INLINE_STORE_WIDE_SCRAPE:
            return Response(
                {
                    "error": "Store-wide vendor scrape cannot run synchronously in the web worker.",
                    "detail": (
                        "Use the background queue (Celery). For local debugging only, set DEBUG=True "
                        "or CATALOG_ALLOW_INLINE_STORE_WIDE_SCRAPE=1."
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if run_inline:
            inline_jobs = log_desktop_vendor_jobs()
            result = run_store_wide_catalog_scrape(str(store.id))
            if result.get('error'):
                return Response(
                    {'detail': result['error'], **result},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            result['desktop_jobs'] = _serialize_desktop_jobs(inline_jobs)
            return Response(result, status=status.HTTP_200_OK)
        celery_task_id = str(uuid.uuid4())
        has_browser_scrape = store_has_scrapeable_pending_mappings(store)
        with transaction.atomic():
            if has_browser_scrape:
                set_celery_scrape_state(
                    store,
                    task_id=celery_task_id,
                    scope=StoreCatalogCeleryScrapeState.Scope.STORE,
                    upload=None,
                )
                mark_celery_scrape_worker_started(str(store.id))
            schedule_desktop_jobs_after_commit()
        if not has_browser_scrape:
            append_catalog_log(
                store.id,
                'Browser scrape skipped (no pending Amazon/eBay listings). '
                'Feed vendors (VevorAU, etc.) were queued separately.',
                action_type='scrape_start',
                user_id=request.user.id,
                metadata={'scope': 'store', 'browser_scrape': False},
            )
            return Response({
                "job_id": celery_task_id,
                "scope": "store",
                "status": "accepted",
                "browser_scrape": False,
                "desktop_jobs": desktop_jobs_payload,
                "message": "Feed/desktop vendors queued; no browser scrape needed.",
            }, status=status.HTTP_202_ACCEPTED)
        try:
            catalog_scrape_store_task.apply_async(
                args=[str(store.id)],
                task_id=celery_task_id,
            )
        except Exception:
            clear_celery_scrape_state(str(store.id))
            raise
        return Response({
            "job_id": celery_task_id,
            "scope": "store",
            "status": "accepted",
            "desktop_jobs": desktop_jobs_payload,
        }, status=status.HTTP_202_ACCEPTED)


class CatalogScrapeCancelView(APIView):
    """Stop desktop vendor jobs (HEB, etc.) and/or server-side catalog scrapes for a store."""

    permission_classes = [IsAuthenticated]

    def post(self, request, store_pk):
        from catalog.activity_log import append_catalog_log
        from catalog.ingest_views import SUPPORTED_VENDORS

        store = get_object_or_404(Store, id=store_pk, user=request.user)

        vendor_filter = (request.query_params.get('vendor') or '').strip().lower()
        if vendor_filter and vendor_filter not in SUPPORTED_VENDORS:
            return Response(
                {'error': f'Unknown vendor "{vendor_filter}".'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        qs = HebScrapeJob.objects.filter(
            store=store,
            status__in=[
                HebScrapeJob.Status.PENDING,
                HebScrapeJob.Status.CLAIMED,
            ],
        ).order_by('-requested_at')
        if vendor_filter:
            qs = qs.filter(vendor_code=vendor_filter)

        jobs = list(qs)
        now = timezone.now()
        from catalog.scrape_progress import invalidate_scrape_progress_cache

        invalidate_scrape_progress_cache(str(store.id))
        cancelled_payload = []
        for job in jobs:
            prior_status = job.status
            job.status = HebScrapeJob.Status.CANCELLED
            job.completed_at = now
            job.note = (job.note or '') + (f'\nCancelled by user @ {now.isoformat()}').strip()
            job.save(update_fields=['status', 'completed_at', 'note'])

            append_catalog_log(
                store.id,
                f'You stopped the {job.vendor_code.upper()} price check.',
                action_type=f'{job.vendor_code}_scrape_cancelled',
                user_id=request.user.id,
                metadata={
                    'job_id': str(job.id),
                    'prior_status': prior_status,
                    'vendor': job.vendor_code,
                },
            )
            cancelled_payload.append({
                'job_id': str(job.id),
                'vendor': job.vendor_code,
                'prior_status': prior_status,
                'status': job.status,
                'completed_at': now.isoformat(),
            })

        server_stopped = False
        resume_scope = StoreCatalogCeleryScrapeState.Scope.STORE
        resume_upload_id = None
        st = StoreCatalogCeleryScrapeState.objects.filter(store=store).first()
        if st:
            server_stopped = True
            resume_scope = st.scope
            resume_upload_id = str(st.upload_id) if st.upload_id else None
            root_tid = (st.root_task_id or '').strip()
            if root_tid:
                try:
                    from core.celery import app

                    app.control.revoke(root_tid, terminate=True, signal='SIGTERM')
                except Exception:
                    pass
            clear_celery_scrape_state(str(store.id))

        resume_scheduled = False
        resume_after_sec = None
        resume_eta_iso = None
        if server_stopped:
            try:
                resume_after_sec = max(
                    0, int(getattr(settings, 'CATALOG_SCRAPE_RESUME_AFTER_STOP_SECONDS', 0) or 0),
                )
            except ValueError:
                resume_after_sec = 0
            if resume_after_sec > 0:
                try:
                    from datetime import timedelta as _td

                    from catalog.tasks import resume_catalog_scrape_after_stop

                    resume_catalog_scrape_after_stop.apply_async(
                        kwargs={
                            'store_id': str(store.id),
                            'scope': resume_scope,
                            'upload_id': resume_upload_id,
                        },
                        countdown=resume_after_sec,
                    )
                    resume_scheduled = True
                    resume_eta_iso = (timezone.now() + _td(seconds=resume_after_sec)).isoformat()
                except Exception:
                    logger.exception('Failed to schedule auto-resume catalog scrape')

        if not cancelled_payload and not server_stopped:
            return Response(
                {
                    'cancelled': [],
                    'server_scrape_stopped': False,
                    'detail': 'Nothing was running, so there was nothing to stop.',
                },
                status=status.HTTP_200_OK,
            )

        payload = {
            'cancelled': cancelled_payload,
            'server_scrape_stopped': server_stopped,
            'job_id': cancelled_payload[0]['job_id'] if cancelled_payload else None,
            'status': cancelled_payload[0]['status'] if cancelled_payload else None,
        }
        if resume_scheduled and resume_after_sec is not None:
            payload['server_resume_scheduled'] = True
            payload['server_resume_after_seconds'] = resume_after_sec
            payload['server_resume_eta'] = resume_eta_iso
        elif server_stopped:
            payload['server_resume_scheduled'] = False

        return Response(payload, status=status.HTTP_200_OK)


class CatalogUpdateTriggerView(APIView):
    """Trigger catalog update to Reverb (background job)."""
    permission_classes = [IsAuthenticated]

    def post(self, request, store_pk):
        from catalog.tasks import catalog_update_task
        store = get_object_or_404(Store, id=store_pk, user=request.user)
        upload_id = request.data.get('upload_id')
        if upload_id:
            upload = get_object_or_404(CatalogUpload, id=upload_id, store=store)
        else:
            upload = (
                CatalogUpload.objects.filter(
                    store=store,
                    status__in=[CatalogUpload.Status.SYNCED, CatalogUpload.Status.PARTIAL],
                )
                .order_by('-created_at')
                .first()
            )
        if not upload:
            return Response(
                {"error": "No synced upload found. Run sync first."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        task = catalog_update_task.delay(str(upload.id))
        return Response({
            "job_id": task.id,
            "upload_id": str(upload.id),
            "status": "queued",
        }, status=status.HTTP_202_ACCEPTED)


class CatalogSyncLogsView(APIView):
    """List recent sync logs for a store."""
    permission_classes = [IsAuthenticated]

    def get(self, request, store_pk):
        store = get_object_or_404(Store, id=store_pk, user=request.user)
        logs = (
            CatalogSyncLog.objects.filter(catalog_upload__store=store)
            .select_related('catalog_upload_row')
            .order_by('-created_at')[:100]
        )
        data = [
            {
                "id": str(l.id),
                "upload_id": str(l.catalog_upload_id),
                "row_number": l.catalog_upload_row.row_number if l.catalog_upload_row else None,
                "action": l.action,
                "status": l.status,
                "message": l.message,
                "created_at": l.created_at.isoformat() if l.created_at else None,
            }
            for l in logs
        ]
        return Response(data)


def _compute_vendor_queue_payload(store, vendor_code: str, latest_job):
    """Return queue/ETA info for the Catalog UI, scoped to a single vendor.

    Only jobs with matching ``vendor_code`` are considered — a HEB job never
    delays a Costco queue and vice versa (each has its own desktop poller).

    - ``position``     : 1 = this store is next up; ``None`` if this store is
                        not currently waiting in line.
    - ``ahead_count``  : number of pending jobs ahead of this store.
    - ``eta_seconds``  : approximate seconds until this store's job starts,
                        derived from recent completed runs.
    - ``currently_running``  : payload for whichever job is currently CLAIMED
                                for this vendor.
    - ``average_seconds``    : avg duration of the last ~10 completed runs.
    """
    base_qs = HebScrapeJob.objects.filter(vendor_code=vendor_code)
    pending_qs = (
        base_qs.filter(status=HebScrapeJob.Status.PENDING)
        .order_by('requested_at')
    )

    position = None
    ahead_count = 0
    if latest_job and latest_job.status == HebScrapeJob.Status.PENDING:
        ahead_ids = list(pending_qs.values_list('id', flat=True))
        try:
            position = ahead_ids.index(latest_job.id) + 1
        except ValueError:
            position = None
        ahead_count = max(0, (position or 1) - 1)

    recent_done = (
        base_qs.filter(
            status=HebScrapeJob.Status.DONE,
            claimed_at__isnull=False,
            completed_at__isnull=False,
        )
        .order_by('-completed_at')[:10]
    )
    durations = []
    for j in recent_done:
        if j.claimed_at and j.completed_at:
            d = (j.completed_at - j.claimed_at).total_seconds()
            if d > 0:
                durations.append(d)
    avg_seconds = int(round(sum(durations) / len(durations))) if durations else None

    eta_seconds = None
    if position is not None and avg_seconds:
        eta_seconds = int(position * avg_seconds)

    running = (
        base_qs.select_related('store')
        .filter(status=HebScrapeJob.Status.CLAIMED)
        .order_by('claimed_at')
        .first()
    )
    currently_running = None
    if running is not None:
        is_this_store = (running.store_id == store.id)
        currently_running = {
            'job_id': str(running.id),
            'store_id': str(running.store_id) if running.store_id else None,
            'store_name': (
                running.store.name if running.store_id
                else f'All {vendor_code.upper()} stores'
            ),
            'claimed_at': running.claimed_at.isoformat() if running.claimed_at else None,
            'is_this_store': is_this_store,
        }

    return {
        'vendor': vendor_code,
        'position': position,
        'ahead_count': ahead_count,
        'eta_seconds': eta_seconds,
        'average_seconds': avg_seconds,
        'currently_running': currently_running,
    }


def _compute_heb_queue_payload(store, latest_job):
    """Legacy alias retained for any external callers."""
    return _compute_vendor_queue_payload(store, 'heb', latest_job)


class CatalogScrapeProgressView(APIView):
    """Live progress counters for a store's scrape/ingest pipeline.

    Designed for the Catalog UI to poll every few seconds so the Scrape button
    can stay in a "working" state until every product has fresh data. For HEB
    stores this tracks how many products have been populated from the desktop
    runner's ingest feed (``/api/v1/ingest/heb/``).

    Response keys:
        total                : count of active ProductMappings for the store
        by_status            : {'pending': N, 'scraped': N, 'synced': N, ...}
        heb_total            : HEB-vendor active mappings for this store
        heb_pending          : HEB rows still waiting for ingest data
        heb_scraped          : HEB rows that have fresh prices
        heb_pct              : 0..100 percentage of HEB rows scraped/synced
        heb_last_ingest_at   : most recent VendorPrice ingest across HEB mappings
                                for this store (None if the desktop runner has
                                never posted anything that matched)
        heb_ingested_last_5m : HEB VendorPrice rows received in last 5 min
        heb_ingested_last_24h: HEB VendorPrice rows received in last 24 h
        has_heb              : convenience flag for frontend
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [ProgressReadRateThrottle]

    def get(self, request, store_pk):
        from catalog.scrape_progress import get_scrape_progress_payload

        store = get_object_or_404(Store, id=store_pk, user=request.user)
        payload = get_scrape_progress_payload(store)
        return Response(
            payload,
            headers={'Cache-Control': 'no-store, max-age=0, private'},
        )


class CatalogScrapeRunsView(APIView):
    """List scrape runs for a store."""
    permission_classes = [IsAuthenticated]

    def get(self, request, store_pk):
        from sync.models import ScrapeRun
        store = get_object_or_404(Store, id=store_pk, user=request.user)
        runs = ScrapeRun.objects.filter(store=store).order_by('-started_at')[:50]
        data = [
            {
                "id": str(r.id),
                "upload_id": str(r.catalog_upload_id) if r.catalog_upload_id else None,
                "status": r.status,
                "rows_processed": r.rows_processed,
                "rows_succeeded": r.rows_succeeded,
                "error_summary": r.error_summary,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            }
            for r in runs
        ]
        return Response(data)


class CatalogUpdateLogsView(APIView):
    """List recent Reverb update logs for a store."""
    permission_classes = [IsAuthenticated]

    def get(self, request, store_pk):
        store = get_object_or_404(Store, id=store_pk, user=request.user)
        logs = (
            ReverbUpdateLog.objects.filter(product_mapping__store=store)
            .select_related('product_mapping')
            .order_by('-created_at')[:100]
        )
        data = [
            {
                "id": str(l.id),
                "product_mapping_id": str(l.product_mapping_id),
                "status": l.status,
                "http_status": l.http_status,
                "error_message": l.error_message,
                "pushed_price": str(l.pushed_price) if l.pushed_price else None,
                "pushed_stock": l.pushed_stock,
                "created_at": l.created_at.isoformat() if l.created_at else None,
            }
            for l in logs
        ]
        return Response(data)


class CatalogJobStatusView(APIView):
    """Poll Celery task status by job_id (task id from sync/scrape/update trigger)."""
    permission_classes = [IsAuthenticated]
    throttle_classes = [ProgressReadRateThrottle]

    def get(self, request, store_pk, job_id):
        from celery.result import AsyncResult
        store = get_object_or_404(Store, id=store_pk, user=request.user)
        result = AsyncResult(job_id)
        data = {
            "job_id": job_id,
            "status": result.status.lower() if result.status else "unknown",
            "ready": result.ready(),
            "successful": result.successful() if result.ready() else None,
        }
        if result.ready():
            if result.successful():
                data["result"] = result.result
            else:
                data["error"] = str(result.result) if result.result else "Task failed"
        return Response(data)


class CatalogActivityLogListView(APIView):
    """Last 24 hours of catalog timeline for a store (scrape, sync, resets)."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ProgressReadRateThrottle]

    def get(self, request, store_pk):
        from datetime import timedelta

        store = get_object_or_404(Store, id=store_pk, user=request.user)
        since = timezone.now() - timedelta(days=1)
        qs = (
            CatalogActivityLog.objects.filter(store=store, created_at__gte=since)
            .select_related('user')
            .order_by('-created_at')[:500]
        )
        return Response(CatalogActivityLogSerializer(qs, many=True).data)


class CatalogPushListingsView(APIView):
    """Push local price/stock to marketplace for scraped/synced products only (no vendor scrape)."""
    permission_classes = [IsAuthenticated]

    def post(self, request, store_pk):
        import uuid

        from catalog.activity_log import append_catalog_log
        from catalog.models import ProductMapping
        from sync.push_listings_lock import (
            handoff_push_listings_lock,
            is_push_listings_locked,
            release_push_listings_lock,
            try_acquire_push_listings_lock,
        )
        from sync.tasks import _execute_store_push_listings_only, run_store_push_listings_only

        store = get_object_or_404(Store, id=store_pk, user=request.user)
        append_catalog_log(
            store.id,
            'You started Manual sync (push listings to the marketplace).',
            action_type='user_action',
            user_id=request.user.id,
        )
        if store.connection_status != 'connected':
            return Response(
                {'error': 'Store not connected. Validate connection first.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        total_listings = ProductMapping.objects.filter(
            store=store,
            is_active=True,
            sync_status__in=['synced', 'scraped'],
            store_price__isnull=False,
        ).count()

        run_inline = request.data.get('run_inline') or request.query_params.get('inline') == '1'
        store_key = str(store.id)

        if run_inline:
            inline_owner = f'inline:{uuid.uuid4().hex}'
            if not try_acquire_push_listings_lock(store_key, inline_owner):
                return Response(
                    {
                        'error': 'push_listings_already_running',
                        'detail': (
                            'A marketplace push is already running for this store. '
                            'Wait for it to finish or check Activity.'
                        ),
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            try:
                result = _execute_store_push_listings_only(store_key, disable_schedule=True)
            finally:
                from sync.push_listings_cancel import clear_push_listings_cancel

                clear_push_listings_cancel(store_key)
                release_push_listings_lock(store_key, inline_owner)
            return Response(result, status=status.HTTP_200_OK)

        if is_push_listings_locked(store_key):
            return Response(
                {
                    'error': 'push_listings_already_running',
                    'detail': (
                        'A marketplace push is already queued or running for this store. '
                        'Wait for it to finish, check Activity, or use Stop Syncing and try again.'
                    ),
                },
                status=status.HTTP_409_CONFLICT,
            )

        reservation = f'reserved:{uuid.uuid4().hex}'
        if not try_acquire_push_listings_lock(store_key, reservation):
            return Response(
                {
                    'error': 'push_listings_already_running',
                    'detail': 'A marketplace push is already running for this store.',
                },
                status=status.HTTP_409_CONFLICT,
            )

        try:
            async_result = run_store_push_listings_only.delay(store_key, True)
            handoff_push_listings_lock(store_key, reservation, async_result.id)
        except Exception as e:
            release_push_listings_lock(store_key, reservation)
            detail = str(e)
            return Response(
                {
                    'error': (
                        'Background sync worker unavailable. Manual sync must run on '
                        'celery_worker_sync (sync queue). Ensure Redis and celery_worker_sync are running.'
                    ),
                    'detail': detail,
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            {
                'job_id': async_result.id,
                'status': 'queued',
                'message': 'Manual listing push queued.',
                'total_listings': total_listings,
            },
            status=status.HTTP_202_ACCEPTED,
        )


class CatalogPushListingsProgressView(APIView):
    """Live progress for Manual sync (marketplace push) — poll like scrape progress."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ProgressReadRateThrottle]

    def get(self, request, store_pk):
        from catalog.push_listings_progress import build_push_listings_progress_payload

        store = get_object_or_404(Store, id=store_pk, user=request.user)
        return Response(
            build_push_listings_progress_payload(store),
            headers={'Cache-Control': 'no-store, max-age=0, private'},
        )


class CatalogPushListingsCancelView(APIView):
    """Stop Manual sync (marketplace push) for a store — mirrors Stop Scraping."""

    permission_classes = [IsAuthenticated]

    def post(self, request, store_pk):
        from catalog.activity_log import append_catalog_log
        from catalog.models import CatalogActivityLog
        from catalog.reverb_catalog import store_is_sears
        from sync.push_listings_cancel import request_push_listings_cancel
        from sync.push_listings_lock import (
            force_release_push_listings_lock,
            get_push_listings_lock_owner,
        )
        from sync.sears_seller_lock import release_sears_seller_lock

        store = get_object_or_404(Store, id=store_pk, user=request.user)
        store_id = str(store.id)
        lock_owner = get_push_listings_lock_owner(store_id)

        if not lock_owner:
            return Response(
                {
                    'push_listings_stopped': False,
                    'detail': 'Nothing was running, so there was nothing to stop.',
                },
                status=status.HTTP_200_OK,
            )

        request_push_listings_cancel(store_id)

        owner = str(lock_owner)
        if not owner.startswith('reserved:') and not owner.startswith('inline:'):
            try:
                from core.celery import app

                app.control.revoke(owner, terminate=True, signal='SIGTERM')
            except Exception:
                logger.exception('Failed to revoke push listings task %s', owner)

        force_release_push_listings_lock(store_id)

        if store_is_sears(store):
            try:
                from store_adapters import get_adapter

                adapter = get_adapter(store)
                seller_id = getattr(adapter, '_seller_id', '') or ''
                if seller_id:
                    release_sears_seller_lock(seller_id, store_id)
            except Exception:
                logger.exception('Failed to release Sears seller lock for store %s', store_id)

        pushed = failed = skipped = 0
        latest_progress = (
            CatalogActivityLog.objects.filter(
                store=store,
                action_type='sync_progress',
            )
            .order_by('-created_at')
            .first()
        )
        if latest_progress and latest_progress.metadata:
            md = latest_progress.metadata
            pushed = int(md.get('pushed') or 0)
            failed = int(md.get('failed') or 0)
            skipped = int(md.get('skipped_no_listing') or 0)

        append_catalog_log(
            store.id,
            'You stopped Manual sync (marketplace push).',
            action_type='sync_cancelled',
            user_id=request.user.id,
            metadata={
                'lock_owner': owner,
                'pushed': pushed,
                'failed': failed,
                'skipped_no_listing': skipped,
            },
        )

        return Response(
            {
                'push_listings_stopped': True,
                'job_id': (
                    owner
                    if not owner.startswith('reserved:') and not owner.startswith('inline:')
                    else None
                ),
                'pushed': pushed,
                'failed': failed,
                'skipped_no_listing': skipped,
            },
            status=status.HTTP_200_OK,
        )


_RESET_PENDING_SCOPE_LABELS = {
    'all': 'all active listings',
    'failed': 'failed listings',
    'needs_attention': 'needs-attention listings',
}


class CatalogResetListingsPendingView(APIView):
    """Set store listings to Pending (clears scrape retry state).

    Body: ``{"confirm": true, "scope": "all"|"failed"|"needs_attention"}`` (scope defaults to ``all``).
    Does not start a scrape — use Start Scraping afterward.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, store_pk):
        if request.data.get('confirm') is not True:
            return Response(
                {'error': 'You must send {"confirm": true}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        scope = (request.data.get('scope') or 'all').strip().lower()
        if scope not in _RESET_PENDING_SCOPE_LABELS:
            return Response(
                {'error': 'scope must be one of: all, failed, needs_attention'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        store = get_object_or_404(Store, id=store_pk, user=request.user)
        qs = ProductMapping.objects.filter(store=store, is_active=True)
        if scope == 'failed':
            qs = qs.filter(sync_status='failed')
        elif scope == 'needs_attention':
            qs = qs.filter(sync_status='needs_attention')

        log_action(
            request.user,
            'catalog_reset_pending',
            'store',
            str(store.id),
            metadata={'store_name': store.name, 'scope': scope},
            request=request,
        )
        n = qs.update(
            sync_status='pending',
            failed_sync_count=0,
            scrape_error=None,
        )
        if scope == 'all':
            Store.objects.filter(id=store.id).update(
                catalog_pending_reset_at=None,
                catalog_zero_pending_at=None,
            )
        from catalog.activity_log import append_catalog_log
        scope_label = _RESET_PENDING_SCOPE_LABELS[scope]
        append_catalog_log(
            store.id,
            f'{n} {scope_label} were set to Pending for a fresh vendor check.',
            action_type='catalog_manual_pending_reset',
            metadata={'rows_reset': n, 'scope': scope},
        )
        return Response({'status': 'ok', 'listings_reset': n, 'scope': scope})


class StoreCriticalZeroView(APIView):
    """
    Emergency: set all listing stock to 0 (local + marketplace), deactivate store and sync schedule.
    Requires JSON body {"confirm": true}.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, store_pk):
        if request.data.get('confirm') is not True:
            return Response(
                {'error': 'You must send {"confirm": true} to run this action.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        store = get_object_or_404(Store, id=store_pk, user=request.user)
        log_action(
            request.user, 'critical_zero_inventory', 'store', str(store.id),
            metadata={'store_name': store.name}, request=request,
        )
        run_inline = request.data.get('run_inline') or request.query_params.get('inline') == '1'
        if run_inline:
            from sync.tasks import run_store_critical_zero_inventory
            result = run_store_critical_zero_inventory(str(store.id))
            return Response(result, status=status.HTTP_200_OK)
        try:
            from sync.tasks import run_store_critical_zero_inventory
            async_result = run_store_critical_zero_inventory.delay(str(store.id))
        except Exception as e:
            detail = str(e)
            if 'redis' in detail.lower() or 'connection' in detail.lower():
                from sync.tasks import run_store_critical_zero_inventory
                result = run_store_critical_zero_inventory(str(store.id))
                return Response(result, status=status.HTTP_200_OK)
            return Response({'detail': detail}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response(
            {'job_id': async_result.id, 'status': 'queued', 'message': 'Critical zero-inventory job queued.'},
            status=status.HTTP_202_ACCEPTED,
        )


class StoreFailedZeroInventoryView(APIView):
    """
    Zero stock locally and on the marketplace for failed / needs_attention listings only.
    Store and schedule stay active. Requires JSON body {"confirm": true}.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, store_pk):
        if request.data.get('confirm') is not True:
            return Response(
                {'error': 'You must send {"confirm": true} to run this action.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        store = get_object_or_404(Store, id=store_pk, user=request.user)
        log_action(
            request.user, 'failed_zero_inventory', 'store', str(store.id),
            metadata={'store_name': store.name}, request=request,
        )
        run_inline = request.data.get('run_inline') or request.query_params.get('inline') == '1'
        if run_inline:
            from sync.tasks import run_store_failed_zero_inventory
            result = run_store_failed_zero_inventory(str(store.id))
            return Response(result, status=status.HTTP_200_OK)
        try:
            from sync.tasks import run_store_failed_zero_inventory
            async_result = run_store_failed_zero_inventory.delay(str(store.id))
        except Exception as e:
            detail = str(e)
            if 'redis' in detail.lower() or 'connection' in detail.lower():
                from sync.tasks import run_store_failed_zero_inventory
                result = run_store_failed_zero_inventory(str(store.id))
                return Response(result, status=status.HTTP_200_OK)
            return Response({'detail': detail}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response(
            {
                'job_id': async_result.id,
                'status': 'queued',
                'message': 'Failed-listing zero-inventory job queued.',
            },
            status=status.HTTP_202_ACCEPTED,
        )


class CatalogSampleTemplateView(APIView):
    """Download sample CSV template for catalog bulk upload (marketplace-specific columns)."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store_id = request.query_params.get('store_id')
        kind_param = (request.query_params.get('marketplace') or '').strip().lower()
        store = None
        if store_id:
            store = get_object_or_404(
                Store.objects.select_related('marketplace'),
                id=store_id,
                user=request.user,
            )

        if kind_param in ('reverb', 'walmart', 'sears'):
            kind = kind_param
        elif store:
            kind = template_kind_from_store_adapter(store)
            if kind == 'other':
                kind = store_marketplace_kind(store)
        elif kind_param:
            kind = 'other'
        else:
            kind = 'other'

        response = HttpResponse(content_type='text/csv')
        fname = sample_template_filename_for_kind(kind)
        headers, sample_rows = sample_template_rows_for_kind(kind)

        response['Content-Disposition'] = f'attachment; filename="{fname}"'
        writer = csv.writer(response)
        writer.writerow(headers)
        for row in sample_rows:
            writer.writerow(row)
        return response
