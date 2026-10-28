import io
import json
import re
import tempfile
from datetime import timedelta

from django.core.files.base import ContentFile
from django.db.models import BooleanField, Case, Value, When
from django.http import FileResponse, StreamingHttpResponse
from django.utils import timezone
from django.utils.http import content_disposition_header
from rest_framework import status
from rest_framework.negotiation import DefaultContentNegotiation
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.utils.mediatypes import media_type_matches, order_by_precedence
from rest_framework.views import APIView

from .columns import ID_FIELD, options_payload
from .files import (
    SpreadsheetError,
    csv_chunks,
    open_bytes,
    read_result_sheet,
    read_spreadsheet,
    template_bytes,
    workbook_bytes,
    write_workbook,
)
from . import limits
from .models import DiscoveryJob
from .queue import PENDING_TASK_ID, dispatch_next, positions_for
from .rules import normalize_rules


class CanUseDiscovery(BasePermission):
    message = 'You do not have permission to use scraping.'

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_staff:
            return True
        return user.has_product_permission('scraping')


def _workbook_is_saved(job: DiscoveryJob) -> bool:
    """True when a workbook is stored, without reading the file into memory."""
    if getattr(job.result_file, 'name', ''):
        return True
    flagged = getattr(job, 'has_result_bytes', None)
    if flagged is not None:
        return bool(flagged)
    if not job.pk:
        return False
    return DiscoveryJob.objects.filter(pk=job.pk, result_bytes__isnull=False).exists()


def _jobs_for(user):
    org_id = getattr(user, 'organization_id', None)
    if org_id:
        jobs = DiscoveryJob.objects.filter(owner__organization_id=org_id)
    else:
        jobs = DiscoveryJob.objects.filter(owner=user)
    # The workbook lives in result_bytes. Loading it just to list jobs pulls
    # tens of megabytes into the web worker for every Amazon file.
    return jobs.defer('source_bytes', 'result_bytes').annotate(
        has_result_bytes=Case(
            When(result_bytes__isnull=False, then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        ),
    )


def _job_payload(job: DiscoveryJob, queue_position: int = 0) -> dict:
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
        'queue_position': queue_position,
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
            if _workbook_is_saved(job)
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


def _payloads(user, jobs) -> list[dict]:
    positions = positions_for(user)
    return [_job_payload(job, positions.get(job.id, 0)) for job in jobs]


def _revoke_task(task_id: str) -> None:
    if not task_id or task_id == PENDING_TASK_ID:
        return
    try:
        from core.celery import app as celery_app

        celery_app.control.revoke(task_id)
    except Exception:
        return


def _delete_job(job: DiscoveryJob) -> None:
    if job.source_file:
        job.source_file.delete(save=False)
    if job.result_file:
        job.result_file.delete(save=False)
    job.delete()


_XLSX_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def _not_ready(job: DiscoveryJob):
    if job.status in (DiscoveryJob.Status.QUEUED, DiscoveryJob.Status.RUNNING):
        detail = 'No products have been saved yet. Try again in a moment.'
    else:
        detail = 'Result file is not ready.'
    return Response({'detail': detail}, status=status.HTTP_404_NOT_FOUND)


def _open_saved_workbook(job: DiscoveryJob):
    """Open the workbook the scrape already wrote.

    The database copy is the one shared with the US and AU workers. The disk
    file is only a fallback from before that copy existed.
    """
    blob = job.result_bytes
    if blob:
        if isinstance(blob, memoryview):
            blob = blob.tobytes()
        elif not isinstance(blob, (bytes, bytearray)):
            blob = bytes(blob)
        return io.BytesIO(blob)
    if getattr(job.result_file, 'name', ''):
        try:
            return job.result_file.open('rb')
        except (OSError, FileNotFoundError, ValueError):
            return None
    return None


def _xlsx_response(handle, filename: str):
    response = FileResponse(handle, as_attachment=True, filename=filename)
    response['Content-Type'] = _XLSX_TYPE
    return response


def _csv_response(chunks, filename: str):
    response = StreamingHttpResponse(chunks, content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = content_disposition_header(True, filename)
    return response


def _export_columns(job: DiscoveryJob) -> list[str]:
    columns = [str(column) for column in (job.columns or []) if str(column or '').strip()]
    if columns:
        return columns
    first = job.products.order_by('created_at').values_list('data', flat=True).first()
    if isinstance(first, dict) and first:
        return list(first.keys())
    return []


def _iter_product_rows(job: DiscoveryJob):
    stored = (
        job.products.order_by('created_at')
        .values_list('data', flat=True)
        .iterator(chunk_size=500)
    )
    for data in stored:
        yield data or {}


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
        jobs = list(_jobs_for(request.user).select_related('owner')[:50])
        return Response(_payloads(request.user, jobs))

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
            rows = read_spreadsheet(upload, marketplace=marketplace, mode=mode)
        except SpreadsheetError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        too_many = limits.limit_detail(mode, len(rows))
        if too_many:
            return Response({'detail': too_many}, status=status.HTTP_400_BAD_REQUEST)
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
        dispatch_next(request.user, marketplace)
        job.refresh_from_db()
        return Response(
            _job_payload(job, positions_for(request.user).get(job.id, 0)),
            status=status.HTTP_201_CREATED,
        )


class DiscoveryJobDetailView(APIView):
    permission_classes = [IsAuthenticated, CanUseDiscovery]

    def get(self, request, job_id):
        job = _jobs_for(request.user).filter(id=job_id).first()
        if job is None:
            return Response({'detail': 'Job not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(_job_payload(job, positions_for(request.user).get(job.id, 0)))

    def delete(self, request, job_id):
        job = _jobs_for(request.user).filter(id=job_id).first()
        if job is None:
            return Response({'detail': 'Job not found.'}, status=status.HTTP_404_NOT_FOUND)
        owner = job.owner
        marketplace = job.marketplace
        advance = job.status in (DiscoveryJob.Status.QUEUED, DiscoveryJob.Status.RUNNING)
        if advance:
            _revoke_task(job.celery_task_id)
        _delete_job(job)
        if advance:
            dispatch_next(owner, marketplace)
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


class _SpreadsheetNegotiation(DefaultContentNegotiation):
    """`?format=xlsx` chooses the spreadsheet, not a REST renderer."""

    def select_renderer(self, request, renderers, format_suffix=None):
        accepts = self.get_accept_list(request)
        for media_type_set in order_by_precedence(accepts):
            for renderer in renderers:
                for media_type in media_type_set:
                    if media_type_matches(renderer.media_type, media_type):
                        return renderer, media_type.split(';')[0]
        return renderers[0], renderers[0].media_type


class DiscoveryJobDownloadView(APIView):
    permission_classes = [IsAuthenticated, CanUseDiscovery]
    content_negotiation_class = _SpreadsheetNegotiation

    def get(self, request, job_id):
        job = _jobs_for(request.user).filter(id=job_id).first()
        if job is None:
            return Response({'detail': 'Result file is not ready.'}, status=status.HTTP_404_NOT_FOUND)
        if not job.products.exists():
            return _not_ready(job)
        export_format = (request.query_params.get('format') or 'xlsx').strip().lower()
        # A finished or stopped scrape already stored the workbook. Sending that
        # copy skips rebuilding tens of thousands of Amazon rows inside the
        # request, which is what the web worker was timing out on.
        if export_format != 'csv' and job.status != DiscoveryJob.Status.RUNNING:
            saved = _open_saved_workbook(job)
            if saved is not None:
                return _xlsx_response(saved, f'{job.marketplace}-{job.mode}-results.xlsx')
        columns = _export_columns(job)
        rows = _iter_product_rows(job)
        if export_format == 'csv':
            return _csv_response(csv_chunks(columns, rows), f'{job.marketplace}-{job.mode}-results.csv')
        handle = tempfile.TemporaryFile()
        try:
            write_workbook(columns, rows, handle)
        except Exception:
            handle.close()
            raise
        handle.seek(0)
        return _xlsx_response(handle, f'{job.marketplace}-{job.mode}-results.xlsx')


class DiscoveryJobCancelView(APIView):
    permission_classes = [IsAuthenticated, CanUseDiscovery]

    def post(self, request, job_id):
        job = _jobs_for(request.user).filter(id=job_id).first()
        if job is None:
            return Response({'detail': 'Job not found.'}, status=status.HTTP_404_NOT_FOUND)
        if job.status not in (DiscoveryJob.Status.QUEUED, DiscoveryJob.Status.RUNNING):
            return Response({'detail': 'This scrape is not running.'}, status=status.HTTP_400_BAD_REQUEST)
        task_id = job.celery_task_id
        job.cancel_requested = True
        if job.status == DiscoveryJob.Status.QUEUED:
            # Not on the worker yet. Close it here so the next file can start.
            job.status = DiscoveryJob.Status.CANCELLED
            job.error_message = 'Stopped.'
            job.finished_at = timezone.now()
            job.save(update_fields=['cancel_requested', 'status', 'error_message', 'finished_at'])
            _revoke_task(task_id)
            dispatch_next(request.user, job.marketplace)
        else:
            job.save(update_fields=['cancel_requested'])
            _revoke_task(task_id)
        job.refresh_from_db()
        return Response(_job_payload(job, positions_for(request.user).get(job.id, 0)))


class DiscoveryJobResumeView(APIView):
    """Continue a stopped scrape from the last product already saved."""

    permission_classes = [IsAuthenticated, CanUseDiscovery]

    def post(self, request, job_id):
        job = _jobs_for(request.user).filter(id=job_id).first()
        if job is None:
            return Response({'detail': 'Job not found.'}, status=status.HTTP_404_NOT_FOUND)
        if job.status != DiscoveryJob.Status.CANCELLED:
            return Response(
                {'detail': 'Continue is available after you stop a scrape.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        stats = dict(job.stats or {})
        stats['resume'] = True
        job.stats = stats
        job.cancel_requested = False
        job.status = DiscoveryJob.Status.QUEUED
        job.celery_task_id = ''
        job.error_message = ''
        job.finished_at = None
        job.save(update_fields=[
            'stats', 'cancel_requested', 'status', 'celery_task_id', 'error_message', 'finished_at',
        ])
        dispatch_next(request.user, job.marketplace)
        job.refresh_from_db()
        return Response(_job_payload(job, positions_for(request.user).get(job.id, 0)))


class DiscoveryJobClearView(APIView):
    """Drop this account's scrape history so the next upload starts clean."""

    permission_classes = [IsAuthenticated, CanUseDiscovery]

    def delete(self, request):
        jobs = list(_jobs_for(request.user))
        for job in jobs:
            if job.status in (DiscoveryJob.Status.QUEUED, DiscoveryJob.Status.RUNNING):
                job.cancel_requested = True
                job.save(update_fields=['cancel_requested'])
                _revoke_task(job.celery_task_id)
            _delete_job(job)
        return Response({'deleted': len(jobs)})


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
        if job.products.exists():
            stored = (
                job.products.order_by('created_at')
                .values_list('data', flat=True)
                .iterator(chunk_size=1000)
            )

            def rows():
                for data in stored:
                    value = str((data or {}).get(field) or '').strip()
                    if value:
                        yield {field: value}

            handle = tempfile.TemporaryFile()
            try:
                write_workbook([field], rows(), handle)
            except Exception:
                handle.close()
                raise
            handle.seek(0)
            filename = f'{job.marketplace}-category-{field}s.xlsx'
            return _xlsx_response(handle, filename)
        try:
            blob = job.read_result()
        except FileNotFoundError:
            blob = b''
        if not blob:
            return Response({'detail': 'Result file is not ready.'}, status=status.HTTP_404_NOT_FOUND)
        _columns, sheet_rows = read_result_sheet(io.BytesIO(blob))
        rows = [
            {field: row.get(field) or ''}
            for row in sheet_rows
            if str(row.get(field) or '').strip()
        ]
        return _xlsx_response(
            io.BytesIO(workbook_bytes([field], rows)),
            f'{job.marketplace}-category-{field}s.xlsx',
        )


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
        sheet_columns = ['url', 'asin', 'item_id', 'title', 'price', 'rating', 'review_count', 'category']
        chunks = [
            product_rows[index:index + limits.MAX_PRODUCT_ROWS]
            for index in range(0, len(product_rows), limits.MAX_PRODUCT_ROWS)
        ]
        stamp = timezone.now()
        normalized_rules = normalize_rules(rules if isinstance(rules, dict) else parent.rules)
        chosen_columns = columns if isinstance(columns, list) else []
        children = []
        for index, chunk in enumerate(chunks):
            payload = workbook_bytes(sheet_columns, chunk)
            suffix = '' if len(chunks) == 1 else f'-{index + 1}'
            child = DiscoveryJob(
                owner=request.user,
                parent=parent,
                marketplace=parent.marketplace,
                mode=DiscoveryJob.Mode.PRODUCT,
                original_filename=f'{parent.marketplace}-from-category{suffix}.xlsx',
                rules=normalized_rules,
                columns=chosen_columns,
                use_sample=bool(use_sample),
                zip_code=parent.zip_code,
                source_bytes=payload,
            )
            try:
                child.source_file.save(
                    f'{parent.id}-products{suffix}.xlsx',
                    ContentFile(payload),
                    save=False,
                )
            except OSError:
                pass
            child.save()
            DiscoveryJob.objects.filter(pk=child.pk).update(
                created_at=stamp + timedelta(microseconds=index),
            )
            children.append(child)
        dispatch_next(request.user, parent.marketplace)
        child = children[0]
        child.refresh_from_db()
        body = _job_payload(child, positions_for(request.user).get(child.id, 0))
        body['parts'] = len(children)
        return Response(body, status=status.HTTP_201_CREATED)
