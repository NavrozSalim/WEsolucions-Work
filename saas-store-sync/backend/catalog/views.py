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

from catalog.models import ProductMapping, CatalogUpload, CatalogUploadRow, CatalogSyncLog, ReverbUpdateLog, CatalogActivityLog, HebScrapeJob
from catalog.serializers import ProductMappingSerializer, CatalogActivityLogSerializer
from catalog.pagination import CatalogProductPagination
from catalog.services import validate_and_create_upload
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

        stores = Store.objects.filter(user=request.user).select_related('marketplace').annotate(
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
                'marketplace_code': (s.marketplace.code or '').strip() if s.marketplace else None,
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


def _store_has_heb_products(store) -> bool:
    """Legacy alias retained for any external callers. Prefer
    ``_store_has_vendor_products(store, 'heb')`` in new code."""
    return _store_has_vendor_products(store, 'heb')


class CatalogScrapeTriggerView(APIView):
    """Trigger catalog scrape (fetch vendor price/stock, apply rules)."""
    permission_classes = [IsAuthenticated]

    @staticmethod
    def _maybe_enqueue_vendor_job(store, user, vendor_code: str) -> HebScrapeJob | None:
        """Create (or return existing) ``HebScrapeJob`` row for ``vendor_code``
        if ``store`` actually has products for that vendor.

        Returns ``None`` if the store has nothing for this vendor — callers
        should silently skip that vendor in that case.
        """
        if not _store_has_vendor_products(store, vendor_code):
            return None
        existing = HebScrapeJob.objects.filter(
            store=store,
            vendor_code=vendor_code,
            status__in=[HebScrapeJob.Status.PENDING, HebScrapeJob.Status.CLAIMED],
        ).order_by('-requested_at').first()
        if existing:
            return existing
        return HebScrapeJob.objects.create(
            store=store,
            requested_by=user,
            vendor_code=vendor_code,
        )

    @classmethod
    def _maybe_enqueue_desktop_jobs(cls, store, user) -> list:
        """Walk every supported desktop-runner vendor and enqueue a job for
        each one that has products in ``store``. Returns a list of
        ``(vendor_code, job)`` tuples for the ones that got queued (new or
        pre-existing pending/claimed).
        """
        from catalog.ingest_views import SUPPORTED_VENDORS
        jobs: list = []
        for vendor_code in SUPPORTED_VENDORS.keys():
            job = cls._maybe_enqueue_vendor_job(store, user, vendor_code)
            if job is not None:
                jobs.append((vendor_code, job))
        return jobs

    @classmethod
    def _maybe_enqueue_heb_job(cls, store, user) -> HebScrapeJob | None:
        """Backward-compat shim for the HEB-specific helper."""
        return cls._maybe_enqueue_vendor_job(store, user, 'heb')

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

        desktop_jobs = self._maybe_enqueue_desktop_jobs(store, request.user)
        for vendor_code, vendor_job in desktop_jobs:
            append_catalog_log(
                store.id,
                f'Queued {vendor_code.upper()} scrape job {vendor_job.id} for the desktop runner.',
                action_type=f'{vendor_code}_scrape_queued',
                user_id=request.user.id,
                metadata={'job_id': str(vendor_job.id), 'vendor': vendor_code},
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


class CatalogScrapeCancelView(APIView):
    """Cancel running or queued desktop-runner scrapes for a store.

    By default cancels every active job (``PENDING`` or ``CLAIMED``) across
    all supported vendors for this store. Pass ``?vendor=heb`` (or
    ``?vendor=costco``) to scope the cancellation to a single vendor.

    Safe to call when there is no active job — returns 200 with
    ``cancelled: []`` so the UI can call this optimistically.
    """

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
        if not jobs:
            return Response(
                {'cancelled': [], 'detail': 'No active scrape for this store.'},
                status=status.HTTP_200_OK,
            )

        now = timezone.now()
        cancelled_payload = []
        for job in jobs:
            prior_status = job.status
            job.status = HebScrapeJob.Status.CANCELLED
            job.completed_at = now
            job.note = (job.note or '') + (f'\nCancelled by user @ {now.isoformat()}').strip()
            job.save(update_fields=['status', 'completed_at', 'note'])

            append_catalog_log(
                store.id,
                f'You cancelled the running {job.vendor_code.upper()} scrape.',
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

        return Response(
            {
                'cancelled': cancelled_payload,
                # Back-compat fields for older frontend builds that only look
                # at a single cancelled job.
                'job_id': cancelled_payload[0]['job_id'] if cancelled_payload else None,
                'status': cancelled_payload[0]['status'] if cancelled_payload else None,
            },
            status=status.HTTP_200_OK,
        )


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

    def get(self, request, store_pk):
        from datetime import timedelta
        from vendor.models import VendorPrice
        from catalog.ingest_views import SUPPORTED_VENDORS

        store = get_object_or_404(Store, id=store_pk, user=request.user)

        active = ProductMapping.objects.filter(store=store, is_active=True)
        total = active.count()
        by_status_rows = active.values('sync_status').annotate(n=Count('id'))
        by_status = {r['sync_status']: r['n'] for r in by_status_rows}

        now = timezone.now()
        vendors_payload: dict[str, dict] = {}

        # Per-vendor progress/queue — iterate every registered desktop vendor so
        # the frontend can render one progress strip per vendor the store uses.
        for vendor_code in SUPPORTED_VENDORS.keys():
            vendor_ids = _vendor_db_ids_for(vendor_code)
            if not vendor_ids:
                continue

            vq = active.filter(product__vendor_id__in=vendor_ids)
            v_total = vq.count()
            v_rows = vq.values('sync_status').annotate(n=Count('id'))
            v_by_status = {r['sync_status']: r['n'] for r in v_rows}

            v_last_ingest_at = None
            v_ingested_last_5m = 0
            v_ingested_last_24h = 0
            if v_total:
                v_product_ids = list(vq.values_list('product_id', flat=True).distinct())
                vp_qs = VendorPrice.objects.filter(product_id__in=v_product_ids)
                last_vp = vp_qs.order_by('-scraped_at').values_list('scraped_at', flat=True).first()
                v_last_ingest_at = last_vp.isoformat() if last_vp else None
                v_ingested_last_5m = vp_qs.filter(scraped_at__gte=now - timedelta(minutes=5)).count()
                v_ingested_last_24h = vp_qs.filter(scraped_at__gte=now - timedelta(hours=24)).count()

            v_scraped = v_by_status.get('scraped', 0) + v_by_status.get('synced', 0)
            v_pending = (
                v_by_status.get('pending', 0)
                + v_by_status.get('needs_attention', 0)
                + v_by_status.get('failed', 0)
            )
            v_pct = int(round(v_scraped * 100 / v_total)) if v_total else 0

            latest_job = (
                HebScrapeJob.objects
                .filter(store=store, vendor_code=vendor_code)
                .order_by('-requested_at')
                .first()
            )
            v_job_payload = None
            if latest_job is not None:
                v_job_payload = {
                    'id': str(latest_job.id),
                    'vendor': latest_job.vendor_code,
                    'status': latest_job.status,
                    'requested_at': latest_job.requested_at.isoformat(),
                    'claimed_at': latest_job.claimed_at.isoformat() if latest_job.claimed_at else None,
                    'completed_at': latest_job.completed_at.isoformat() if latest_job.completed_at else None,
                    'url_count': latest_job.url_count,
                    'stats': latest_job.stats or {},
                }
            v_queue_payload = _compute_vendor_queue_payload(store, vendor_code, latest_job)

            vendors_payload[vendor_code] = {
                'vendor': vendor_code,
                'label': SUPPORTED_VENDORS[vendor_code].get('label', vendor_code.upper()),
                'has_products': v_total > 0,
                'total': v_total,
                'scraped': v_scraped,
                'pending': v_pending,
                'by_status': v_by_status,
                'pct': v_pct,
                'last_ingest_at': v_last_ingest_at,
                'ingested_last_5m': v_ingested_last_5m,
                'ingested_last_24h': v_ingested_last_24h,
                'job': v_job_payload,
                'queue': v_queue_payload,
            }

        # Backward-compat: flatten the HEB payload into the top-level `heb_*`
        # keys that the current frontend build still reads.
        heb = vendors_payload.get('heb') or {}
        heb_total = heb.get('total', 0)
        heb_scraped = heb.get('scraped', 0)
        heb_pending = heb.get('pending', 0)

        return Response({
            'total': total,
            'by_status': by_status,
            'has_heb': bool(heb_total > 0),
            'heb_total': heb_total,
            'heb_scraped': heb_scraped,
            'heb_pending': heb_pending,
            'heb_by_status': heb.get('by_status', {}),
            'heb_pct': heb.get('pct', 0),
            'heb_last_ingest_at': heb.get('last_ingest_at'),
            'heb_ingested_last_5m': heb.get('ingested_last_5m', 0),
            'heb_ingested_last_24h': heb.get('ingested_last_24h', 0),
            'heb_job': heb.get('job'),
            'heb_queue': heb.get('queue'),
            # New vendor-aware payload. Frontend should migrate to reading
            # from here so adding another vendor doesn't touch this endpoint.
            'vendors': vendors_payload,
            'checked_at': now.isoformat(),
        })


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
