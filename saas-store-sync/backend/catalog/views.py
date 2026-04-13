import csv
import io
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone

from catalog.models import ProductMapping, CatalogUpload, CatalogUploadRow, CatalogSyncLog, ReverbUpdateLog, CatalogActivityLog
from catalog.serializers import ProductMappingSerializer, CatalogActivityLogSerializer
from catalog.pagination import CatalogProductPagination
from catalog.services import validate_and_create_upload
from catalog.marketplace_templates import (
    export_headers_for_store,
    sample_template_filename,
    sample_template_rows,
    sample_template_filename_for_kind,
    sample_template_rows_for_kind,
    upload_row_to_cells,
)
from products.models import Product
from stores.models import Store
from rest_framework.permissions import IsAuthenticated
from audit.utils import log_action
from django.db.models import Count, Q, Prefetch
from stores.models import StoreVendorPriceSettings


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

    def get(self, request):
        from sync.models import SyncSchedule

        stores = Store.objects.filter(user=request.user).annotate(
            product_count=Count('products', filter=Q(products__is_active=True)),
        ).order_by('name')
        marketplace_id = request.query_params.get('marketplace_id')
        if marketplace_id:
            stores = stores.filter(marketplace_id=marketplace_id)
        store_ids = [s.id for s in stores]
        sched_map = {
            str(s.store_id): s
            for s in SyncSchedule.objects.filter(store_id__in=store_ids)
        }
        data = []
        for s in stores:
            sch = sched_map.get(str(s.id))
            data.append({
                'id': str(s.id),
                'name': s.name,
                'marketplace_id': str(s.marketplace_id) if s.marketplace_id else None,
                'marketplace_name': s.marketplace.name if s.marketplace else None,
                'product_count': s.product_count,
                'schedule_active': sch.is_active if sch else None,
            })
        return Response(data)


class ProductMappingViewSet(viewsets.ModelViewSet):
    serializer_class = ProductMappingSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = CatalogProductPagination

    def get_queryset(self):
        store_id = self.kwargs.get('store_pk')
        qs = ProductMapping.objects.filter(is_active=True).select_related(
            'product',
            'product__vendor',
            'store',
        ).prefetch_related(
            'product__vendor_prices',
            Prefetch(
                'store__vendor_price_settings',
                queryset=StoreVendorPriceSettings.objects.prefetch_related(
                    'range_margins__price_range',
                ),
            ),
        )
        if store_id:
            return qs.filter(store_id=store_id, store__user=self.request.user)
        return qs.filter(store__user=self.request.user)

    @action(detail=True, methods=['post'])
    def reset_sync_status(self, request, store_pk=None, pk=None):
        """Reset failed_sync_count and sync_status to retry a 'needs_attention' product."""
        pm = self.get_object()
        pm.failed_sync_count = 0
        pm.sync_status = 'pending'
        pm.save()
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
            'SKU', 'Title', 'Vendor', 'Vendor URL', 'Vendor price', 'Store price', 'Stock',
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
            if vp and vp.price is not None:
                vprice = str(vp.price)
            writer.writerow([
                sku or '',
                (pm.title or '')[:500],
                pm.product.vendor.name if pm.product and pm.product.vendor else '',
                pm.product.vendor_url if pm.product else '',
                vprice,
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
        store = get_object_or_404(Store, id=store_pk, user=request.user)
        file_obj = request.data.get('file')
        if not file_obj:
            return Response({"error": "No file provided"}, status=status.HTTP_400_BAD_REQUEST)

        filename = getattr(file_obj, 'name', 'upload.csv')
        upload, errors = validate_and_create_upload(
            user=request.user,
            store=store,
            file_obj=file_obj,
            filename=filename,
        )
        if upload is None:
            return Response({"error": errors[0] if errors else "Upload failed"}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            "upload_id": str(upload.id),
            "total_rows": upload.total_rows,
            "status": upload.status,
            "errors": errors[:20],
        }, status=status.HTTP_201_CREATED)


class CatalogUploadListView(APIView):
    """List catalog uploads for a store (upload history)."""
    permission_classes = [IsAuthenticated]

    def get(self, request, store_pk):
        store = get_object_or_404(Store, id=store_pk, user=request.user)
        uploads = (
            CatalogUpload.objects.filter(store=store)
            .select_related('user', 'store', 'store__marketplace')
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
        rows = list(upload.rows.select_related('product_mapping').all())
        pm_ids = [r.product_mapping_id for r in rows if r.product_mapping_id]
        upload.delete()
        ProductMapping.objects.filter(id__in=pm_ids, store=store).delete()
        log_action(
            request.user, 'catalog_upload_deleted', 'catalog_upload', str(upload_id),
            metadata={'store_id': str(store.id), 'deleted_mappings': len(pm_ids)}, request=request
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
                CatalogUpload.objects.filter(store=store, status__in=['pending', 'validated'])
                .order_by('-created_at')
                .first()
            )
        if not upload:
            return Response(
                {"error": "No pending/validated upload found. Provide upload_id or upload first."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        run_inline = request.data.get('run_inline') or request.query_params.get('inline') == '1'
        auto_scrape = request.data.get('auto_scrape', True)
        if isinstance(auto_scrape, str):
            auto_scrape = auto_scrape.lower() in ('1', 'true', 'yes')

        if run_inline:
            from catalog.tasks import run_catalog_sync, run_catalog_scrape
            result = run_catalog_sync(str(upload.id))
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
                link=catalog_scrape_task.si(str(upload.id)),
            )
            return Response({
                "job_id": async_result.id,
                "upload_id": str(upload.id),
                "status": "queued",
                "scrape_after_sync": True,
            }, status=status.HTTP_202_ACCEPTED)

        task = catalog_sync_task.delay(str(upload.id))
        return Response({
            "job_id": task.id,
            "upload_id": str(upload.id),
            "status": "queued",
        }, status=status.HTTP_202_ACCEPTED)


class CatalogScrapeTriggerView(APIView):
    """Trigger catalog scrape (fetch vendor price/stock, apply rules)."""
    permission_classes = [IsAuthenticated]

    def post(self, request, store_pk):
        from catalog.activity_log import append_catalog_log
        from catalog.tasks import (
            catalog_scrape_store_task,
            catalog_scrape_task,
            run_catalog_scrape,
            run_store_wide_catalog_scrape,
        )
        store = get_object_or_404(Store, id=store_pk, user=request.user)
        append_catalog_log(
            store.id,
            'You requested a vendor scrape from the catalog page.',
            action_type='user_action',
            user_id=request.user.id,
        )
        upload_id = request.data.get('upload_id')
        scope_upload = (request.data.get('scope') or '').strip().lower() == 'upload'

        run_inline = request.data.get('run_inline') or request.query_params.get('inline') == '1'

        if upload_id:
            upload = get_object_or_404(CatalogUpload, id=upload_id, store=store)
            if run_inline:
                result = run_catalog_scrape(str(upload.id))
                if result.get('error'):
                    return Response(
                        {'detail': result['error'], **result},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    )
                return Response(result, status=status.HTTP_200_OK)
            task = catalog_scrape_task.delay(str(upload.id))
            return Response({
                "job_id": task.id,
                "upload_id": str(upload.id),
                "scope": "upload",
                "status": "queued",
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
                result = run_catalog_scrape(str(upload.id))
                if result.get('error'):
                    return Response(
                        {'detail': result['error'], **result},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    )
                return Response(result, status=status.HTTP_200_OK)
            task = catalog_scrape_task.delay(str(upload.id))
            return Response({
                "job_id": task.id,
                "upload_id": str(upload.id),
                "scope": "upload",
                "status": "queued",
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
            result = run_store_wide_catalog_scrape(str(store.id))
            if result.get('error'):
                return Response(
                    {'detail': result['error'], **result},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            return Response(result, status=status.HTTP_200_OK)
        task = catalog_scrape_store_task.delay(str(store.id))
        return Response({
            "job_id": task.id,
            "scope": "store",
            "status": "queued",
        }, status=status.HTTP_202_ACCEPTED)


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
        from catalog.activity_log import append_catalog_log

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
        run_inline = request.data.get('run_inline') or request.query_params.get('inline') == '1'
        if run_inline:
            from sync.tasks import run_store_push_listings_only
            result = run_store_push_listings_only(str(store.id), disable_schedule=True)
            return Response(result, status=status.HTTP_200_OK)
        try:
            from sync.tasks import run_store_push_listings_only
            async_result = run_store_push_listings_only.delay(str(store.id), True)
        except Exception as e:
            detail = str(e)
            if 'redis' in detail.lower() or 'connection' in detail.lower():
                from sync.tasks import run_store_push_listings_only
                result = run_store_push_listings_only(str(store.id), disable_schedule=True)
                return Response(result, status=status.HTTP_200_OK)
            return Response({'detail': detail}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response(
            {'job_id': async_result.id, 'status': 'queued', 'message': 'Manual listing push queued.'},
            status=status.HTTP_202_ACCEPTED,
        )


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


class CatalogSampleTemplateView(APIView):
    """Download sample CSV template for catalog bulk upload (marketplace-specific columns)."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store_id = request.query_params.get('store_id')
        store = None
        if store_id:
            store = get_object_or_404(Store, id=store_id, user=request.user)

        response = HttpResponse(content_type='text/csv')
        if store:
            fname = sample_template_filename(store)
            headers, sample_rows = sample_template_rows(store)
        else:
            fname = sample_template_filename_for_kind('other')
            headers, sample_rows = sample_template_rows_for_kind('other')

        response['Content-Disposition'] = f'attachment; filename="{fname}"'
        writer = csv.writer(response)
        writer.writerow(headers)
        for row in sample_rows:
            writer.writerow(row)
        return response
