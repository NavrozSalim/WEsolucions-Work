#!/usr/bin/env python3
"""
Listing Runner: runs the Reverb and Etsy Excel bots for SellerPilot Hub (Auto Bot page).

The server is the master. This laptop:
  1. polls GET <INGEST_BASE_URL>reverb/next-job/ and .../etsy/next-job/ with its ingest token,
  2. downloads that job's Excel,
  3. runs the existing bot script with the Excel path (and --store when set),
  4. when the bot stops, uploads the finished and remaining Excel files and marks the job complete.

While a bot runs, GET .../jobs/<id>/ is checked every few seconds. When the job is
cancelled or deleted on the server, only that bot's Python run and its Chrome window
are stopped. The runner keeps going.

When the server is briefly unavailable (a deploy or restart: HTTP 502/503/504, or no
connection), the runner waits and tries again instead of failing:
  - idle polls stay quiet; one line is logged only if the outage lasts a few minutes,
    and another when the server is back,
  - the Excel download, result upload and "complete" call retry for about 5 minutes,
  - results that still cannot be sent are kept in jobs/<id>/ and sent once the server
    is back, so a finished run is never lost.

The bots, their stores.json, Chrome profiles and VPN settings are never changed here.

Usage:
  python poller.py              # run forever
  python poller.py --once       # handle at most one job, then exit
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin

import requests

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / 'ingest.env'
LOG_FILE = HERE / 'ingest.log'
JOBS_DIR = HERE / 'jobs'
PENDING_NAME = 'pending_report.json'

DEFAULT_POLL_INTERVAL = 20      # seconds between next-job polls when idle
DEFAULT_CANCEL_CHECK_EVERY = 5  # seconds between GET /jobs/<id>/ probes
DEFAULT_TIMEOUT = 60
VENDORS = ('reverb', 'etsy')

# The server is restarting or overloaded: wait and try again.
TRANSIENT_STATUSES = {502, 503, 504}
# Waits between attempts; the last value repeats. 8 attempts ~ 5 minutes in total.
RETRY_DELAYS = (5, 15, 30, 60)
IMPORTANT_ATTEMPTS = 8
# Idle polls log nothing until the server has been unreachable this long.
OUTAGE_LOG_AFTER = 180

XLSX_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

# job_type -> (script under the bot folder, output folder under the bot folder, done prefix, remaining prefix)
JOB_TYPES: dict[str, tuple[str, str, str, str]] = {
    'reverb-create': (r'create_listing\create_listing.py', 'output', 'Created', 'Remaining'),
    'reverb-stock': (r'price_stock\update_price_stock.py', r'output\price_stock', 'Updated', 'remain-to-update'),
    'reverb-sku': (r'sku_replace\sku_replace.py', r'output\sku_replace', 'Updated', 'remain-to-update'),
    'etsy-create': (r'create_listing\create_listing.py', 'output', 'Created', 'Remaining'),
    'etsy-stock': (r'price_stock\update_price_stock.py', r'output\price_stock', 'Updated', 'remain-to-update'),
    'etsy-sku': (r'sku_replace\sku_replace.py', r'output\sku_replace', 'Updated', 'remain-to-update'),
}

EXIT_NOTES = {
    0: 'Bot finished.',
    2: 'Bot finished. Some rows failed; they are in the remaining file.',
    3: 'Bot stopped on a login or human check that was not finished in 5 minutes. Chrome was left open.',
    130: 'Bot was stopped by the user on the laptop.',
}


def log(msg: str) -> None:
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] [runner] {msg}'
    print(line, flush=True)
    try:
        with LOG_FILE.open('a', encoding='utf-8') as fh:
            fh.write(line + '\n')
    except Exception:
        pass


def load_env(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


# --------------------------------------------------------------------------- #
# HTTP (same rules as the Costco/HEB poller)                                  #
# --------------------------------------------------------------------------- #

class ServerUnavailable(Exception):
    """The server did not answer (no connection, timeout, or HTTP 502/503/504) after every attempt."""


def _normalize_api_url(api_url: str) -> str:
    """Force https:// on non-loopback hosts. A 301 from http would turn POST into GET."""
    url = (api_url or '').strip()
    if not url.lower().startswith('http://'):
        return url
    rest = url[len('http://'):]
    host = rest.split('/', 1)[0].split(':', 1)[0].lower()
    if host in ('localhost', '127.0.0.1', '::1') or host.endswith('.local'):
        return url
    return 'https://' + rest


def _endpoint(api_url: str, suffix: str) -> str:
    return api_url.rstrip('/') + '/' + suffix.lstrip('/')


def _short_body(resp: requests.Response) -> str:
    """One readable line from a response: the <title> of an HTML error page, else the start of the text."""
    try:
        text = (resp.text or '').strip()
    except Exception:
        return ''
    title = re.search(r'<title>(.*?)</title>', text, re.I | re.S)
    if title:
        return ' '.join(title.group(1).split())
    try:
        detail = resp.json().get('detail')
        if detail:
            return str(detail)[:200]
    except Exception:
        pass
    return ' '.join(text.split())[:200]


def _retry_after(resp: requests.Response | None) -> float:
    if resp is None:
        return 0.0
    try:
        return min(120.0, max(1.0, float(resp.headers.get('Retry-After', ''))))
    except (TypeError, ValueError):
        return 0.0


def _delay(attempt: int) -> float:
    return float(RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)])


class Server:
    def __init__(self, base_url: str, token: str, *, timeout: int, verify: bool):
        self.base = _normalize_api_url(base_url).rstrip('/') + '/'
        self.token = token
        self.timeout = timeout
        self.verify = verify
        self.outage_since: float | None = None
        self.outage_logged = False

    def _headers(self) -> dict[str, str]:
        return {'Authorization': f'Bearer {self.token}'}

    def vendor_url(self, vendor: str) -> str:
        return self.base + vendor + '/'

    def _send(
        self, method: str, url: str, *, what: str, attempts: int = 1, timeout: int | None = None,
        files_factory: Callable[[], tuple[dict, list]] | None = None, **kwargs,
    ) -> requests.Response:
        """Send a request, retrying while the server is unavailable.

        Returns any answer that is not 502/503/504 (the caller checks the status).
        Raises ServerUnavailable when every attempt failed that way.
        ``files_factory`` reopens upload files for each attempt.
        """
        last = ''
        for attempt in range(1, attempts + 1):
            resp = None
            handles: list = []
            try:
                extra = {}
                if files_factory is not None:
                    files, handles = files_factory()
                    extra['files'] = files or None
                resp = requests.request(
                    method, url, headers=self._headers(), timeout=timeout or self.timeout,
                    verify=self.verify, allow_redirects=False, **kwargs, **extra,
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                last = f'{type(exc).__name__}: {str(exc)[:150]}'
            finally:
                for fh in handles:
                    fh.close()
            if resp is not None and resp.status_code not in TRANSIENT_STATUSES:
                return resp
            if resp is not None:
                last = f'HTTP {resp.status_code} {_short_body(resp)}'.strip()
            if attempt == attempts:
                break
            wait = _retry_after(resp) or _delay(attempt)
            log(f'  {what}: server unavailable ({last}). Try {attempt + 1}/{attempts} in {wait:.0f}s.')
            time.sleep(wait)
        raise ServerUnavailable(f'{what}: {last}')

    def _get(self, url: str, *, what: str, attempts: int = 1, **kwargs) -> requests.Response:
        return self._send('GET', url, what=what, attempts=attempts, **kwargs)

    # -- outage bookkeeping for the quiet idle polls ---------------------------- #

    def _outage(self, reason: str) -> None:
        now = time.time()
        if self.outage_since is None:
            self.outage_since = now
        if not self.outage_logged and now - self.outage_since >= OUTAGE_LOG_AFTER:
            self.outage_logged = True
            log(f'Server unavailable for {int(now - self.outage_since)}s ({reason}). Still trying every poll.')

    def _reachable(self) -> None:
        if self.outage_since is not None and self.outage_logged:
            log(f'Server is back after {int(time.time() - self.outage_since)}s.')
        self.outage_since = None
        self.outage_logged = False

    # -- endpoints --------------------------------------------------------------- #

    def poll_next_job(self, vendor: str) -> dict[str, Any] | None:
        """A job dict, or None. Never raises, so a server outage never stops the runner."""
        try:
            resp = self._get(_endpoint(self.vendor_url(vendor), 'next-job/'), what=f'{vendor} poll')
        except ServerUnavailable as exc:
            self._outage(str(exc))
            return None
        except Exception as exc:
            log(f'{vendor} poll error: {exc}')
            return None
        self._reachable()
        if resp.status_code in (301, 302, 303, 307, 308):
            log(f'{vendor} poll redirect -> {resp.headers.get("Location", "")!r}. Use https:// in INGEST_BASE_URL.')
            return None
        if resp.status_code != 200:
            log(f'{vendor} poll failed: HTTP {resp.status_code} {_short_body(resp)}')
            return None
        try:
            data = resp.json()
        except ValueError:
            return None
        return data if isinstance(data, dict) and data.get('job_id') else None

    def download(self, file_url: str, dest: Path) -> None:
        url = urljoin(self.base, file_url)
        with self._get(url, what='Excel download', attempts=IMPORTANT_ATTEMPTS, stream=True) as resp:
            if resp.status_code != 200:
                raise RuntimeError(f'Excel download failed: HTTP {resp.status_code} {_short_body(resp)}')
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open('wb') as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    fh.write(chunk)

    def should_stop(self, vendor: str, job_id: str) -> bool:
        """True when the job was cancelled or deleted. Network errors keep the bot running."""
        try:
            resp = self._get(_endpoint(self.vendor_url(vendor), f'jobs/{job_id}/'), what='status check')
        except ServerUnavailable:
            return False  # the server is restarting; keep the bot going and ask again next time
        except Exception as exc:
            log(f'  status check error: {exc}')
            return False
        if resp.status_code == 404:
            log('  job was deleted on the server.')
            return True
        if resp.status_code != 200:
            log(f'  status check failed: HTTP {resp.status_code} {_short_body(resp)}')
            return False
        try:
            return bool(resp.json().get('cancelled'))
        except ValueError:
            return False

    def upload_results(
        self, vendor: str, job_id: str, done: Path | None, remaining: Path | None, exit_code: int | None,
        note: str, *, attempts: int = IMPORTANT_ATTEMPTS,
    ) -> bool:
        """True when the server took the files (or rejected them for good). Raises ServerUnavailable."""
        def files_factory() -> tuple[dict, list]:
            files, handles = {}, []
            for field, path in (('done_file', done), ('remaining_file', remaining)):
                if path is not None:
                    fh = path.open('rb')
                    handles.append(fh)
                    files[field] = (path.name, fh, XLSX_TYPE)
            return files, handles

        data = {'job_id': job_id, 'note': note}
        if exit_code is not None:
            data['exit_code'] = str(exit_code)
        resp = self._send(
            'POST', self.vendor_url(vendor), what='result upload', attempts=attempts,
            timeout=max(self.timeout, 120), data=data, files_factory=files_factory,
        )
        if resp.status_code != 200:
            log(f'  result upload failed: HTTP {resp.status_code} {_short_body(resp)}')
            return False
        try:
            stats = resp.json().get('stats')
        except ValueError:
            stats = None
        log(f'  results uploaded: {stats}')
        return True

    def complete(self, vendor: str, job_id: str, status: str, note: str, *, attempts: int = IMPORTANT_ATTEMPTS) -> None:
        """Mark the job finished on the server. Raises ServerUnavailable."""
        resp = self._send(
            'POST', _endpoint(self.vendor_url(vendor), f'jobs/{job_id}/complete/'), what='complete',
            attempts=attempts, json={'status': status, 'note': note[:2000]},
        )
        if resp.status_code == 200:
            log(f'  job {job_id} marked {status}.')
        else:
            log(f'  complete failed: HTTP {resp.status_code} {_short_body(resp)}')


# --------------------------------------------------------------------------- #
# Reporting results, with a local copy kept until the server has them          #
# --------------------------------------------------------------------------- #

def _keep_copy(path: Path | None, job_dir: Path) -> str:
    """Copy a result file into the job folder: a later run the same day reuses the bot's output name."""
    if path is None:
        return ''
    job_dir.mkdir(parents=True, exist_ok=True)
    target = job_dir / ('result-' + path.name)
    try:
        shutil.copy2(path, target)
        return str(target)
    except OSError as exc:
        log(f'  could not keep a copy of {path}: {exc}')
        return str(path)


def _save_pending(entry: dict[str, Any]) -> None:
    job_dir = JOBS_DIR / entry['job_id']
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / PENDING_NAME).write_text(json.dumps(entry, indent=2), encoding='utf-8')


def _deliver(server: Server, entry: dict[str, Any], *, attempts: int) -> None:
    """Upload (if not done yet) then complete. Raises ServerUnavailable; progress is kept in ``entry``."""
    if entry.get('upload') and not entry.get('uploaded'):
        done = Path(entry['done']) if entry.get('done') else None
        remaining = Path(entry['remaining']) if entry.get('remaining') else None
        server.upload_results(
            entry['vendor'], entry['job_id'], done, remaining, entry.get('exit_code'), entry['note'], attempts=attempts,
        )
        entry['uploaded'] = True
    server.complete(entry['vendor'], entry['job_id'], entry['status'], entry['note'], attempts=attempts)


def report(
    server: Server, vendor: str, job_id: str, status: str, note: str, *,
    done: Path | None = None, remaining: Path | None = None, exit_code: int | None = None,
    upload: bool = False, attempts: int = IMPORTANT_ATTEMPTS,
) -> None:
    """Send a job's outcome. If the server stays unavailable, keep it on disk to send later."""
    entry: dict[str, Any] = {
        'vendor': vendor, 'job_id': job_id, 'status': status, 'note': note,
        'exit_code': exit_code, 'upload': upload, 'uploaded': False,
        'done': str(done) if done else '', 'remaining': str(remaining) if remaining else '',
    }
    try:
        _deliver(server, entry, attempts=attempts)
    except ServerUnavailable as exc:
        job_dir = JOBS_DIR / job_id
        entry['done'] = _keep_copy(done, job_dir) if done else ''
        entry['remaining'] = _keep_copy(remaining, job_dir) if remaining else ''
        _save_pending(entry)
        log(f'  server still unavailable ({exc}). Results kept in {job_dir} and will be sent when it is back.')


def flush_pending(server: Server) -> None:
    """Send results that were kept while the server was down. One quiet try each; failures wait for the next loop."""
    if not JOBS_DIR.is_dir():
        return
    for pending in sorted(JOBS_DIR.glob(f'*/{PENDING_NAME}')):
        try:
            entry = json.loads(pending.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            log(f'Unreadable saved result {pending}: {exc}')
            continue
        try:
            _deliver(server, entry, attempts=1)
        except ServerUnavailable:
            _save_pending(entry)  # keeps "uploaded" so the files are not sent twice
            return  # server still down; try again on the next loop
        except Exception as exc:
            log(f'Could not send saved results for job {entry.get("job_id")}: {exc!r}')
            continue
        log(f'Sent saved results for job {entry["job_id"]}.')
        try:
            pending.unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# Bot process                                                                  #
# --------------------------------------------------------------------------- #

def _bot_python(bot_dir: Path, configured: str) -> str:
    if configured:
        return configured
    venv = bot_dir / '.venv' / 'Scripts' / 'python.exe'
    return str(venv) if venv.is_file() else sys.executable


def _debug_port(bot_dir: Path) -> int:
    """Chrome debug port from the bot's stores.json. Read only, never written."""
    try:
        cfg = json.loads((bot_dir / 'stores.json').read_text(encoding='utf-8'))
        return int(cfg.get('debug_port') or 9222)
    except Exception:
        return 9222


def stop_bot(proc: subprocess.Popen, port: int) -> None:
    """Stop this bot's Python run and the Chrome it drives. The runner itself stays up."""
    if proc.poll() is None:
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)], capture_output=True, text=True)
    # The bot's Chrome is the only chrome.exe started with this remote-debugging port.
    script = (
        "Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'chrome.exe' -or "
        "$_.Name -eq 'chromedriver.exe') -and $_.CommandLine -and "
        f"$_.CommandLine -like '*--remote-debugging-port={port}*' }} | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    subprocess.run(['powershell', '-NoProfile', '-Command', script], capture_output=True, text=True)
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        pass


def newest_output(folder: Path, prefix: str, since: float) -> Path | None:
    """The newest <prefix>(YYYY-MM-DD).xlsx (or its -HHMMSS fallback) written after the bot started."""
    pattern = re.compile(rf'^{re.escape(prefix)}\(\d{{4}}-\d{{2}}-\d{{2}}\)(-\d+)?\.xlsx$', re.I)
    found = [
        p for p in folder.glob('*.xlsx')
        if pattern.match(p.name) and p.stat().st_mtime >= since - 2
    ]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def _log_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _read_tail(path: Path, start: int = 0) -> list[str]:
    try:
        with path.open('rb') as fh:
            fh.seek(start)
            text = fh.read().decode('utf-8', errors='replace')
    except OSError:
        return []
    # Selenium dumps long "(No symbol)" stack frames; they never help on the page.
    return [line.rstrip() for line in text.splitlines() if line.strip() and '(No symbol)' not in line]


def bot_error_text(log_path: Path, log_start: int, err_path: Path, limit: int = 1400) -> str:
    """Why the bot failed, for the job note: its error output plus this run's error and FAIL log lines."""
    errors = _read_tail(err_path)[-12:]
    logged = _read_tail(log_path, log_start)
    picked = [line for line in logged if re.search(r'\[(ERROR|CRITICAL|WARNING)\]|\bFAIL\b|Traceback', line)]
    lines = errors + (picked or logged)[-12:]
    text = '\n'.join(lines).strip()
    return text[-limit:] if text else ''


def _safe_name(name: str) -> str:
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '_', Path(name or 'input.xlsx').name).strip(' .')
    return clean if clean.lower().endswith('.xlsx') else (clean or 'input') + '.xlsx'


def handle_job(job: dict[str, Any], server: Server, bot_dirs: dict[str, Path], cfg: dict[str, str]) -> None:
    vendor = job['vendor']
    job_id = job['job_id']
    job_type = job.get('job_type') or ''
    store = (job.get('store_name') or '').strip()
    log(f'Claimed {job_type} job {job_id} ({job.get("file_name")}){f" store {store}" if store else ""}')

    spec = JOB_TYPES.get(job_type)
    bot_dir = bot_dirs.get(vendor)
    if spec is None or bot_dir is None or not bot_dir.is_dir():
        report(server, vendor, job_id, 'failed', f'This laptop cannot run {job_type}: bot folder not set or missing.')
        return
    script_rel, out_rel, done_prefix, remain_prefix = spec
    script = bot_dir / script_rel
    if not script.is_file():
        report(server, vendor, job_id, 'failed', f'Bot script not found on {cfg.get("RUNNER_NAME") or "laptop"}: {script}')
        return

    excel = JOBS_DIR / job_id / _safe_name(job.get('file_name') or 'input.xlsx')
    try:
        server.download(job['file_url'], excel)
    except Exception as exc:
        report(server, vendor, job_id, 'failed', f'Could not download the Excel: {exc}')
        return

    # Arguments stay separate list items: paths contain spaces.
    args = [_bot_python(bot_dir, cfg.get('POLLER_PYTHON', '')), str(script), str(excel)]
    if store:
        args += ['--store', store]
    port = _debug_port(bot_dir)
    cancel_every = max(2, int(cfg.get('CANCEL_CHECK_EVERY') or DEFAULT_CANCEL_CHECK_EVERY))
    # Each bot logs to <script>.log. Remember where this run starts so only its lines are reported.
    bot_log = script.with_suffix('.log')
    log_start = _log_size(bot_log)
    # Crashes before logging starts (missing Chrome, missing package) only reach stderr, so keep it.
    err_path = excel.parent / 'bot_errors.txt'
    started = time.time()
    log(f'  running {script} ...')
    with err_path.open('w', encoding='utf-8') as err_file:
        proc = subprocess.Popen(
            args, cwd=str(script.parent), creationflags=subprocess.CREATE_NEW_CONSOLE, stderr=err_file,
        )

    try:
        next_check = time.time() + cancel_every
        while proc.poll() is None:
            time.sleep(1)
            if time.time() >= next_check:
                next_check = time.time() + cancel_every
                if server.should_stop(vendor, job_id):
                    log('  stopping the bot (cancelled or deleted on the server).')
                    stop_bot(proc, port)
                    return
    except KeyboardInterrupt:
        log('  runner stopped by user. Stopping the bot.')
        stop_bot(proc, port)
        report(server, vendor, job_id, 'failed', 'The laptop runner was stopped. Send the file again.', attempts=2)
        raise

    code = proc.returncode
    out_dir = bot_dir / out_rel
    done = newest_output(out_dir, done_prefix, started)
    remaining = newest_output(out_dir, remain_prefix, started)
    note = EXIT_NOTES.get(code, f'Bot exited with code {code}.')
    if done is None and remaining is None:
        note += ' The bot wrote no result files.'
    if code != 0:
        detail = bot_error_text(bot_log, log_start, err_path)
        if detail:
            note += '\nBot output:\n' + detail
    log(f'  bot exited with code {code}. done={done} remaining={remaining}')
    report(
        server, vendor, job_id, 'done' if (done or remaining) else 'failed', note,
        done=done, remaining=remaining, exit_code=code, upload=True,
    )


# --------------------------------------------------------------------------- #
# Main loop                                                                    #
# --------------------------------------------------------------------------- #

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run Reverb/Etsy Excel jobs from SellerPilot Hub')
    parser.add_argument('--once', action='store_true', help='Handle at most one job, then exit')
    parser.add_argument('--interval', type=int, default=0, help='Seconds between polls when idle')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cfg = load_env(ENV_FILE)
    base = cfg.get('INGEST_BASE_URL', '')
    token = cfg.get('INGEST_TOKEN', '')
    if not base or not token:
        log(f'Set INGEST_BASE_URL and INGEST_TOKEN in {ENV_FILE}')
        return 2
    bot_dirs = {
        vendor: Path(cfg[key])
        for vendor, key in (('reverb', 'REVERB_BOT_DIR'), ('etsy', 'ETSY_BOT_DIR'))
        if cfg.get(key)
    }
    vendors = [v for v in VENDORS if v in bot_dirs]
    if not vendors:
        log('Set REVERB_BOT_DIR and/or ETSY_BOT_DIR in ingest.env')
        return 2
    server = Server(
        base, token,
        timeout=int(cfg.get('INGEST_TIMEOUT') or DEFAULT_TIMEOUT),
        verify=(cfg.get('INGEST_VERIFY_SSL', '1') != '0'),
    )
    interval = args.interval or int(cfg.get('POLLER_INTERVAL') or DEFAULT_POLL_INTERVAL)
    log(f'Listing Runner started. Polling {", ".join(vendors)} every {interval}s at {server.base}')

    while True:
        # Results kept during an earlier outage go first.
        flush_pending(server)
        # One job at a time: both bots share Chrome debug port and profiles.
        job = None
        for vendor in vendors:
            job = server.poll_next_job(vendor)
            if job:
                job.setdefault('vendor', vendor)
                break
        if job:
            try:
                handle_job(job, server, bot_dirs, cfg)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                log(f'job crashed: {exc!r}')
                report(server, job['vendor'], job['job_id'], 'failed', f'Runner error: {exc!r}'[:500])
            if args.once:
                return 0
            continue
        if args.once:
            return 0
        time.sleep(interval)


def _run_forever() -> int:
    """Restart after an unexpected error instead of dying. Ctrl+C stops it."""
    while True:
        try:
            return main()
        except KeyboardInterrupt:
            log('interrupted - bye')
            return 130
        except SystemExit:
            raise
        except Exception as exc:
            log(f'FATAL: {exc!r} - restarting in 30s')
            try:
                time.sleep(30)
            except KeyboardInterrupt:
                return 130


if __name__ == '__main__':
    sys.exit(_run_forever())
