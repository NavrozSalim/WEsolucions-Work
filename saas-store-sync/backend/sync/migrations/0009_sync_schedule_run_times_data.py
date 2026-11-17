"""Fill ``SyncSchedule.run_times`` from the old Frequency (crontab / interval) fields.

Daily 10:00 -> ["10:00"]; every 12h at :00 -> ["00:00", "12:00"]; every 6h -> 4 times;
every 2h -> 12 times. ``last_triggered_at`` takes the old ``last_run`` so a run that
already happened this minute is not started again. Retry cycles stay 0 (same as before).
"""
from django.db import migrations

MAX_RUN_TIMES = 12


def _int_or(value, default):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _hours_from_field(field):
    field = str(field or '').strip() or '*'
    if field == '*':
        return list(range(0, 24, 2))
    hours = set()
    for part in field.split(','):
        part = part.strip()
        if not part:
            continue
        if '/' in part:
            base, step = part.split('/', 1)
            step = _int_or(step, 0)
            if step <= 0:
                continue
            start = 0 if base.strip() in ('*', '') else _int_or(base, 0)
            hours.update(range(max(0, start), 24, step))
        elif '-' in part:
            lo, hi = part.split('-', 1)
            hours.update(range(max(0, _int_or(lo, 0)), min(23, _int_or(hi, 23)) + 1))
        else:
            h = _int_or(part, -1)
            if 0 <= h <= 23:
                hours.add(h)
    return sorted(hours)


def run_times_from_old_fields(schedule_type, crontab_hour, crontab_minute, interval_seconds):
    if schedule_type == 'interval' and interval_seconds:
        step = max(1, round(int(interval_seconds) / 3600))
        if 24 / step > MAX_RUN_TIMES:
            step = 2
        hours, minute = list(range(0, 24, step)), 0
    else:
        hours = _hours_from_field(crontab_hour)
        minute = _int_or(crontab_minute, 0)
        if not 0 <= minute <= 59:
            minute = 0
    if not hours:
        hours = [10]
    return [f'{h:02d}:{minute:02d}' for h in hours][:MAX_RUN_TIMES]


def forwards(apps, schema_editor):
    SyncSchedule = apps.get_model('sync', 'SyncSchedule')
    for sched in SyncSchedule.objects.all().iterator():
        if sched.run_times:
            continue
        sched.run_times = run_times_from_old_fields(
            sched.schedule_type, sched.crontab_hour, sched.crontab_minute, sched.interval_seconds,
        )
        sched.last_triggered_at = sched.last_run
        sched.save(update_fields=['run_times', 'last_triggered_at'])


class Migration(migrations.Migration):

    dependencies = [
        ('sync', '0008_run_times_vendor_schedules'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
