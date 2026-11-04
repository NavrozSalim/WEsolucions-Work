"""Laptop-facing endpoints for Auto Bot Excel jobs (Reverb and Etsy bots).

Same contract as the HEB/Costco desktop runner (see ``ingest_views``): an
ingest bearer token whose scope matches the vendor, ``next-job`` claims one
pending job for the token owner, a status probe for cancellation, and
``complete``. The difference is the payload: the laptop downloads an Excel,
runs the bot, and posts back the finished and remaining Excel files.

Routes (``vendor`` is 'reverb' or 'etsy'):
  GET  /api/v1/ingest/<vendor>/next-job/
  GET  /api/v1/ingest/<vendor>/jobs/<job_id>/
  GET  /api/v1/ingest/<vendor>/jobs/<job_id>/file/
  POST /api/v1/ingest/<vendor>/                       (multipart results)
  POST /api/v1/ingest/<vendor>/jobs/<job_id>/complete/
"""
from __future__ import annotations

import logging
import uuid

from django.core.files.base import ContentFile
from django.db import transaction
from django.http import FileResponse, Http404
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .excel_jobs import EXCEL_JOB_VENDORS, ExcelJobError, read_sheet
from .ingest_views import (
    IngestRateThrottle,
    _authenticate,
    _client_ip,
    _pick_pending_desktop_job,
)
from .models import HebScrapeJob, IngestToken

logger = logging.getLogger(__name__)

# Results may still be posted after a cancel so the page keeps what finished.
_RESULT_STATUSES = (HebScrapeJob.Status.CLAIMED, HebScrapeJob.Status.CANCELLED)


def _excel_cfg(vendor: str) -> dict:
    cfg = EXCEL_JOB_VENDORS.get((vendor or '').strip().lower())
    if cfg is None:
        raise Http404(f'Unknown Excel job vendor: {vendor}')
    return cfg


def _owned_job(job_id, vendor: str, token: IngestToken) -> HebScrapeJob | None:
    """The Excel job if it belongs to the token owner. ``None`` means 404."""
    try:
        job = HebScrapeJob.objects.select_related('store').get(
            id=job_id, vendor_code=vendor, job_type__gt=''
        )
    except (HebScrapeJob.DoesNotExist, ValueError):
        return None
    uid = token.created_by_id
    if uid is None:
        return job
    owner = job.store.user_id if job.store_id else job.requested_by_id
    return job if owner == uid else None


def _not_found() -> Response:
    return Response({'error': 'Job not found.'}, status=status.HTTP_404_NOT_FOUND)


class _ExcelIngestBase(APIView):
    throttle_classes = [IngestRateThrottle]
    permission_classes = [AllowAny]
    authentication_classes = []

    def _auth(self, request, vendor: str) -> IngestToken:
        cfg = _excel_cfg(vendor)
        return _authenticate(request, required_scope=cfg['scope'])


class ExcelIngestNextJobView(_ExcelIngestBase):
    """Claims the oldest pending Excel job for this vendor and token owner."""

    def get(self, request, vendor=None, *args, **kwargs):
        token = self._auth(request, vendor)
        with transaction.atomic():
            job = _pick_pending_desktop_job(vendor, token)
            if job is not None:
                job.status = HebScrapeJob.Status.CLAIMED
                job.claimed_at = timezone.now()
                job.claimed_by_token = token
                job.claimed_by_ip = _client_ip(request)
                job.save(update_fields=['status', 'claimed_at', 'claimed_by_token', 'claimed_by_ip'])

        if job is None:
            return Response({'job_id': None, 'checked_at': timezone.now().isoformat()})

        logger.info('Laptop %s claimed %s job %s', token.label, job.job_type, job.id)
        return Response({
            'job_id': str(job.id),
            'vendor': vendor,
            'job_type': job.job_type,
            'store_name': job.store_name,
            'file_name': job.input_name,
            'file_url': f'/api/v1/ingest/{vendor}/jobs/{job.id}/file/',
            'requested_at': job.requested_at.isoformat(),
        })


class ExcelIngestJobStatusView(_ExcelIngestBase):
    """Polled while the bot runs. ``cancelled`` or a 404 (deleted) means stop."""

    def get(self, request, job_id, vendor=None, *args, **kwargs):
        token = self._auth(request, vendor)
        job = _owned_job(job_id, vendor, token)
        if job is None:
            return _not_found()
        return Response({
            'job_id': str(job.id),
            'vendor': job.vendor_code,
            'job_type': job.job_type,
            'status': job.status,
            'requested_at': job.requested_at.isoformat(),
            'claimed_at': job.claimed_at.isoformat() if job.claimed_at else None,
            'completed_at': job.completed_at.isoformat() if job.completed_at else None,
            'cancelled': job.status == HebScrapeJob.Status.CANCELLED,
        })


class ExcelIngestJobFileView(_ExcelIngestBase):
    """The uploaded Excel, only for the laptop that claimed the job."""

    def get(self, request, job_id, vendor=None, *args, **kwargs):
        token = self._auth(request, vendor)
        job = _owned_job(job_id, vendor, token)
        if job is None or job.claimed_by_token_id != token.id or not job.input_file:
            return _not_found()
        return FileResponse(
            job.input_file.open('rb'),
            as_attachment=True,
            filename=job.input_name or 'input.xlsx',
        )


class ExcelIngestResultView(_ExcelIngestBase):
    """Laptop posts the finished and remaining Excel files the bot wrote.

    Multipart fields: ``job_id``, ``done_file``, ``remaining_file``,
    ``exit_code``, ``note``. A missing remaining file means nothing finished
    is known, so the original upload is kept as the remaining file.
    """

    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request, vendor=None, *args, **kwargs):
        token = self._auth(request, vendor)
        try:
            job_id = uuid.UUID(str(request.data.get('job_id') or ''))
        except ValueError:
            return Response({'error': 'job_id is required.'}, status=status.HTTP_400_BAD_REQUEST)
        job = _owned_job(job_id, vendor, token)
        if job is None or job.claimed_by_token_id != token.id:
            return _not_found()
        if job.status not in _RESULT_STATUSES:
            return Response({'error': f'Job is already {job.status}.'}, status=status.HTTP_409_CONFLICT)

        done = request.FILES.get('done_file')
        remaining = request.FILES.get('remaining_file')
        try:
            done_rows = read_sheet(done)[1] if done else 0
            remaining_rows = read_sheet(remaining)[1] if remaining else None
        except ExcelJobError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        stem = (job.input_name or 'input.xlsx').rsplit('.', 1)[0]
        if done:
            job.done_file.save(f'{job.id}_done_{done.name}', done, save=False)
        if remaining:
            job.remaining_file.save(f'{job.id}_remaining_{remaining.name}', remaining, save=False)
        elif job.input_file and not done:
            with job.input_file.open('rb') as fh:
                job.remaining_file.save(f'{job.id}_remaining_{stem}.xlsx', ContentFile(fh.read()), save=False)
            with job.input_file.open('rb') as fh:
                remaining_rows = read_sheet(ContentFile(fh.read(), name='input.xlsx'))[1]

        try:
            exit_code = int(request.data.get('exit_code'))
        except (TypeError, ValueError):
            exit_code = None
        job.done_count = done_rows
        job.remaining_count = remaining_rows or 0
        job.stats = {
            'done': job.done_count,
            'remaining': job.remaining_count,
            'total': job.done_count + job.remaining_count,
            'exit_code': exit_code,
        }
        note = str(request.data.get('note') or '')[:2000]
        if note:
            job.note = note
        job.save(update_fields=['done_file', 'remaining_file', 'done_count', 'remaining_count', 'stats', 'note'])
        return Response({'job_id': str(job.id), 'stats': job.stats})


class ExcelIngestCompleteJobView(_ExcelIngestBase):
    """Marks the claimed job done or failed. 409 once it is cancelled."""

    def post(self, request, job_id, vendor=None, *args, **kwargs):
        token = self._auth(request, vendor)
        job = _owned_job(job_id, vendor, token)
        if job is None:
            return _not_found()
        if job.status not in (HebScrapeJob.Status.CLAIMED, HebScrapeJob.Status.PENDING):
            return Response({'error': f'Job is already {job.status}.'}, status=status.HTTP_409_CONFLICT)

        payload = request.data if isinstance(request.data, dict) else {}
        wanted = (payload.get('status') or 'done').strip().lower()
        job.status = HebScrapeJob.Status.FAILED if wanted == 'failed' else HebScrapeJob.Status.DONE
        job.completed_at = timezone.now()
        note = str(payload.get('note') or '')[:2000]
        if note:
            job.note = note
        job.save(update_fields=['status', 'completed_at', 'note'])
        return Response({
            'job_id': str(job.id),
            'vendor': job.vendor_code,
            'status': job.status,
            'completed_at': job.completed_at.isoformat(),
        })
