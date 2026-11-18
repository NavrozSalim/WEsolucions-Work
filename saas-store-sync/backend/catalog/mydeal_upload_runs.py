"""MyDeal "Upload the file on the Marketplace": queue a run for the inventory bot laptop.

The web page starts a run with the vendors and file types (inventory / price) to upload.
The laptop agent (ingest token scope ``inventory-bot``) claims it, downloads a ZIP of just
those vendors' files (built at claim time, so prices and stock are current), uploads them
to the seller portal, and reports the log, per-file results and any "action needed" message.
"""
from __future__ import annotations

from typing import Any

from django.db import transaction
from django.utils import timezone

from catalog.models import MydealTemplateRow, MydealUploadRun
from catalog.mydeal_templates import (
    UNMAPPED_VENDOR,
    build_vendor_zip_bytes,
    mydeal_store_label,
    store_is_mydeal,
    vendor_template_summary,
)

LAPTOP_SCOPE = 'inventory-bot'
KINDS = (MydealTemplateRow.Kind.INVENTORY, MydealTemplateRow.Kind.PRICE)
ACTIVE = (MydealUploadRun.Status.PENDING, MydealUploadRun.Status.RUNNING)
LOG_LIMIT = 200_000
MAX_RESULTS = 1000
RESULT_FIELDS = (
    'vendor', 'kind', 'part', 'parts', 'rows', 'uploaded_at', 'status', 'message',
    'request_id', 'requested_at', 'dash_state', 'error_count',
)


class UploadRunError(ValueError):
    """Shown to the user when a run cannot be started or changed."""


def _clean_kinds(kinds) -> list[str]:
    out = []
    for k in kinds or []:
        k = str(k or '').strip().lower()
        if k in KINDS and k not in out:
            out.append(k)
    # Upload order on the laptop is per vendor: Stock/Status first, then Price.
    return [k for k in KINDS if k in out]


def start_run(store, user, vendor_ids, kinds, *, dry_run: bool = False) -> MydealUploadRun:
    if not store_is_mydeal(store):
        raise UploadRunError('Not a MyDeal store.')
    kinds = _clean_kinds(kinds)
    if not kinds:
        raise UploadRunError('Choose Inventory, Price or both.')
    wanted = [str(v).strip() for v in (vendor_ids or []) if str(v).strip()]
    if not wanted:
        raise UploadRunError('Choose at least one vendor.')

    summary = {v['id']: v for v in vendor_template_summary(store)}
    unknown = [v for v in wanted if v not in summary]
    if unknown:
        raise UploadRunError('Some chosen vendors have no template rows; reload and try again.')
    vendors = []
    for vid in wanted:
        v = summary[vid]
        rows = sum(v[f'{k}_rows'] for k in kinds)
        if rows and vid not in [x['id'] for x in vendors]:
            vendors.append({'id': vid, 'name': v['name']})
    if not vendors:
        label = ' and '.join(kinds)
        raise UploadRunError(f'The chosen vendors have no {label} template rows.')

    with transaction.atomic():
        active = (
            MydealUploadRun.objects.select_for_update()
            .filter(store=store, status__in=ACTIVE)
            .first()
        )
        if active is not None:
            raise UploadRunError(
                'An upload for this store is already waiting or running. '
                'Wait for it to finish or cancel it first.'
            )
        return MydealUploadRun.objects.create(
            store=store,
            user=user if getattr(user, 'is_authenticated', False) else None,
            vendors=vendors,
            kinds=kinds,
            dry_run=bool(dry_run),
            status=MydealUploadRun.Status.PENDING,
        )


def cancel_run(run: MydealUploadRun) -> MydealUploadRun:
    if run.status not in ACTIVE:
        raise UploadRunError('This upload has already ended.')
    was_pending = run.status == MydealUploadRun.Status.PENDING
    run.status = MydealUploadRun.Status.CANCELLED
    run.attention = ''
    fields = ['status', 'attention']
    if was_pending:   # no laptop will report back
        run.finished_at = timezone.now()
        run.message = 'Cancelled before the laptop started it.'
        fields += ['finished_at', 'message']
    run.save(update_fields=fields)
    return run


def run_zip(run: MydealUploadRun) -> tuple[bytes, str, int]:
    """ZIP of the run's vendors and file types; (bytes, file name, file count)."""
    vendor_ids = [v['id'] for v in run.vendors or []]
    content, count = build_vendor_zip_bytes(run.store, vendor_ids, tuple(_clean_kinds(run.kinds)))
    name = f'Mydeal - {mydeal_store_label(run.store)} - Upload {str(run.id)[:8]}.zip'
    return content, name, count


def _clean_results(raw) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw[:MAX_RESULTS]:
        if not isinstance(item, dict):
            continue
        row = {}
        for key in RESULT_FIELDS:
            value = item.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                row[key] = value
            elif value is None:
                row[key] = ''
            else:
                row[key] = str(value)[:300]
        out.append(row)
    return out


def _trim_log(text) -> str:
    text = str(text or '')
    return text[-LOG_LIMIT:] if len(text) > LOG_LIMIT else text


def record_progress(run: MydealUploadRun, data: dict) -> None:
    fields = ['last_seen_at']
    run.last_seen_at = timezone.now()
    if 'log' in data:
        run.log_text = _trim_log(data.get('log'))
        fields.append('log_text')
    if 'attention' in data:
        run.attention = str(data.get('attention') or '').strip()[:300]
        fields.append('attention')
    if 'results' in data:
        run.results = _clean_results(data.get('results'))
        fields.append('results')
    run.save(update_fields=fields)


def finish_run(run: MydealUploadRun, data: dict) -> None:
    try:
        code = int(data.get('exit_code'))
    except (TypeError, ValueError):
        raise UploadRunError('exit_code is required.') from None
    record_progress(run, data)
    run.exit_code = code
    run.attention = ''
    run.message = str(data.get('message') or '').strip()[:1000]
    run.finished_at = timezone.now()
    if run.status != MydealUploadRun.Status.CANCELLED:
        # 0 = every file completed, 1 = finished with some files failed / with errors.
        run.status = MydealUploadRun.Status.FINISHED if code in (0, 1) else MydealUploadRun.Status.FAILED
        if code == 130:
            run.status = MydealUploadRun.Status.CANCELLED
    if not run.message:
        run.message = {0: 'All files uploaded and completed.', 1: 'Finished; some files need a look.'}.get(
            code, f'The bot stopped (exit code {code}). See the log.')
    run.save(update_fields=['exit_code', 'attention', 'message', 'finished_at', 'status'])


def serialize_run(run: MydealUploadRun, *, with_log: bool = True) -> dict[str, Any]:
    data = {
        'id': str(run.id),
        'status': run.status,
        'status_label': run.get_status_display(),
        'vendors': run.vendors or [],
        'kinds': run.kinds or [],
        'dry_run': run.dry_run,
        'laptop': run.laptop,
        'exit_code': run.exit_code,
        'results': run.results or [],
        'message': run.message,
        'attention': run.attention,
        'started_at': run.started_at.isoformat() if run.started_at else None,
        'claimed_at': run.claimed_at.isoformat() if run.claimed_at else None,
        'last_seen_at': run.last_seen_at.isoformat() if run.last_seen_at else None,
        'finished_at': run.finished_at.isoformat() if run.finished_at else None,
        'started_by': getattr(run.user, 'email', '') if run.user_id else '',
    }
    if with_log:
        data['log'] = run.log_text
    return data


__all__ = [
    'ACTIVE', 'LAPTOP_SCOPE', 'UNMAPPED_VENDOR', 'UploadRunError', 'cancel_run', 'finish_run',
    'record_progress', 'run_zip', 'serialize_run', 'start_run',
]
