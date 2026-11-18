"""Endpoints for MyDeal "Upload the file on the Marketplace" runs.

Web page (signed-in user):
  GET  /api/v1/stores/<store>/mydeal/upload-runs/                 recent runs
  POST /api/v1/stores/<store>/mydeal/upload-runs/                 {vendors: [...], kinds: [...], dry_run}
  GET  /api/v1/stores/<store>/mydeal/upload-runs/<run>/           one run with its log
  POST /api/v1/stores/<store>/mydeal/upload-runs/<run>/cancel/

Inventory bot laptop (bearer ingest token with scope "inventory-bot"; a token with an
owner only sees that owner's stores):
  GET  /api/v1/ingest/inventory-bot/next-job/
  GET  /api/v1/ingest/inventory-bot/jobs/<run>/                   status (cancelled -> stop)
  GET  /api/v1/ingest/inventory-bot/jobs/<run>/file/              the run's ZIP
  POST /api/v1/ingest/inventory-bot/jobs/<run>/progress/          {log, attention, results}
  POST /api/v1/ingest/inventory-bot/jobs/<run>/complete/          {exit_code, log, results, message}
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog.ingest_views import IngestRateThrottle, _authenticate
from catalog.models import MydealUploadRun
from catalog.mydeal_templates import store_is_mydeal
from catalog import mydeal_upload_runs as runs
from users.org_scope import get_store_for_user

logger = logging.getLogger(__name__)
RECENT_RUNS = 10


def _bad(msg, code=status.HTTP_400_BAD_REQUEST):
    return Response({'error': msg}, status=code)


def _mydeal_store(request, store_pk):
    store = get_store_for_user(request.user, store_pk, select_related=('marketplace',))
    return store if store_is_mydeal(store) else None


class UploadRunsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, store_pk):
        store = _mydeal_store(request, store_pk)
        if store is None:
            return _bad('Not a MyDeal store.')
        qs = MydealUploadRun.objects.filter(store=store).select_related('user')[:RECENT_RUNS]
        return Response({'runs': [runs.serialize_run(r, with_log=False) for r in qs]})

    def post(self, request, store_pk):
        store = _mydeal_store(request, store_pk)
        if store is None:
            return _bad('Not a MyDeal store.')
        try:
            run = runs.start_run(
                store,
                request.user,
                request.data.get('vendors') or [],
                request.data.get('kinds') or [],
                dry_run=str(request.data.get('dry_run', '')).lower() in ('1', 'true', 'yes'),
            )
        except runs.UploadRunError as exc:
            return _bad(str(exc))
        return Response(runs.serialize_run(run), status=status.HTTP_201_CREATED)


def _store_run(request, store_pk, run_id):
    store = _mydeal_store(request, store_pk)
    if store is None:
        return None
    return MydealUploadRun.objects.filter(store=store, pk=run_id).select_related('user').first()


class UploadRunDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, store_pk, run_id):
        run = _store_run(request, store_pk, run_id)
        if run is None:
            return _bad('Upload not found.', status.HTTP_404_NOT_FOUND)
        return Response(runs.serialize_run(run))


class UploadRunCancelView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, store_pk, run_id):
        run = _store_run(request, store_pk, run_id)
        if run is None:
            return _bad('Upload not found.', status.HTTP_404_NOT_FOUND)
        try:
            runs.cancel_run(run)
        except runs.UploadRunError as exc:
            return _bad(str(exc))
        return Response(runs.serialize_run(run))


# ---------- laptop ----------

class _LaptopBase(APIView):
    throttle_classes = [IngestRateThrottle]
    permission_classes = [AllowAny]
    authentication_classes = []
    parser_classes = [JSONParser]

    def token(self, request):
        return _authenticate(request, required_scope=runs.LAPTOP_SCOPE)

    def owned_run(self, request, run_id):
        token = self.token(request)
        run = MydealUploadRun.objects.select_related('store', 'store__marketplace').filter(pk=run_id).first()
        if run is None or (token.created_by_id and run.store.user_id != token.created_by_id):
            return None
        return run


def _not_found():
    return _bad('Job not found.', status.HTTP_404_NOT_FOUND)


class LaptopNextJobView(_LaptopBase):
    def get(self, request):
        token = self.token(request)
        with transaction.atomic():
            qs = MydealUploadRun.objects.select_for_update(skip_locked=True).filter(
                status=MydealUploadRun.Status.PENDING,
            )
            if token.created_by_id:
                qs = qs.filter(store__user_id=token.created_by_id)
            run = qs.order_by('started_at').first()
            if run is not None:
                now = timezone.now()
                run.status = MydealUploadRun.Status.RUNNING
                run.claimed_at = now
                run.last_seen_at = now
                run.laptop = token.label
                run.save(update_fields=['status', 'claimed_at', 'last_seen_at', 'laptop'])
        if run is None:
            return Response({'job_id': None, 'checked_at': timezone.now().isoformat()})
        run = MydealUploadRun.objects.select_related('store').get(pk=run.pk)
        logger.info('Laptop %s claimed MyDeal upload %s for %s', token.label, run.id, run.store.name)
        base = f'/api/v1/ingest/inventory-bot/jobs/{run.id}'
        return Response({
            'job_id': str(run.id),
            'store_name': run.store.name,
            'vendors': run.vendors,
            'kinds': run.kinds,
            'dry_run': run.dry_run,
            'include_unmapped': any(v.get('id') == runs.UNMAPPED_VENDOR for v in run.vendors or []),
            'status_url': f'{base}/',
            'file_url': f'{base}/file/',
            'progress_url': f'{base}/progress/',
            'complete_url': f'{base}/complete/',
        })


class LaptopJobStatusView(_LaptopBase):
    def get(self, request, run_id):
        run = self.owned_run(request, run_id)
        return _not_found() if run is None else Response({'status': run.status})


class LaptopJobFileView(_LaptopBase):
    def get(self, request, run_id):
        run = self.owned_run(request, run_id)
        if run is None:
            return _not_found()
        try:
            content, name, count = runs.run_zip(run)
        except ValueError as exc:
            return _bad(str(exc))
        if not count:
            return _bad('No files for the chosen vendors and file types.')
        resp = HttpResponse(content, content_type='application/zip')
        resp['Content-Disposition'] = f'attachment; filename="{name}"'
        return resp


class LaptopJobProgressView(_LaptopBase):
    def post(self, request, run_id):
        run = self.owned_run(request, run_id)
        if run is None:
            return _not_found()
        if run.status in (MydealUploadRun.Status.RUNNING, MydealUploadRun.Status.CANCELLED):
            runs.record_progress(run, request.data if isinstance(request.data, dict) else {})
        return Response({'status': run.status})


class LaptopJobCompleteView(_LaptopBase):
    def post(self, request, run_id):
        run = self.owned_run(request, run_id)
        if run is None:
            return _not_found()
        if run.status not in (MydealUploadRun.Status.RUNNING, MydealUploadRun.Status.CANCELLED):
            return _bad('Job is not active.', status.HTTP_409_CONFLICT)
        try:
            runs.finish_run(run, request.data if isinstance(request.data, dict) else {})
        except runs.UploadRunError as exc:
            return _bad(str(exc))
        return Response({'status': run.status})
