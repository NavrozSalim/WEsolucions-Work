import io
import json
import re

from django.core.files.base import ContentFile
from django.http import FileResponse
from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .columns import ID_FIELD, options_payload
from .files import (
    SpreadsheetError,
    open_bytes,
    read_result_sheet,
    read_spreadsheet,
    template_bytes,
    workbook_bytes,
)
from .models import DiscoveryJob
from .routing import queue_for_marketplace
from .rules import normalize_rules
from .tasks import run_discovery_job


class CanUseDiscovery(BasePermission):
    message = 'You do not have permission to use scraping.'

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_staff:
            return True
        return user.has_product_permission('catalog')


def _jobs_for(user):
    org_id = getattr(user, 'organization_id', None)
    if org_id:
        return DiscoveryJob.objects.filter(owner__organization_id=org_id)
    return DiscoveryJob.objects.filter(owner=user)


def _job_payload(job: DiscoveryJob) -> dict:
    return {
        'id': str(job.id),
        'marketplace': job.marketplace,
        'marketplace_label': job.get_marketplace_display(),
        'mode': job.mode,
        'mode_label': job.get_mode_display(),
        'status': job.status,
        'original_filename': job.original_filename,
        'use_sample': job.use_sample,
        'zip_code': job.zip_code,
        'queue_name': job.queue_name,
        'rules': job.rules or {},
        'columns': job.columns or [],
        'stats': job.stats or {},
        'error_message': job.error_message,
        'parent_id': str(job.parent_id) if job.parent_id else None,
        'created_at': job.created_at,
        'started_at': job.started_at,
        'finished_at': job.finished_at,
        'download_url': (
            f'/api/v1/discovery/jobs/{job.id}/download/'
            if job.result_bytes or job.result_file
            else None
        ),
    }


def _parse_json(value, fallback):
    if value is None or value == '':
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _enqueue(job: DiscoveryJob) -> None:
    queue = queue_for_marketplace(job.marketplace)
    job.queue_name = queue
    job.save(update_fields=['queue_name'])
    run_discovery_job.apply_async(args=[str(job.id)], queue=queue)


class DiscoveryOptionsView(APIView):
    permission_classes = [IsAuthenticated, CanUseDiscovery]

    def get(self, request):
        return Response(options_payload())


class DiscoveryTemplateView(APIView):
    permission_classes = [IsAuthenticated, CanUseDiscovery]

    def get(self, request):
        marketplace = request.query_params.get('marketplace') or 'amazon_us'
        mode = request.query_params.get('mode') or 'category'
        if marketplace not in DiscoveryJob.Marketplace.values:
            return Response({'detail': 'Unknown marketplace.'}, status=status.HTTP_400_BAD_REQUEST)
        if mode not in DiscoveryJob.Mode.values:
            return Response({'detail': 'Unknown mode.'}, status=status.HTTP_400_BAD_REQUEST)
        filename, payload = template_bytes(marketplace, mode)
        response = FileResponse(ContentFile(payload), as_attachment=True, filename=filename)
        response['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        return response


class DiscoveryJobListCreateView(APIView):
    permission_classes = [IsAuthenticated, CanUseDiscovery]

    def get(self, request):
        jobs = _jobs_for(request.user)[:50]
        return Response([_job_payload(job) for job in jobs])

    def post(self, request):
        marketplace = request.data.get('marketplace') or ''
        mode = request.data.get('mode') or ''
        upload = request.FILES.get('file')
        if marketplace not in DiscoveryJob.Marketplace.values:
            return Response({'detail': 'Choose a marketplace.'}, status=status.HTTP_400_BAD_REQUEST)
        if mode not in DiscoveryJob.Mode.values:
            return Response({'detail': 'Choose Category or Product details.'}, status=status.HTTP_400_BAD_REQUEST)
        if upload is None:
            return Response({'detail': 'Upload a CSV or XLSX file.'}, status=status.HTTP_400_BAD_REQUEST)
        name = (upload.name or '').lower()
        if not name.endswith(('.csv', '.xlsx', '.xls')):
            return Response({'detail': 'Upload a CSV or XLSX file.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            read_spreadsheet(upload, marketplace=marketplace, mode=mode)
        except SpreadsheetError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        upload.seek(0)
        source_bytes = upload.read()
        upload.seek(0)

        use_sample = str(request.data.get('use_sample', '')).lower() in ('1', 'true', 'yes', 'on')
        zip_code = re.sub(r'\s+', '', str(request.data.get('zip_code') or ''))
        if not use_sample and not re.fullmatch(r'[A-Za-z0-9]{3,10}', zip_code):
            return Response(
                {'detail': 'Enter the delivery zip or postcode (for example 10001 or 2000).'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        job = DiscoveryJob.objects.create(
            owner=request.user,
            marketplace=marketplace,
            mode=mode,
            original_filename=upload.name,
            source_file=upload,
            source_bytes=source_bytes,
            rules=normalize_rules(_parse_json(request.data.get('rules'), {})),
            columns=_parse_json(request.data.get('columns'), []),
            use_sample=use_sample,
            zip_code=zip_code,
        )
        _enqueue(job)
        job.refresh_from_db()
        return Response(_job_payload(job), status=status.HTTP_201_CREATED)


class DiscoveryJobDetailView(APIView):
    permission_classes = [IsAuthenticated, CanUseDiscovery]

    def get(self, request, job_id):
        job = _jobs_for(request.user).filter(id=job_id).first()
        if job is None:
            return Response({'detail': 'Job not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(_job_payload(job))

    def delete(self, request, job_id):
        job = _jobs_for(request.user).filter(id=job_id).first()
        if job is None:
            return Response({'detail': 'Job not found.'}, status=status.HTTP_404_NOT_FOUND)
        if job.source_file:
            job.source_file.delete(save=False)
        if job.result_file:
            job.result_file.delete(save=False)
        job.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class DiscoveryJobRowsView(APIView):
    """Rows for the in-app product table."""

    permission_classes = [IsAuthenticated, CanUseDiscovery]

    def get(self, request, job_id):
        job = _jobs_for(request.user).filter(id=job_id).first()
        if job is None:
            return Response({'detail': 'Job not found.'}, status=status.HTTP_404_NOT_FOUND)
        stored = list(job.products.order_by('created_at'))
        if stored:
            columns = list(job.columns or []) or list(stored[0].data.keys())
            rows = [item.data for item in stored]
        else:
            try:
                blob = job.read_result()
            except FileNotFoundError:
                blob = b''
            if blob:
                columns, rows = read_result_sheet(io.BytesIO(blob))
            else:
                columns, rows = list(job.columns or []), []
        query = (request.query_params.get('q') or '').strip().lower()
        if query:
            rows = [
                row for row in rows
                if query in ' '.join(str(value or '') for value in row.values()).lower()
            ]
        page_size = 10
        try:
            page = max(1, int(request.query_params.get('page') or 1))
        except (TypeError, ValueError):
            page = 1
        page_count = max(1, (len(rows) + page_size - 1) // page_size)
        page = min(page, page_count)
        start = (page - 1) * page_size
        return Response({
            'columns': columns,
            'rows': rows[start:start + page_size],
            'total': len(rows),
            'page': page,
            'page_size': page_size,
        })


class DiscoveryJobDownloadView(APIView):
    permission_classes = [IsAuthenticated, CanUseDiscovery]

    def get(self, request, job_id):
        job = _jobs_for(request.user).filter(id=job_id).first()
        if job is None:
            return Response({'detail': 'Result file is not ready.'}, status=status.HTTP_404_NOT_FOUND)
        try:
            payload = job.read_result()
        except FileNotFoundError:
            payload = b''
        if not payload:
            return Response({'detail': 'Result file is not ready.'}, status=status.HTTP_404_NOT_FOUND)
        filename = f'{job.marketplace}-{job.mode}-results.xlsx'
        response = FileResponse(ContentFile(payload), as_attachment=True, filename=filename)
        response['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        return response


class DiscoveryJobIdsDownloadView(APIView):
    """One-column file of the product ids a category job kept."""

    permission_classes = [IsAuthenticated, CanUseDiscovery]

    def get(self, request, job_id):
        job = _jobs_for(request.user).filter(id=job_id).first()
        if job is None:
            return Response({'detail': 'Job not found.'}, status=status.HTTP_404_NOT_FOUND)
        if job.mode != DiscoveryJob.Mode.CATEGORY or job.status != DiscoveryJob.Status.SUCCEEDED:
            return Response({'detail': 'ASINs are ready after a category scrape finishes.'}, status=status.HTTP_400_BAD_REQUEST)
        field = ID_FIELD[job.marketplace]
        stored = list(job.products.order_by('created_at'))
        if stored:
            rows = [{field: (item.data or {}).get(field) or ''} for item in stored]
        else:
            try:
                blob = job.read_result()
            except FileNotFoundError:
                blob = b''
            if not blob:
                return Response({'detail': 'Result file is not ready.'}, status=status.HTTP_404_NOT_FOUND)
            _columns, sheet_rows = read_result_sheet(io.BytesIO(blob))
            rows = [{field: row.get(field) or ''} for row in sheet_rows]
        rows = [row for row in rows if str(row.get(field) or '').strip()]
        payload = workbook_bytes([field], rows)
        filename = f'{job.marketplace}-category-{field}s.xlsx'
        response = FileResponse(ContentFile(payload), as_attachment=True, filename=filename)
        response['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        return response


class DiscoveryJobContinueView(APIView):
    """Start a product-details job from the rows a category job kept."""

    permission_classes = [IsAuthenticated, CanUseDiscovery]

    def post(self, request, job_id):
        parent = _jobs_for(request.user).filter(id=job_id).first()
        if parent is None:
            return Response({'detail': 'Job not found.'}, status=status.HTTP_404_NOT_FOUND)
        if parent.mode != DiscoveryJob.Mode.CATEGORY:
            return Response({'detail': 'Continue is available for category jobs.'}, status=status.HTTP_400_BAD_REQUEST)
        if parent.status != DiscoveryJob.Status.SUCCEEDED:
            return Response({'detail': 'Wait until the category job finishes.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            result_blob = parent.read_result()
        except FileNotFoundError:
            result_blob = b''
        if not result_blob:
            return Response({'detail': 'Wait until the category job finishes.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            rows = read_spreadsheet(open_bytes(result_blob, parent.original_filename or 'results.xlsx'))
        except SpreadsheetError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        product_rows = []
        for row in rows:
            url = str(row.get('url') or '').strip()
            if not url:
                continue
            product_rows.append({
                'url': url,
                'asin': row.get('asin') or '',
                'item_id': row.get('item_id') or '',
                'title': row.get('title') or '',
                'price': row.get('price') if row.get('price') not in (None, '') else '',
                'rating': row.get('rating') if row.get('rating') not in (None, '') else '',
                'review_count': row.get('review_count') if row.get('review_count') not in (None, '') else '',
                'category': row.get('category') or '',
            })
        if not product_rows:
            return Response(
                {'detail': 'The category file has no product URLs left to scrape.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        use_sample = request.data.get('use_sample', parent.use_sample)
        if isinstance(use_sample, str):
            use_sample = use_sample.lower() in ('1', 'true', 'yes', 'on')
        rules = _parse_json(request.data.get('rules'), None)
        columns = _parse_json(request.data.get('columns'), None)
        payload = workbook_bytes(
            ['url', 'asin', 'item_id', 'title', 'price', 'rating', 'review_count', 'category'],
            product_rows,
        )
        child = DiscoveryJob(
            owner=request.user,
            parent=parent,
            marketplace=parent.marketplace,
            mode=DiscoveryJob.Mode.PRODUCT,
            original_filename=f'{parent.marketplace}-from-category.xlsx',
            rules=normalize_rules(rules if isinstance(rules, dict) else parent.rules),
            columns=columns if isinstance(columns, list) else [],
            use_sample=bool(use_sample),
            zip_code=parent.zip_code,
            source_bytes=payload,
        )
        try:
            child.source_file.save(
                f'{parent.id}-products.xlsx',
                ContentFile(payload),
                save=False,
            )
        except OSError:
            pass
        child.save()
        _enqueue(child)
        child.refresh_from_db()
        return Response(_job_payload(child), status=status.HTTP_201_CREATED)
