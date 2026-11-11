"""Auto Order Bot API, under /api/v1/stores/<store_id>/auto-order-bot/.

  GET/PATCH  .../                     store info + automate flag
  GET/POST   .../accounts/            purchase accounts for the store region
  DELETE     .../accounts/<id>/
  GET/POST   .../runs/                POST is multipart: orders, mapping, account, place
  GET        .../runs/<id>/
  GET        .../runs/<id>/download/<done|remaining|log>/
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from django.conf import settings
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from users.drf_permissions import HasProductPermission
from users.org_scope import stores_for_user

from . import auto_order_bot as bot
from .models import AutoOrderRun, PurchaseAccount

DOWNLOADS = {'done': 'xlsx', 'remaining': 'xlsx', 'log': 'txt'}


class _StoreBotView(APIView):
    """Same rules as the store endpoints: members read, the 'stores' permission mutates."""
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    required_product_permission = 'stores'

    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [IsAuthenticated()]
        return [IsAuthenticated(), HasProductPermission()]

    def get_store(self, store_pk):
        return get_object_or_404(stores_for_user(self.request.user).select_related('marketplace'), pk=store_pk)


def _truthy(v) -> bool:
    return str(v).strip().lower() in ('1', 'true', 'yes', 'on')


LEGACY_GONE = (
    'Orders are now fetched, checked for stock and profit, and bought one at a time from the Orders page '
    "(choose an Auto buying vendor on the store's Auto Purchasing step)."
)


def _legacy_runs() -> bool:
    from django.conf import settings as dj_settings

    return bool(getattr(dj_settings, 'AUTO_ORDER_BOT_LEGACY_RUNS', False))


def _legacy_gone():
    return Response({'detail': LEGACY_GONE}, status=status.HTTP_410_GONE)


def _store_info(store, user=None) -> dict:
    mp = bot.bot_marketplace(store)
    return {
        'store_id': str(store.id),
        'store_name': store.name,
        'marketplace_name': getattr(store.marketplace, 'name', '') or '',
        'marketplace_code': getattr(store.marketplace, 'code', '') or '',
        'region': store.region,
        'automate': bool(store.auto_order_bot_enabled),
        'supported': mp is not None,
        'bot_marketplace': mp,
        'vendors': list(bot.region_vendors(store.region)),
        'message': '' if mp else bot.SUPPORTED_TEXT,
        'files': bot.saved_files(store),
        'runner': bot.runner_mode(),
        'laptop_online': bot.laptop_online(user) if user else False,
        'legacy_runs': _legacy_runs(),
        'auto_buy_vendor': store.auto_buy_vendor or '',
        'auto_buy_label': dict(store.AUTO_BUY_VENDOR_CHOICES).get(store.auto_buy_vendor, ''),
    }


def _account_json(a: PurchaseAccount) -> dict:
    return {'id': str(a.id), 'name': a.name, 'vendor': a.vendor, 'region': a.region, 'email': a.email,
            'chrome_profile': a.chrome_profile, 'user_data_dir': a.user_data_dir, 'debug_port': a.debug_port,
            'chrome_path': a.chrome_path}


def _run_json(run: AutoOrderRun, detail=False) -> dict:
    data = {
        'id': str(run.id), 'status': run.status, 'place': run.place, 'marketplace': run.marketplace,
        'account_name': run.account_name, 'vendor': run.vendor, 'exit_code': run.exit_code,
        'started_at': run.started_at.isoformat(), 'finished_at': run.finished_at.isoformat() if run.finished_at else None,
        'done_count': len(run.done_rows or []), 'remaining_count': len(run.remaining_rows or []),
        'kind': run.kind, 'message': run.message or '', 'laptop': run.laptop or '',
        'order_id': str(run.marketplace_order_id) if run.marketplace_order_id else None,
    }
    if detail:
        running = run.status in bot.ACTIVE
        # The bot rewrites its files after every line, so a running job shows live progress.
        done = bot.read_workbook(bot.output_paths(run)['done']) if running else run.done_rows
        remaining = bot.read_workbook(bot.output_paths(run)['remaining']) if running else run.remaining_rows
        log_text = bot.read_log(run) if running else run.log_text
        data.update({'stdout': run.stdout, 'log_tail': bot.log_tail(log_text), 'done_rows': done,
                     'remaining_rows': remaining, 'done_count': len(done), 'remaining_count': len(remaining)})
    return data


class SettingsView(_StoreBotView):
    def get(self, request, store_pk):
        return Response(_store_info(self.get_store(store_pk), request.user))

    def patch(self, request, store_pk):
        store = self.get_store(store_pk)
        if 'automate' not in request.data:
            return Response({'detail': 'automate is required.'}, status=status.HTTP_400_BAD_REQUEST)
        store.auto_order_bot_enabled = _truthy(request.data['automate'])
        store.save(update_fields=['auto_order_bot_enabled'])
        return Response(_store_info(store, request.user))


class FileView(_StoreBotView):
    """PUT/POST replaces the store's mapping or today's order export; DELETE removes it."""

    def _kind(self, kind):
        return kind if kind in bot.KINDS else None

    def post(self, request, store_pk, kind):
        if not _legacy_runs():
            return _legacy_gone()
        store = self.get_store(store_pk)
        up = request.FILES.get('file')
        if not self._kind(kind) or not up or not up.name.lower().endswith('.xlsx'):
            return Response({'detail': 'Send an .xlsx file as "file" to orders or mapping.'}, status=status.HTTP_400_BAD_REQUEST)
        if AutoOrderRun.objects.filter(store=store, status__in=bot.ACTIVE).exists():
            return Response({'detail': 'A run for this store is going; wait for it to finish.'}, status=status.HTTP_409_CONFLICT)
        bot.save_upload(store, kind, up)
        return Response(bot.saved_files(store), status=status.HTTP_201_CREATED)

    put = post

    def delete(self, request, store_pk, kind):
        store = self.get_store(store_pk)
        if not self._kind(kind):
            return Response(status=status.HTTP_404_NOT_FOUND)
        bot.delete_saved(store, kind)
        return Response(bot.saved_files(store))


class AccountsView(_StoreBotView):
    def get(self, request, store_pk):
        store = self.get_store(store_pk)
        accts = PurchaseAccount.objects.filter(user=request.user, region=store.region)
        return Response({'accounts': [_account_json(a) for a in accts], 'vendors': list(bot.region_vendors(store.region))})

    def post(self, request, store_pk):
        store = self.get_store(store_pk)
        d = request.data
        name, vendor = str(d.get('name', '')).strip(), str(d.get('vendor', '')).strip().lower()
        if not name:
            return Response({'detail': 'name is required.'}, status=status.HTTP_400_BAD_REQUEST)
        if vendor not in bot.region_vendors(store.region):
            return Response({'detail': f'vendor must be one of: {", ".join(bot.region_vendors(store.region))}.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if not (str(d.get('chrome_profile', '')).strip() or str(d.get('user_data_dir', '')).strip()):
            return Response({'detail': 'chrome_profile or user_data_dir is required.'}, status=status.HTTP_400_BAD_REQUEST)
        if PurchaseAccount.objects.filter(user=request.user, name=name).exists():
            return Response({'detail': 'You already have an account with that name.'}, status=status.HTTP_400_BAD_REQUEST)
        port = d.get('debug_port')
        try:
            port = int(port) if str(port or '').strip() else None
        except ValueError:
            return Response({'detail': 'debug_port must be a number.'}, status=status.HTTP_400_BAD_REQUEST)
        acct = PurchaseAccount.objects.create(
            user=request.user, name=name, vendor=vendor, region=store.region, debug_port=port,
            **{k: str(d.get(k, '')).strip() for k in ('email', 'chrome_profile', 'user_data_dir', 'chrome_path')})
        return Response(_account_json(acct), status=status.HTTP_201_CREATED)


class MyPurchaseAccountsView(APIView):
    """Store wizard (Auto Purchasing step): the signed-in user's purchase accounts, filtered by region/vendor."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = PurchaseAccount.objects.filter(user=request.user)
        region = (request.query_params.get('region') or '').strip()
        vendor = (request.query_params.get('vendor') or '').strip()
        if region:
            qs = qs.filter(region=region)
        if vendor:
            qs = qs.filter(vendor=vendor)
        return Response({'accounts': [_account_json(a) for a in qs]})


class AccountDetailView(_StoreBotView):
    def delete(self, request, store_pk, account_id):
        self.get_store(store_pk)
        acct = get_object_or_404(PurchaseAccount, pk=account_id, user=request.user)
        acct.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class RunsView(_StoreBotView):
    def get(self, request, store_pk):
        store = self.get_store(store_pk)
        runs = AutoOrderRun.objects.filter(store=store)
        bot.expire_stale(runs)
        bot.purge_expired(store)
        return Response({'runs': [_run_json(r) for r in runs[:50]]})

    def post(self, request, store_pk):
        if not _legacy_runs():
            return _legacy_gone()
        store = self.get_store(store_pk)
        bad = lambda msg, code=status.HTTP_400_BAD_REQUEST: Response({'detail': msg}, status=code)  # noqa: E731
        if not store.auto_order_bot_enabled:
            return bad('Turn on "Automate the order process" for this store first.')
        marketplace = bot.bot_marketplace(store)
        if marketplace is None:
            return bad(bot.SUPPORTED_TEXT)
        acct = PurchaseAccount.objects.filter(user=request.user, name=str(request.data.get('account', '')).strip()).first()
        if acct is None:
            return bad('Choose one of your own purchase accounts.')
        if acct.region != store.region or acct.vendor not in bot.region_vendors(store.region):
            return bad('That purchase account belongs to a different region than this store.')
        for key in bot.KINDS:  # optional here: an upload replaces the saved file
            f = request.FILES.get(key)
            if f and not f.name.lower().endswith('.xlsx'):
                return bad(f'{f.name}: only .xlsx files are accepted.')
        runs = AutoOrderRun.objects.filter(store=store)
        bot.expire_stale(runs)
        if runs.filter(status__in=bot.ACTIVE).exists():
            return bad('A run for this store is already going.', status.HTTP_409_CONFLICT)

        for key in bot.KINDS:
            if request.FILES.get(key):
                bot.save_upload(store, key, request.FILES[key])
        saved = bot.saved_files(store)
        if not saved['orders']:
            return bad("Upload today's order export first (order files are kept for one day).")
        if not saved['mapping']:
            return bad('Upload the mapping workbook first.')

        run_id = uuid.uuid4()
        run_dir = Path(settings.MEDIA_ROOT) / 'auto_order_bot' / str(store.id) / str(run_id)
        (run_dir / 'output').mkdir(parents=True)
        files = {k: bot.current_path(store, k) for k in bot.KINDS}
        accounts_file = run_dir / 'accounts.json'
        bot.write_accounts_file(request.user, accounts_file)
        place = _truthy(request.data.get('place', False))
        cmd = bot.build_command(store, marketplace, acct.name, files['orders'], files['mapping'], accounts_file,
                                run_dir / 'output', place)
        import json
        run = AutoOrderRun.objects.create(
            id=run_id, store=store, user=request.user, account_name=acct.name, vendor=acct.vendor,
            marketplace=marketplace, place=place, command=json.dumps(cmd), run_dir=str(run_dir),
            status=(AutoOrderRun.Status.RUNNING if bot.runner_mode() == 'local' else AutoOrderRun.Status.PENDING))
        bot.launch(run)
        run.refresh_from_db()
        return Response(_run_json(run, detail=True), status=status.HTTP_201_CREATED)


class RunDetailView(_StoreBotView):
    def get(self, request, store_pk, run_id):
        store = self.get_store(store_pk)
        run = get_object_or_404(AutoOrderRun, pk=run_id, store=store)
        return Response(_run_json(run, detail=True))


class RunCancelView(_StoreBotView):
    def post(self, request, store_pk, run_id):
        run = get_object_or_404(AutoOrderRun, pk=run_id, store=self.get_store(store_pk))
        if run.status not in bot.ACTIVE:
            return Response({'detail': 'That run is not active.'}, status=status.HTTP_409_CONFLICT)
        run.status = AutoOrderRun.Status.CANCELLED
        run.finished_at = timezone.now()
        run.done_rows = bot.read_workbook(bot.output_paths(run)['done'])
        run.remaining_rows = bot.read_workbook(bot.output_paths(run)['remaining'])
        run.log_text = bot.read_log(run)
        run.save()
        return Response(_run_json(run, detail=True))


class RunDownloadView(_StoreBotView):
    def get(self, request, store_pk, run_id, kind):
        store = self.get_store(store_pk)
        if kind not in DOWNLOADS:
            return Response({'detail': 'kind must be done, remaining or log.'}, status=status.HTTP_404_NOT_FOUND)
        run = get_object_or_404(AutoOrderRun, pk=run_id, store=store)
        path = bot.output_paths(run)[kind]
        if not path.exists():
            return Response({'detail': 'File not written yet.'}, status=status.HTTP_404_NOT_FOUND)
        return FileResponse(open(path, 'rb'), as_attachment=True, filename=path.name)
