"""Laptop-facing endpoints for the Auto Order Bot (same bearer-token contract as the other ingest runners).

Token scope: "order-bot". A token with an owner only sees that owner's runs.

  GET  /api/v1/ingest/order-bot/next-job/                    claim the oldest pending run
  GET  /api/v1/ingest/order-bot/jobs/<id>/                   status probe (cancelled -> stop)
  GET  /api/v1/ingest/order-bot/jobs/<id>/file/<orders|mapping|accounts>/
  POST /api/v1/ingest/order-bot/jobs/<id>/progress/          multipart: done, remaining, log (optional)
  POST /api/v1/ingest/order-bot/jobs/<id>/complete/          multipart: exit_code, stdout, done, remaining, log
"""
from __future__ import annotations

import logging
from pathlib import Path

from django.db import transaction
from django.http import FileResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog.ingest_views import IngestRateThrottle, _authenticate
from catalog.models import IngestToken

from . import auto_order_bot as bot
from .models import AutoOrderRun

logger = logging.getLogger(__name__)
KIND_FILES = {'orders': 'orders.xlsx', 'mapping': 'mapping.xlsx', 'accounts': 'accounts.json'}


class _Base(APIView):
    throttle_classes = [IngestRateThrottle]
    permission_classes = [AllowAny]
    authentication_classes = []
    parser_classes = [MultiPartParser, FormParser]

    def token(self, request) -> IngestToken:
        return _authenticate(request, required_scope=bot.LAPTOP_SCOPE)

    def owned_run(self, run_id, token):
        run = AutoOrderRun.objects.select_related('store').filter(pk=run_id).first()
        if run is None or (token.created_by_id and run.store.user_id != token.created_by_id):
            return None
        return run


def _not_found():
    return Response({'error': 'Run not found.'}, status=status.HTTP_404_NOT_FOUND)


class NextJobView(_Base):
    def get(self, request):
        token = self.token(request)
        with transaction.atomic():
            qs = AutoOrderRun.objects.select_for_update(skip_locked=True).filter(status=AutoOrderRun.Status.PENDING)
            if token.created_by_id:
                qs = qs.filter(store__user_id=token.created_by_id)
            run = qs.select_related('store').order_by('started_at').first()
            if run is not None:
                run.status = AutoOrderRun.Status.RUNNING
                run.claimed_at = timezone.now()
                run.laptop = token.label
                run.save(update_fields=['status', 'claimed_at', 'laptop'])
        if run is None:
            return Response({'job_id': None, 'checked_at': timezone.now().isoformat()})
        logger.info('Laptop %s claimed order-bot run %s', token.label, run.id)
        base = f'/api/v1/ingest/order-bot/jobs/{run.id}'
        return Response({
            'job_id': str(run.id),
            'region': bot.REGIONS[run.store.region][1],     # 'us' or 'au' -> run_us.py / run_au.py
            'marketplace': run.marketplace,
            'account': run.account_name,
            'place': run.place,
            'store_name': run.store.name,
            'files': {k: f'{base}/file/{k}/' for k in KIND_FILES},
            'status_url': f'{base}/',
            'progress_url': f'{base}/progress/',
            'complete_url': f'{base}/complete/',
        })


class JobStatusView(_Base):
    def get(self, request, run_id):
        run = self.owned_run(run_id, self.token(request))
        return _not_found() if run is None else Response({'status': run.status})


class JobFileView(_Base):
    def get(self, request, run_id, kind):
        run = self.owned_run(run_id, self.token(request))
        if run is None or kind not in KIND_FILES:
            return _not_found()
        name = KIND_FILES[kind]
        path = Path(run.run_dir) / name if kind == 'accounts' else bot.current_path(run.store, kind)
        if not path.exists():
            return _not_found()
        return FileResponse(open(path, 'rb'), as_attachment=True, filename=name)


def _files(request) -> dict:
    return {k: request.FILES.get(k) for k in ('done', 'remaining', 'log')}


class JobProgressView(_Base):
    def post(self, request, run_id):
        run = self.owned_run(run_id, self.token(request))
        if run is None:
            return _not_found()
        if run.status in (AutoOrderRun.Status.RUNNING, AutoOrderRun.Status.CANCELLED):
            bot.save_laptop_files(run, _files(request))
        return Response({'status': run.status})


class JobCompleteView(_Base):
    def post(self, request, run_id):
        run = self.owned_run(run_id, self.token(request))
        if run is None:
            return _not_found()
        if run.status not in (AutoOrderRun.Status.RUNNING, AutoOrderRun.Status.CANCELLED):
            return Response({'error': 'Run is not active.'}, status=status.HTTP_409_CONFLICT)
        try:
            code = int(request.data.get('exit_code'))
        except (TypeError, ValueError):
            return Response({'error': 'exit_code is required.'}, status=status.HTTP_400_BAD_REQUEST)
        bot.save_laptop_files(run, _files(request))
        was_cancelled = run.status == AutoOrderRun.Status.CANCELLED
        bot.finish_run(run, code, str(request.data.get('stdout', '')))
        if was_cancelled:  # keep what finished, but the run stays cancelled
            run.status = AutoOrderRun.Status.CANCELLED
            run.save(update_fields=['status'])
        return Response({'status': run.status})
