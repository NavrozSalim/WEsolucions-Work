"""Auto Bot page: queue Reverb/Etsy Excel jobs for the laptops and read the results.

  GET    /api/v1/auto-bot/jobs/
  POST   /api/v1/auto-bot/jobs/                     (multipart: job_type, file, store_name)
  DELETE /api/v1/auto-bot/jobs/<id>/
  POST   /api/v1/auto-bot/jobs/<id>/cancel/
  GET    /api/v1/auto-bot/jobs/<id>/download/<input|done|remaining>/
  GET    /api/v1/auto-bot/laptops/
"""
from __future__ import annotations

from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .excel_ingest_views import token_stores
from .excel_jobs import (
    EXCEL_JOB_VENDORS,
    JOB_TYPES,
    ExcelJobError,
    delete_job_files,
    split_by_store,
    validate_upload,
)
from .models import HebScrapeJob, IngestToken

_DOWNLOADS = {'input': 'input_file', 'done': 'done_file', 'remaining': 'remaining_file'}
_LIST_LIMIT = 200


def _excel_jobs():
    return HebScrapeJob.objects.filter(vendor_code__in=list(EXCEL_JOB_VENDORS), job_type__gt='')


def _user_jobs(user):
    return _excel_jobs().filter(requested_by=user).select_related('claimed_by_token')


def _queue_positions() -> dict:
    """Place of each pending job in its store's queue.

    Each store has its own laptop, so an IQ job only waits behind older IQ jobs
    (Etsy and Reverb together), from any user.
    """
    pending = (
        _excel_jobs()
        .filter(status=HebScrapeJob.Status.PENDING)
        .order_by('requested_at')
        .values_list('id', 'store_name')
    )
    seen: dict[str, int] = {}
    positions = {}
    for job_id, store in pending:
        seen[store] = seen.get(store, 0) + 1
        positions[job_id] = seen[store]
    return positions


def _laptop_tokens(user) -> list[IngestToken]:
    """Active laptop tokens this user's jobs can run on: shared ones (no owner) and the user's own."""
    tokens = (
        IngestToken.objects.filter(is_active=True)
        .filter(Q(created_by=user) | Q(created_by__isnull=True))
        .order_by('label')
    )
    return [tok for tok in tokens if any(s in EXCEL_JOB_VENDORS for s in (tok.scopes or []))]


def _has_laptop(job: HebScrapeJob, tokens: list[IngestToken]) -> bool:
    for tok in tokens:
        stores = token_stores(tok)
        if job.vendor_code in (tok.scopes or []) and (not stores or job.store_name in stores):
            return True
    return False


def _serialize(job: HebScrapeJob, queue: dict | None = None, tokens: list[IngestToken] | None = None) -> dict:
    spec = JOB_TYPES.get(job.job_type, {})
    return {
        'queue_position': (queue or {}).get(job.id),
        'has_laptop': _has_laptop(job, tokens) if tokens is not None else True,
        'id': str(job.id),
        'vendor': job.vendor_code,
        'job_type': job.job_type,
        'job_label': spec.get('label', job.job_type),
        'store_name': job.store_name,
        'file_name': job.input_name,
        'status': job.status,
        'laptop': job.claimed_by_token.label if job.claimed_by_token_id else '',
        'requested_at': job.requested_at.isoformat(),
        'claimed_at': job.claimed_at.isoformat() if job.claimed_at else None,
        'completed_at': job.completed_at.isoformat() if job.completed_at else None,
        'done_count': job.done_count,
        'remaining_count': job.remaining_count,
        'has_done_file': bool(job.done_file),
        'has_remaining_file': bool(job.remaining_file),
        'note': job.note,
    }


class AutoBotJobListCreateView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        jobs = _user_jobs(request.user).order_by('-requested_at')[:_LIST_LIMIT]
        queue = _queue_positions()
        tokens = _laptop_tokens(request.user)
        return Response({
            'job_types': [{'value': key, 'label': spec['label']} for key, spec in JOB_TYPES.items()],
            'jobs': [_serialize(job, queue, tokens) for job in jobs],
        })

    def post(self, request):
        job_type = str(request.data.get('job_type') or '').strip().lower()
        upload = request.FILES.get('file')
        store_name = str(request.data.get('store_name') or '').strip().upper()[:64]
        if job_type not in JOB_TYPES:
            return Response({'error': 'Choose a job type.'}, status=status.HTTP_400_BAD_REQUEST)
        if upload is None:
            return Response({'error': 'Choose an Excel file.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            validate_upload(job_type, upload)
            parts = split_by_store(upload, only_store=store_name)
        except ExcelJobError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        # One job per store: each store runs on the laptop assigned to it.
        stem = upload.name.rsplit('.', 1)[0]
        created = []
        with transaction.atomic():
            for store, content in sorted(parts.items()):
                name = f'{stem} - {store}.xlsx' if len(parts) > 1 else upload.name
                job = HebScrapeJob(
                    requested_by=request.user,
                    vendor_code=JOB_TYPES[job_type]['vendor'],
                    job_type=job_type,
                    store_name=store,
                    input_name=name[:255],
                )
                job.input_file.save(name, ContentFile(content), save=False)
                job.save()
                created.append(job)
        queue = _queue_positions()
        tokens = _laptop_tokens(request.user)
        return Response(
            {'jobs': [_serialize(job, queue, tokens) for job in created]},
            status=status.HTTP_201_CREATED,
        )


class AutoBotJobDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, job_id):
        job = _user_jobs(request.user).filter(id=job_id).first()
        if job is None:
            return Response({'error': 'Job not found.'}, status=status.HTTP_404_NOT_FOUND)
        delete_job_files(job)
        job.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AutoBotJobCancelView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, job_id):
        job = _user_jobs(request.user).filter(id=job_id).first()
        if job is None:
            return Response({'error': 'Job not found.'}, status=status.HTTP_404_NOT_FOUND)
        if job.status not in (HebScrapeJob.Status.PENDING, HebScrapeJob.Status.CLAIMED):
            return Response({'error': f'Job is already {job.status}.'}, status=status.HTTP_409_CONFLICT)
        job.status = HebScrapeJob.Status.CANCELLED
        job.completed_at = timezone.now()
        job.note = (job.note + '\n' if job.note else '') + 'Cancelled from the Auto Bot page.'
        job.save(update_fields=['status', 'completed_at', 'note'])
        return Response(_serialize(job))


class AutoBotJobDownloadView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, job_id, kind):
        field_name = _DOWNLOADS.get(kind)
        job = _user_jobs(request.user).filter(id=job_id).first()
        if field_name is None or job is None:
            return Response({'error': 'File not found.'}, status=status.HTTP_404_NOT_FOUND)
        field = getattr(job, field_name)
        if not field:
            return Response({'error': 'File not found.'}, status=status.HTTP_404_NOT_FOUND)
        stem = (job.input_name or 'jobs.xlsx').rsplit('.', 1)[0]
        name = job.input_name if kind == 'input' else f'{stem} - {kind}.xlsx'
        return FileResponse(field.open('rb'), as_attachment=True, filename=name)


class AutoBotLaptopsView(APIView):
    """Laptops that can run this user's jobs: shared laptops (token with no owner) and the user's own.

    ``busy`` is true while the laptop holds a claimed job. The job name is only
    shown when that job belongs to the viewer.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        running = {
            job.claimed_by_token_id: job
            for job in _excel_jobs().filter(status=HebScrapeJob.Status.CLAIMED, claimed_by_token__isnull=False)
        }
        laptops = []
        for tok in _laptop_tokens(request.user):
            scopes = [s for s in (tok.scopes or []) if s in EXCEL_JOB_VENDORS]
            job = running.get(tok.id)
            mine = job is not None and job.requested_by_id == request.user.id
            laptops.append({
                'label': tok.label,
                'scopes': scopes,
                'stores': token_stores(tok),
                'shared': tok.created_by_id is None,
                'busy': job is not None,
                'job_label': JOB_TYPES.get(job.job_type, {}).get('label', '') if mine else '',
                'last_used_at': tok.last_used_at.isoformat() if tok.last_used_at else None,
            })
        return Response({'laptops': laptops, 'now': timezone.now().isoformat()})
