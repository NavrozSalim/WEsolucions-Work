"""Auto Order Bot: build the command for the external order bot, run it in the background, read its workbooks.

The bot lives outside this repo (settings.AUTO_ORDER_BOT_DIR) and is only ever called as a program.
"""
from __future__ import annotations

import json
import shutil
import logging
import os
import subprocess
import sys
import threading
from datetime import timedelta
from pathlib import Path

import openpyxl
from django.conf import settings
from django.db import connection
from django.utils import timezone

from .models import AutoOrderRun, PurchaseAccount

log = logging.getLogger(__name__)

DEFAULT_BOT_DIR = r'G:\All Tools Created\Order bot'
MAX_ROWS = 5000
MAX_TEXT = 200_000
LOG_TAIL_LINES = 200

# Store.region -> bot script / bot region key
REGIONS = {'USA': ('run_us.py', 'us'), 'AU': ('run_au.py', 'au')}
# Bot marketplaces per store region
REGION_MARKETPLACES = {'USA': ('sears', 'reverb'), 'AU': ('kogan', 'bigw')}
# App marketplace code -> marketplace argument for the bot (MyDeal stores use the BigW export)
MARKETPLACE_TO_BOT = {'reverb': 'reverb', 'sears': 'sears', 'kogan': 'kogan', 'mydeal': 'bigw'}
SUPPORTED_TEXT = 'Auto Order Bot supports Reverb, Sears, Kogan, and BigW only.'


def bot_dir() -> Path:
    return Path(getattr(settings, 'AUTO_ORDER_BOT_DIR', '') or DEFAULT_BOT_DIR)


def bot_python() -> str:
    return getattr(settings, 'AUTO_ORDER_BOT_PYTHON', '') or sys.executable


def bot_marketplace(store) -> str | None:
    """Marketplace argument for this store, or None when the store is not supported."""
    code = str(getattr(store.marketplace, 'code', '') or '').strip().lower()
    mp = MARKETPLACE_TO_BOT.get(code)
    if mp and mp in REGION_MARKETPLACES.get(store.region, ()):
        return mp
    return None


def region_vendors(region: str) -> tuple:
    return PurchaseAccount.REGION_VENDORS.get(region, ())


def build_command(store, marketplace, account_name, orders, mapping, accounts_file, output_dir, place) -> list[str]:
    script, _ = REGIONS[store.region]
    cmd = [bot_python(), script, '--marketplace', marketplace, '--orders', str(orders), '--mapping', str(mapping),
           '--account', account_name]
    if place:
        cmd.append('--place')
    cmd += ['--accounts-file', str(accounts_file), '--output-dir', str(output_dir)]
    return cmd


def write_accounts_file(user, path: Path) -> None:
    """accounts.json for this run: only this user's accounts, same shape as the bot's accounts.example.json."""
    data = {key: {v: [] for v in region_vendors(region)} for region, (_, key) in REGIONS.items()}
    for i, acct in enumerate(PurchaseAccount.objects.filter(user=user).order_by('created_at', 'id')):
        key = REGIONS[acct.region][1]
        entry = {'name': acct.name, 'chrome_profile': acct.chrome_profile, 'email': acct.email,
                 'user_data_dir': acct.user_data_dir, 'debug_port': acct.debug_port or 9222 + i,
                 'chrome_path': acct.chrome_path}
        data[key].setdefault(acct.vendor, []).append(entry)
    path.write_text(json.dumps(data, indent=2), encoding='utf-8')


def read_workbook(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        rows = wb.worksheets[0].iter_rows(values_only=True)
        header = [str(c or '') for c in next(rows, ())]
        out = []
        for row in rows:
            out.append({h: ('' if v is None else v if isinstance(v, (int, float)) else str(v))
                        for h, v in zip(header, row)})
            if len(out) >= MAX_ROWS:
                break
        wb.close()
        return out
    except Exception:
        log.exception('could not read %s', path)
        return []


def output_paths(run: AutoOrderRun) -> dict:
    out = Path(run.run_dir) / 'output'
    return {'done': out / f'{run.marketplace}-done.xlsx', 'remaining': out / f'{run.marketplace}-remaining.xlsx',
            'log': out / f'{run.marketplace}-log.txt'}


def read_log(run: AutoOrderRun) -> str:
    p = output_paths(run)['log']
    try:
        return p.read_text(encoding='utf-8', errors='replace')[-MAX_TEXT:] if p.exists() else ''
    except OSError:
        return ''


def log_tail(text: str, lines: int = LOG_TAIL_LINES) -> str:
    return '\n'.join((text or '').splitlines()[-lines:])


def finish_run(run: AutoOrderRun, exit_code: int | None, stdout: str) -> None:
    run.exit_code = exit_code
    run.stdout = (stdout or '')[-MAX_TEXT:]
    run.status = (AutoOrderRun.Status.FINISHED if exit_code == 0 else
                  AutoOrderRun.Status.REJECTED if exit_code == 2 else AutoOrderRun.Status.FAILED)
    paths = output_paths(run)
    run.done_rows = read_workbook(paths['done'])
    run.remaining_rows = read_workbook(paths['remaining'])
    run.log_text = read_log(run)
    run.finished_at = timezone.now()
    run.save()


def execute_run(run_id) -> None:
    """Blocking: spawn the bot, wait for it, store what it wrote. Runs in a background thread."""
    try:
        run = AutoOrderRun.objects.get(pk=run_id)
        cmd = json.loads(run.command)
        env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
        try:
            proc = subprocess.Popen(cmd, cwd=str(bot_dir()), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding='utf-8', errors='replace', env=env)
            out, _ = proc.communicate()
            finish_run(run, proc.returncode, out)
        except Exception as e:  # could not start the bot at all
            log.exception('auto order bot could not start')
            finish_run(run, None, f'Could not start the order bot: {e}')
    finally:
        if not getattr(settings, 'AUTO_ORDER_BOT_INLINE', False):
            connection.close()


def runner_mode() -> str:
    """'laptop' (default): runs wait for a laptop agent. 'local': Django spawns the bot itself."""
    return getattr(settings, 'AUTO_ORDER_BOT_RUNNER', 'laptop')


ACTIVE = (AutoOrderRun.Status.PENDING, AutoOrderRun.Status.RUNNING)
LAPTOP_SCOPE = 'order-bot'
ONLINE_SECONDS = 120


def laptop_online(user) -> bool:
    """True when an active order-bot token usable by this user called in during the last two minutes."""
    from catalog.models import IngestToken
    since = timezone.now() - timedelta(seconds=ONLINE_SECONDS)
    qs = IngestToken.objects.filter(is_active=True, last_used_at__gte=since)
    return any(LAPTOP_SCOPE in (t.scopes or []) and t.created_by_id in (None, user.id) for t in qs)


def save_laptop_files(run: AutoOrderRun, files: dict) -> None:
    """Store done / remaining / log uploaded by the laptop where the page's readers expect them."""
    paths = output_paths(run)
    paths['done'].parent.mkdir(parents=True, exist_ok=True)
    for kind, upload in files.items():
        if kind in paths and upload:
            with open(paths[kind], 'wb') as fh:
                for chunk in upload.chunks():
                    fh.write(chunk)


def launch(run: AutoOrderRun) -> None:
    if runner_mode() != 'local':
        return  # a laptop agent claims it through /ingest/order-bot/next-job/
    if getattr(settings, 'AUTO_ORDER_BOT_INLINE', False):
        execute_run(run.pk)
    else:
        threading.Thread(target=execute_run, args=(run.pk,), daemon=True, name=f'order-bot-{run.pk}').start()


def expire_stale(runs) -> None:
    """A server restart kills the watcher thread; do not leave such runs 'running' forever."""
    hours = getattr(settings, 'AUTO_ORDER_BOT_STALE_HOURS', 12)
    cutoff = timezone.now() - timedelta(hours=hours)
    for r in runs.filter(status__in=ACTIVE, started_at__lt=cutoff):
        r.status = AutoOrderRun.Status.FAILED
        r.stdout = (r.stdout or '') + '\nRun was still marked running after the stale limit; assumed interrupted.'
        r.finished_at = timezone.now()
        r.save()


# ---- saved files: mapping is kept until replaced, the order export is kept for one day -----------------
KINDS = ('orders', 'mapping')


def store_dir(store) -> Path:
    return Path(settings.MEDIA_ROOT) / 'auto_order_bot' / str(store.id)


def current_dir(store) -> Path:
    return store_dir(store) / 'current'


def current_path(store, kind: str) -> Path:
    return current_dir(store) / f'{kind}.xlsx'


def _meta_path(store) -> Path:
    return current_dir(store) / 'meta.json'


def _read_meta(store) -> dict:
    try:
        return json.loads(_meta_path(store).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def save_upload(store, kind: str, upload) -> None:
    """Store (or replace) the store's mapping / today's order export."""
    d = current_dir(store)
    d.mkdir(parents=True, exist_ok=True)
    with open(current_path(store, kind), 'wb') as fh:
        for chunk in upload.chunks():
            fh.write(chunk)
    meta = _read_meta(store)
    meta[kind] = {'name': upload.name, 'uploaded_at': timezone.now().isoformat(),
                  'date': timezone.localdate().isoformat()}
    _meta_path(store).write_text(json.dumps(meta), encoding='utf-8')


def delete_saved(store, kind: str) -> None:
    current_path(store, kind).unlink(missing_ok=True)
    meta = _read_meta(store)
    if meta.pop(kind, None) is not None:
        _meta_path(store).write_text(json.dumps(meta), encoding='utf-8')


def saved_files(store) -> dict:
    """{'orders': {...}|None, 'mapping': {...}|None} after dropping an order file from a previous day."""
    purge_expired(store)
    meta = _read_meta(store)
    return {k: (meta[k] if k in meta and current_path(store, k).exists() else None) for k in KINDS}


def purge_expired(store) -> bool:
    """Delete the order export once its day is over (never while a run for the store is going)."""
    if AutoOrderRun.objects.filter(store=store, status__in=ACTIVE).exists():
        return False
    meta = _read_meta(store)
    entry = meta.get('orders')
    stale = current_path(store, 'orders').exists() and (
        not entry or entry.get('date') != timezone.localdate().isoformat())
    if stale:
        delete_saved(store, 'orders')
    # Old runs: drop the generated accounts file and screenshots (they can show customer addresses).
    today = timezone.localdate()
    for run in AutoOrderRun.objects.filter(store=store).exclude(status__in=ACTIVE):
        if timezone.localtime(run.started_at).date() < today and run.run_dir:
            rd = Path(run.run_dir)
            (rd / 'accounts.json').unlink(missing_ok=True)
            shutil.rmtree(rd / 'output' / 'screenshots', ignore_errors=True)
    return stale
