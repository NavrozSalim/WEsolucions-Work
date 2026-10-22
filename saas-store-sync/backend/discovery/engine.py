"""Run one discovery job: fetch, drop duplicates, apply rules, write the file."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import requests
from django.core.files.base import ContentFile
from django.utils import timezone

from .columns import HOSTS, clean_columns, fill_image_columns, is_amazon, is_ebay, output_columns
from .files import (
    looks_like_category_url,
    open_bytes,
    product_url,
    read_spreadsheet,
    stamp_identity,
    workbook_bytes,
)
from .identity import dedupe_rows, extract_asin, extract_ebay_item_id, product_key
from .models import DiscoveryJob, DiscoveryProduct
from .parsers import (
    amazon_reported_total,
    amazon_results_url,
    amazon_with_price,
    listing_follow_urls,
    ebay_results_url,
    next_page_url,
    parse_amazon_category,
    parse_amazon_product,
    parse_ebay_category,
    parse_ebay_product,
    with_page,
)
from .locks import lock_owner_var, refresh_discovery_lock
from .rules import normalize_rules, row_has_rule_fields, row_removed
from .sample_data import sample_by_key, sample_catalog

logger = logging.getLogger(__name__)


class DiscoveryCancelled(Exception):
    """The user stopped this job."""


def _should_stop(job_id) -> bool:
    refresh_discovery_lock(job_id, lock_owner_var.get())
    row = DiscoveryJob.objects.filter(pk=job_id).values_list('cancel_requested', flat=True).first()
    return row is None or bool(row)


def _raise_if_stopped(job_id) -> None:
    if _should_stop(job_id):
        raise DiscoveryCancelled()

# Per search URL. Amazon repeats the same cards after a few hundred, so a
# department-sized category is split by price instead of stopping there.
MAX_CATEGORY_PAGES = 500
MAX_CATEGORY_PRODUCTS = 100_000
_AMAZON_PRICE_CAP_CENTS = 2_000_000
_AMAZON_MIN_BAND_CENTS = 100
FETCH_TIMEOUT = 25
# Amazon product pages share one delivery zip. A few at a time is faster
# while pages come back clean. A run of blocks drops back to one page at a time.
AMAZON_FETCH_WORKERS = 3

_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
)


class DiscoveryError(Exception):
    pass


def _with_ebay_zip(url: str, zip_code: str) -> str:
    if not zip_code or 'ebay.' not in (url or '').lower():
        return url
    parts = urlsplit(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    query['_stpos'] = [zip_code]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), ''))


def _browser_session():
    """Chrome-like client. eBay rejects a plain HTTP client with 403."""
    try:
        from curl_cffi import requests as curl_requests
        return curl_requests.Session(impersonate='chrome131')
    except Exception:
        session = requests.Session()
        session.headers['User-Agent'] = _UA
        return session


def _fetch(url: str, session: requests.Session | None = None) -> str:
    client = session or requests
    headers = {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Upgrade-Insecure-Requests': '1',
    }
    if session is None:
        headers['User-Agent'] = _UA
        headers['Accept-Language'] = 'en-US,en;q=0.9'
    try:
        response = client.get(url, timeout=FETCH_TIMEOUT, headers=headers, allow_redirects=True)
        response.raise_for_status()
    except requests.RequestException:
        raise
    except Exception as exc:
        raise requests.RequestException(str(exc)) from exc
    return response.text or ''


def _amazon_session(marketplace: str, zip_code: str):
    """Open Amazon and set the delivery zip so prices match that location."""
    host = HOSTS.get(marketplace) or HOSTS['amazon_us']
    session = _browser_session()
    session.headers['Accept-Language'] = 'en-US,en;q=0.9'
    if 'amazon.com.au' in host:
        session.cookies.set('i18n-prefs', 'AUD', domain='.amazon.com.au')
        session.cookies.set('lc-acbau', 'en_AU', domain='.amazon.com.au')
    else:
        session.cookies.set('i18n-prefs', 'USD', domain='.amazon.com')
        session.cookies.set('lc-main', 'en_US', domain='.amazon.com')
    home = host + '/'
    try:
        session.get(home, timeout=FETCH_TIMEOUT)
        if zip_code:
            session.post(
                host + '/gp/delivery/ajax/address-change.html',
                data={
                    'locationType': 'LOCATION_INPUT',
                    'zipCode': zip_code,
                    'storeContext': 'generic',
                    'deviceType': 'web',
                    'pageType': 'Search',
                    'actionSource': 'glow',
                },
                headers={
                    'x-requested-with': 'XMLHttpRequest',
                    'referer': home,
                },
                timeout=FETCH_TIMEOUT,
            )
    except Exception:
        logger.warning('Could not set Amazon delivery zip %s', zip_code)
    return session


_ebay_thread_sessions = threading.local()
_amazon_thread_sessions = threading.local()
_ebay_browser_lock = threading.Lock()


def _copy_session_cookies(source, target) -> None:
    if source is None or target is None or source is target:
        return
    try:
        jar = getattr(source.cookies, 'jar', None)
        if jar is not None:
            for cookie in list(jar):
                name = getattr(cookie, 'name', None)
                value = getattr(cookie, 'value', None)
                if not name or value is None:
                    continue
                domain = getattr(cookie, 'domain', '') or ''
                path = getattr(cookie, 'path', '/') or '/'
                if domain:
                    target.cookies.set(name, value, domain=domain, path=path)
                else:
                    target.cookies.set(name, value, path=path)
            return
    except Exception:
        pass
    try:
        target.cookies.update(source.cookies)
    except Exception:
        pass


def _thread_amazon_session(parent, marketplace: str):
    """One HTTP session per fetch thread, with the warmed delivery-zip cookies."""
    parent_id = id(parent) if parent is not None else None
    current = getattr(_amazon_thread_sessions, 'session', None)
    if current is not None and getattr(_amazon_thread_sessions, 'parent_id', None) == parent_id:
        return current
    current = _browser_session()
    current._marketplace = marketplace
    current._amazon_blocks = 0
    host = HOSTS.get(marketplace) or HOSTS['amazon_us']
    current.headers['Accept-Language'] = 'en-US,en;q=0.9'
    if 'amazon.com.au' in host:
        current.cookies.set('i18n-prefs', 'AUD', domain='.amazon.com.au')
        current.cookies.set('lc-acbau', 'en_AU', domain='.amazon.com.au')
    else:
        current.cookies.set('i18n-prefs', 'USD', domain='.amazon.com')
        current.cookies.set('lc-main', 'en_US', domain='.amazon.com')
    if parent is not None:
        try:
            current.headers.update(dict(parent.headers))
        except Exception:
            pass
        _copy_session_cookies(parent, current)
    _amazon_thread_sessions.session = current
    _amazon_thread_sessions.parent_id = parent_id
    return current


def _apply_ebay_cookies(session, marketplace: str, parent=None) -> None:
    host = HOSTS.get(marketplace) or HOSTS['ebay_us']
    session.headers['User-Agent'] = _UA
    session.headers['Accept-Language'] = (
        'en-AU,en;q=0.9,en-US;q=0.8'
        if marketplace == 'ebay_au'
        else 'en-US,en;q=0.9'
    )
    session.headers['Referer'] = host + '/'
    session.headers['Cache-Control'] = 'max-age=0'
    session.headers['Pragma'] = 'no-cache'
    session.headers['Upgrade-Insecure-Requests'] = '1'
    if marketplace == 'ebay_au':
        try:
            from scrapers.ebay_au_fast import load_cookies_for_http

            for cookie in load_cookies_for_http():
                name = cookie.get('name')
                value = cookie.get('value')
                if not name or value is None:
                    continue
                domain = cookie.get('domain') or '.ebay.com.au'
                path = cookie.get('path') or '/'
                session.cookies.set(name, value, domain=domain, path=path)
        except Exception:
            pass
    if parent is not None:
        try:
            session.headers.update(dict(parent.headers))
        except Exception:
            pass
        _copy_session_cookies(parent, session)


def _thread_ebay_session(parent):
    """One browser session per fetch thread, keeping the warmed eBay cookies."""
    marketplace = getattr(parent, '_marketplace', 'ebay_au')
    parent_id = id(parent) if parent is not None else None
    current = getattr(_ebay_thread_sessions, 'session', None)
    if current is not None and getattr(_ebay_thread_sessions, 'parent_id', None) == parent_id:
        if parent is not None:
            _copy_session_cookies(parent, current)
        return current
    current = _browser_session()
    current._parent_session = parent
    current._marketplace = marketplace
    _apply_ebay_cookies(current, marketplace, parent=parent)
    _ebay_thread_sessions.session = current
    _ebay_thread_sessions.parent_id = parent_id
    return current


def _ebay_session(marketplace: str):
    """Browser-style eBay session. A normal request is answered with 403."""
    session = _browser_session()
    session._marketplace = marketplace
    session._browser_state = {}
    session._browser_failures = 0
    session._browser_warmed_http = False
    session._browser_lock = threading.Lock()
    host = HOSTS.get(marketplace) or HOSTS['ebay_us']
    _apply_ebay_cookies(session, marketplace)
    try:
        session.get(host + '/', timeout=FETCH_TIMEOUT)
    except Exception:
        logger.warning('Could not open eBay before the scrape')
    return session


def _close_ebay_browser(session) -> None:
    if session is None:
        return
    root = getattr(session, '_parent_session', None) or session
    state = getattr(root, '_browser_state', None)
    if not state:
        return
    try:
        from scrapers.ebay_common import close_ebay_session

        close_ebay_session(state)
    except Exception:
        pass


def _ebay_html_usable(html: str, url: str) -> bool:
    """A real listing or search page. A challenge page is not one."""
    if not html or _blocked(html):
        return False
    parsed = parse_ebay_product(html, url)
    if parsed.get('title') or parsed.get('price') is not None:
        return True
    return bool(parse_ebay_category(html, url))


def _ebay_browser_html(url: str, marketplace: str, session) -> str | None:
    """Open the page in headless Chrome and keep the cookies for the next HTTP request."""
    if session is None:
        return None
    root = getattr(session, '_parent_session', None) or session
    lock = getattr(root, '_browser_lock', None) or _ebay_browser_lock
    with lock:
        if getattr(root, '_browser_warmed_http', False):
            _copy_session_cookies(root, session)
            try:
                html = _fetch(url, session)
                if _ebay_html_usable(html, url):
                    return html
            except Exception:
                pass
        if getattr(root, '_browser_failures', 0) >= 2:
            return None
        try:
            from scrapers.ebay_common import (
                EBAY_MARKET_AU,
                EBAY_MARKET_US,
                EbayBrowserSession,
                EbayHTTP,
                close_ebay_market_session,
            )
        except Exception:
            root._browser_failures = 2
            return None
        market = EBAY_MARKET_AU if marketplace == 'ebay_au' else EBAY_MARKET_US
        region = 'AU' if marketplace == 'ebay_au' else 'US'
        state = getattr(root, '_browser_state', None)
        if state is None:
            state = {}
            root._browser_state = state
        html, _err = EbayBrowserSession.warm_and_fetch(url, region, state, market)
        try:
            http_client = EbayHTTP._get_client(state, market)
            if http_client is not None:
                _copy_session_cookies(http_client, root)
                _copy_session_cookies(root, session)
        except Exception:
            pass
        if _ebay_html_usable(html, url):
            root._browser_failures = 0
            root._browser_warmed_http = True
            return html
        root._browser_failures = getattr(root, '_browser_failures', 0) + 1
        if root._browser_failures >= 2:
            try:
                close_ebay_market_session(state, market)
            except Exception:
                pass
        return None


def _ebay_browser_fallback(url: str, marketplace: str, session) -> dict | None:
    """Warm eBay in headless Chrome when HTTP is challenged, and hand cookies to HTTP."""
    html = _ebay_browser_html(url, marketplace, session)
    if not html:
        return None
    parsed = parse_ebay_product(html, url)
    if parsed.get('title') or parsed.get('price') is not None:
        return parsed
    return None


def _blocked(html: str) -> bool:
    text = html or ''
    sample = text[:8000].lower()
    markers = (
        'captcha',
        'robot check',
        'enter the characters you see',
        'sorry, we just need to make sure',
        'pardon our interruption',
        'checking your browser',
        'splashui/challenge',
        'to discuss automated access to amazon data',
        'validatecaptcha',
        'api-services-support@amazon.com',
    )
    if any(marker in sample for marker in markers):
        return True
    # Amazon's refusal page is short. A real product page is much larger.
    return len(text) < 20000 and 'continue shopping' in sample


def _project(row: dict, columns: list[str]) -> dict:
    return {column: row.get(column, '') for column in columns}


def _load_inputs(job: DiscoveryJob) -> list[dict]:
    try:
        blob = job.read_source()
    except FileNotFoundError:
        blob = b''
    if not blob:
        raise DiscoveryError('The uploaded file is missing from the shared database.')
    return read_spreadsheet(open_bytes(blob, job.original_filename))


def _stamp_category_url(batch: list[dict], source: str) -> list[dict]:
    """Remember the category link from the file on every product from that link."""
    for row in batch:
        row['category_url'] = source
    return batch


def _sample_category_rows(job: DiscoveryJob, urls: list[str]) -> list[dict]:
    catalog = sample_catalog(job.marketplace)
    rows = []
    for source in urls:
        for item in catalog:
            row = dict(item)
            row['category_url'] = source
            rows.append(row)
    return rows


def _sample_product_row(job: DiscoveryJob, row: dict) -> dict:
    stamped = stamp_identity(job.marketplace, row)
    keyed = sample_by_key(job.marketplace)
    if is_amazon(job.marketplace):
        found = keyed.get(stamped.get('asin') or '')
    else:
        found = keyed.get(stamped.get('item_id') or '')
    if found:
        return dict(found)
    # Unknown ids still return a row so a custom file can be exercised locally.
    fallback = dict(sample_catalog(job.marketplace)[0])
    if is_amazon(job.marketplace):
        asin = stamped.get('asin') or extract_asin(stamped.get('url') or '') or 'B0UNKNOWN1'
        fallback['asin'] = asin
        fallback['url'] = stamped.get('url') or fallback['url']
        fallback['title'] = f'Sample {asin}'
    else:
        item_id = stamped.get('item_id') or extract_ebay_item_id(stamped.get('url') or '') or '100000000099'
        fallback['item_id'] = item_id
        fallback['url'] = stamped.get('url') or fallback['url']
        fallback['title'] = f'Sample {item_id}'
    return fallback


def _queue_page(pages: list[str], seen: set[str], url: str) -> None:
    if url and url not in seen and url not in pages:
        pages.append(url)


def _json_row(row: dict) -> dict:
    safe = {}
    for key, value in row.items():
        if value is None or isinstance(value, (str, int, float, bool)):
            safe[key] = value
        else:
            safe[key] = str(value)
    return safe


class _LiveTable:
    """Persist kept products while the scrape is still running."""

    def __init__(self, job: DiscoveryJob, rules: dict, input_count: int, lock_owner: str | None = None):
        self.job = job
        self.rules = rules
        self.input_count = input_count
        self.lock_owner = lock_owner
        self.seen = set()
        self.resumed = set()
        self.duplicates = 0
        self.removed_by_rules = 0
        self.scraped = 0
        self.kept = 0
        self.warning = ''

    def publish(self) -> None:
        refresh_discovery_lock(self.job.id, self.lock_owner)
        self.job.stats = {
            'input_rows': self.input_count,
            'scraped': self.scraped,
            'duplicates_removed': self.duplicates,
            'removed_by_rules': self.removed_by_rules,
            'kept': self.kept,
            'errors': [],
            'warning': self.warning,
        }
        self.job.save(update_fields=['stats'])

    def add(self, batch: list[dict]) -> None:
        fresh = []
        for row in batch:
            key = product_key(self.job.marketplace, row)
            if key and key in self.resumed:
                continue
            self.scraped += 1
            if key and key in self.seen:
                self.duplicates += 1
                continue
            if key:
                self.seen.add(key)
            if row_removed(row, self.rules):
                self.removed_by_rules += 1
                continue
            if not key:
                key = f'row:{len(self.seen)}'
                self.seen.add(key)
            fresh.append(DiscoveryProduct(
                job=self.job,
                product_key=key[:80],
                data=_json_row(row),
            ))
        if fresh:
            DiscoveryProduct.objects.bulk_create(fresh, ignore_conflicts=True)
            self.kept += len(fresh)
        self.publish()


def _paginate_amazon(grid, session, errors, seen_asins, rows, on_batch, note_empty, stop=None):
    """Walk a search URL until a page adds no new ASINs.

    Follows Amazon's own Next link, then ``page=`` if that link is missing.
    A repeated page is Amazon's cap for this URL, not the end of the category.
    Returns the printed result count, whether the request was blocked, and
    whether that cap was hit.
    """
    reported = None
    blocked = False
    recycled = False
    previous = None
    current = with_page(grid, 1)
    seen_pages = set()
    for step in range(1, MAX_CATEGORY_PAGES + 1):
        if stop and stop():
            raise DiscoveryCancelled()
        if len(rows) >= MAX_CATEGORY_PRODUCTS:
            recycled = True
            break
        if not current or current in seen_pages:
            recycled = True
            break
        seen_pages.add(current)
        try:
            html = _fetch(current, session)
        except requests.RequestException as exc:
            errors.append(f'{current}: {exc}')
            blocked = True
            break
        if _blocked(html):
            errors.append(f'{current}: the site blocked the request')
            blocked = True
            break
        if reported is None:
            reported = amazon_reported_total(html)
        parsed = parse_amazon_category(html, current)
        page_asins = tuple(row.get('asin') for row in parsed)
        if previous is not None and page_asins and page_asins == previous:
            recycled = True
            break
        previous = page_asins
        fresh = [row for row in parsed if row.get('asin') not in seen_asins]
        if not fresh:
            if parsed and len(parsed) >= 8:
                recycled = True
            elif reported is not None and reported > len(seen_asins) + 24:
                recycled = True
            elif step == 1 and note_empty and not rows:
                errors.append(f'{current}: no product cards found')
            break
        for row in fresh:
            seen_asins.add(row.get('asin'))
        rows.extend(fresh)
        if on_batch:
            on_batch(fresh)
        if len(rows) >= MAX_CATEGORY_PRODUCTS:
            recycled = True
            break
        nxt = next_page_url(html, current)
        if not nxt or nxt in seen_pages:
            nxt = with_page(grid, step + 1)
        current = nxt
    else:
        recycled = True
    return reported, blocked, recycled


def _amazon_band_can_split(low: int | None, high: int | None, depth: int) -> bool:
    if depth >= 24:
        return False
    if low is None or high is None:
        return True
    return high - low > _AMAZON_MIN_BAND_CENTS


def _collect_amazon(grid, low, high, depth, session, errors, seen_asins, rows, on_batch, stop=None):
    if stop and stop():
        raise DiscoveryCancelled()
    if len(rows) >= MAX_CATEGORY_PRODUCTS:
        return
    target = grid if low is None else amazon_with_price(grid, low, high)
    before = len(seen_asins)
    can_split = _amazon_band_can_split(low, high, depth)
    reported, blocked, recycled = _paginate_amazon(
        target,
        session,
        errors,
        seen_asins,
        rows,
        on_batch,
        note_empty=low is None and not rows and before == 0,
        stop=stop,
    )
    if blocked:
        return
    gained = len(seen_asins) - before
    # A repeated page means this URL is capped (often a few hundred ASINs).
    # The printed total means the same thing when Amazon states it.
    short = reported is not None and reported > gained + 24
    if (not recycled and not short) or not can_split:
        return
    if gained == 0 and low is not None:
        return
    if low is None or high is None:
        low, high = 0, _AMAZON_PRICE_CAP_CENTS
    mid = low + (high - low) // 2
    if mid <= low or mid >= high:
        return
    _collect_amazon(grid, low, mid, depth + 1, session, errors, seen_asins, rows, on_batch, stop)
    _collect_amazon(grid, mid + 1, high, depth + 1, session, errors, seen_asins, rows, on_batch, stop)


def _live_category_rows(
    url: str,
    marketplace: str,
    errors: list[str],
    on_batch=None,
    session: requests.Session | None = None,
    zip_code: str = '',
    stop=None,
) -> list[dict]:
    """Walk every results page of a category link.

    An Amazon ``/b/?node=`` link is opened as the search grid. Pages follow the
    Next link until a page adds no new products. When that page is only a repeat,
    or Amazon's own total is still higher, the same grid is fetched again in
    price bands.
    """
    rows = []
    source = url

    def emit(batch, source=source):
        _stamp_category_url(batch, source)
        if on_batch:
            on_batch(batch)

    if is_amazon(marketplace):
        grid = amazon_results_url(url) or url
        _collect_amazon(grid, None, None, 0, session, errors, set(), rows, emit, stop)
        return rows[:MAX_CATEGORY_PRODUCTS]

    pages = [ebay_results_url(_with_ebay_zip(url, zip_code))]
    seen_urls = set()
    seen_ids = set()
    discovered_follows = False
    while pages and len(seen_urls) < MAX_CATEGORY_PAGES and len(rows) < MAX_CATEGORY_PRODUCTS:
        if stop and stop():
            raise DiscoveryCancelled()
        current = pages.pop(0)
        if not current or current in seen_urls:
            continue
        seen_urls.add(current)
        html = ''
        fetch_error = None
        try:
            html = _fetch(current, session)
        except requests.RequestException as exc:
            fetch_error = exc
        if fetch_error is not None or _blocked(html):
            recovered = _retry_ebay_html(current, marketplace, session)
            if recovered:
                html = recovered
                fetch_error = None
                logger.info('discovery ebay category recovered %s', current)
        if fetch_error is not None:
            if '403' in str(fetch_error):
                errors.append(
                    f'{current}: eBay refused this server (HTTP 403). '
                    'The link is valid. eBay is blocking automated requests from this network.'
                )
            else:
                errors.append(f'{current}: {fetch_error}')
            continue
        if _blocked(html):
            errors.append(f'{current}: the site blocked the request')
            continue
        parsed = parse_ebay_category(html, current)
        fresh = []
        for row in parsed:
            key = str(row.get('item_id') or row.get('url') or '')
            if not key or key in seen_ids:
                continue
            seen_ids.add(key)
            fresh.append(row)
        if not fresh:
            if not rows:
                errors.append(f'{current}: no product cards found')
            continue
        rows.extend(fresh)
        emit(fresh)
        if not discovered_follows:
            discovered_follows = True
            for follow in listing_follow_urls(html, current):
                _queue_page(pages, seen_urls, follow)
        _queue_page(pages, seen_urls, ebay_results_url(next_page_url(html, current)))
    return rows[:MAX_CATEGORY_PRODUCTS]


def _retry_ebay_html(url: str, marketplace: str, session) -> str | None:
    """One more HTTP try on the warmed session, then headless Chrome."""
    if session is None:
        return None
    host = HOSTS.get(marketplace) or HOSTS['ebay_us']
    parent = getattr(session, '_parent_session', None)
    time.sleep(0.35)
    try:
        session.get(host + '/', timeout=FETCH_TIMEOUT)
    except Exception:
        pass
    if parent is not None:
        _copy_session_cookies(parent, session)
    try:
        html = _fetch(url, session)
        if _ebay_html_usable(html, url):
            if parent is not None:
                _copy_session_cookies(session, parent)
            return html
    except Exception:
        pass
    return _ebay_browser_html(url, marketplace, session)


def _retry_amazon_product(url: str, marketplace: str, session) -> dict | None:
    """Open the product once more after Amazon sends a refusal or an empty page."""
    if session is None:
        return None
    streak = getattr(session, '_amazon_blocks', 0) + 1
    session._amazon_blocks = streak
    time.sleep(2.5 if streak >= 3 else 0.6)
    host = HOSTS.get(marketplace) or HOSTS['amazon_us']
    try:
        session.get(host + '/', timeout=FETCH_TIMEOUT)
    except Exception:
        pass
    try:
        html = _fetch(url, session)
    except requests.RequestException:
        return None
    if _blocked(html):
        return None
    parsed = parse_amazon_product(html, url)
    if parsed.get('title') or parsed.get('price') is not None:
        session._amazon_blocks = 0
        return parsed
    return None


def _retry_ebay_product(url: str, marketplace: str, session) -> dict | None:
    html = _retry_ebay_html(url, marketplace, session)
    if not html:
        return None
    parsed = parse_ebay_product(html, url)
    if parsed.get('title') or parsed.get('price') is not None:
        return parsed
    return None


def _live_product_row(row: dict, marketplace: str, errors: list[str], session: requests.Session | None = None) -> dict | None:
    stamped = stamp_identity(marketplace, row)
    url = stamped.get('url') or ''
    if not url:
        errors.append('A row had no product URL or id.')
        return None
    try:
        html = _fetch(url, session)
    except requests.RequestException as exc:
        if is_ebay(marketplace):
            recovered = _retry_ebay_product(url, marketplace, session)
            if recovered is not None:
                return recovered
            if '403' in str(exc):
                errors.append(
                    f'{url}: eBay refused this server (HTTP 403). '
                    'eBay is blocking automated requests from this network.'
                )
            else:
                errors.append(f'{url}: {exc}')
        else:
            recovered = _retry_amazon_product(url, marketplace, session)
            if recovered is not None:
                return recovered
            errors.append(f'{url}: {exc}')
        return None
    if _blocked(html):
        if is_ebay(marketplace):
            recovered = _retry_ebay_product(url, marketplace, session)
            if recovered is not None:
                return recovered
        if is_amazon(marketplace):
            recovered = _retry_amazon_product(url, marketplace, session)
            if recovered is not None:
                return recovered
        errors.append(f'{url}: the site blocked the request')
        return None
    parsed = parse_amazon_product(html, url) if is_amazon(marketplace) else parse_ebay_product(html, url)
    if not parsed.get('title') and parsed.get('price') is None:
        if is_ebay(marketplace):
            recovered = _retry_ebay_product(url, marketplace, session)
            if recovered is not None:
                return recovered
        if is_amazon(marketplace):
            recovered = _retry_amazon_product(url, marketplace, session)
            if recovered is not None:
                return recovered
        errors.append(f'{url}: product details were not in the page')
        return None
    return parsed


def _category_urls(job: DiscoveryJob, inputs: list[dict]) -> list[str]:
    urls = []
    for row in inputs:
        url = str(row.get('url') or '').strip()
        if url:
            urls.append(url)
    if not urls:
        raise DiscoveryError('Add at least one category URL.')
    return urls


def _discovery_ebay_html_ok(html: str | None, url: str) -> bool:
    if not html or _blocked(html):
        return False
    parsed = parse_ebay_product(html, url)
    return bool(parsed.get('title') or parsed.get('price') is not None)


def _ebay_catalog_http(url: str, region: str, state: dict, market: str) -> tuple[str | None, str]:
    from scrapers.ebay_common import EbayHTTP, _ebay_http_first_enabled

    if not _ebay_http_first_enabled(region):
        return None, 'http_skipped'
    try:
        html, _status, err = EbayHTTP.fetch(url, region, state, market)
    except Exception as exc:
        return None, f'http_error: {exc}'
    return html, err or ''


def _load_ebay_product_html(url: str, marketplace: str, state: dict) -> tuple[str | None, str]:
    """Load one eBay product the way the catalog scraper does.

    One warmed session, a short pause, HTTP first, then Chrome for this product
    only. A failed Chrome open does not turn the browser off for later rows.
    eBay AU goes through the catalog proxy pool when that pool is configured.
    """
    from scrapers.core import backoff_delay, random_delay
    from scrapers.ebay_common import (
        EBAY_MARKET_AU,
        EBAY_MARKET_US,
        RETRY_LIMIT,
        EbayBrowserSession,
        EbayDriver,
    )

    market = EBAY_MARKET_AU if marketplace == 'ebay_au' else EBAY_MARKET_US
    region = 'AU' if marketplace == 'ebay_au' else 'US'
    if marketplace == 'ebay_au':
        random_delay(0.15, 0.4)
        try:
            from scrapers.ebay_au_scraper import _ensure_cookies_in_http_client

            _ensure_cookies_in_http_client(state)
        except Exception:
            logger.warning('discovery ebay cookies were not loaded')
    else:
        random_delay(0.2, 0.6)

    au_http_only = False
    if marketplace == 'ebay_au':
        try:
            import os

            from scrapers.ebay_au_proxies import proxies_configured

            force_browser = (os.environ.get('EBAY_AU_FULL_ENGINE_SELENIUM') or '').strip().lower() in (
                '1', 'true', 'yes', 'on',
            )
            au_http_only = bool(proxies_configured()) and not force_browser
        except Exception:
            au_http_only = False

    last_err = 'unknown_error'
    for attempt in range(RETRY_LIMIT):
        if attempt > 0:
            backoff_delay(attempt, base=2.0, jitter=1.5)
            if marketplace == 'ebay_au':
                try:
                    from scrapers.ebay_au_proxies import proxies_configured

                    if proxies_configured():
                        EbayDriver.invalidate_au_proxy(state)
                except Exception:
                    logger.warning('discovery ebay au proxy did not rotate')
        html, err = _ebay_catalog_http(url, region, state, market)
        if _discovery_ebay_html_ok(html, url):
            return html, ''
        last_err = err or last_err
        if last_err in ('http_404', 'not_found'):
            return None, last_err
        if au_http_only:
            html2, err2 = _ebay_catalog_http(url, region, state, market)
            if _discovery_ebay_html_ok(html2, url):
                return html2, ''
            last_err = err2 or last_err
            continue
        try:
            browser_html, browser_err = EbayBrowserSession.warm_and_fetch(
                url,
                region,
                state,
                market,
                force_proxy_rotate=attempt > 0,
            )
        except Exception as exc:
            browser_html, browser_err = None, str(exc)
        if _discovery_ebay_html_ok(browser_html, url):
            return browser_html, ''
        # The browser visit updates cookies on the HTTP client in ``state``.
        html2, err2 = _ebay_catalog_http(url, region, state, market)
        if _discovery_ebay_html_ok(html2, url):
            return html2, ''
        last_err = err2 or browser_err or last_err
    return None, last_err


def _ebay_miss_message(url: str, err: str) -> str:
    if err in ('http_404', 'not_found', 'not_product_like', 'no_details'):
        return f'{url}: product details were not in the page'
    if err and '403' in err:
        return (
            f'{url}: eBay refused this server (HTTP 403). '
            'eBay is blocking automated requests from this network.'
        )
    return f'{url}: the site blocked the request'


_amazon_catalog_states = threading.local()


def _close_amazon_catalog_http(state: dict | None) -> None:
    if not state:
        return
    for key in ('amazon_http_session', 'amazon_au_http_session'):
        sess = state.pop(key, None)
        if sess is None:
            continue
        try:
            sess.close()
        except Exception:
            pass


def _amazon_catalog_http_state(marketplace: str, zip_code: str) -> dict:
    token = (marketplace, zip_code or '')
    current = getattr(_amazon_catalog_states, 'state', None)
    if current is not None and current.get('_token') == token:
        return current
    _close_amazon_catalog_http(current)
    current = {'_token': token}
    _amazon_catalog_states.state = current
    return current


def _amazon_requests_session(marketplace: str, state: dict, zip_code: str):
    """Catalog HTTP session. The delivery zip is set once and then reused."""
    is_au = marketplace == 'amazon_au'
    if is_au:
        from scrapers.amazon_au_scraper import AmazonAUHTTP

        session = AmazonAUHTTP._get_session(state, True)
        host = 'https://www.amazon.com.au'
    else:
        from scrapers.amazon_us_scraper import AmazonHTTP

        session = AmazonHTTP._get_session(state)
        host = 'https://www.amazon.com'
    if state.get('_discovery_zip_set'):
        return session
    code = (zip_code or '').strip()
    if not code:
        if is_au:
            try:
                from scrapers.amazon_au_scraper import amazon_au_postcode

                code = amazon_au_postcode()
            except Exception:
                code = '2762'
        else:
            code = '10001'
    try:
        session.get(host + '/', timeout=FETCH_TIMEOUT)
        session.post(
            host + '/gp/delivery/ajax/address-change.html',
            data={
                'locationType': 'LOCATION_INPUT',
                'zipCode': code,
                'storeContext': 'generic',
                'deviceType': 'web',
                'pageType': 'Detail',
                'actionSource': 'glow',
            },
            headers={
                'x-requested-with': 'XMLHttpRequest',
                'referer': host + '/',
            },
            timeout=FETCH_TIMEOUT,
        )
    except Exception:
        logger.warning('Could not set Amazon delivery zip %s', code)
    state['_discovery_zip_set'] = True
    return session


def _amazon_html_usable(html: str | None, url: str) -> bool:
    if not html or _blocked(html):
        return False
    parsed = parse_amazon_product(html, url)
    return bool(parsed.get('title') or parsed.get('price') is not None)


def _amazon_http_once(url: str, marketplace: str, state: dict, zip_code: str) -> tuple[str, str]:
    from scrapers.core import detect_block

    try:
        session = _amazon_requests_session(marketplace, state, zip_code)
        response = session.get(url, timeout=FETCH_TIMEOUT, allow_redirects=True)
    except requests.Timeout:
        return '', 'timeout'
    except requests.RequestException as exc:
        return '', f'request_error: {exc}'
    html = response.text or ''
    status = getattr(response, 'status_code', 0) or 0
    if status == 404:
        return html, 'http_404'
    if status != 200:
        return html, f'http_{status}'
    blocked, reason = detect_block(html)
    if blocked or _blocked(html):
        return html, f'blocked_{reason or "page"}'
    if _amazon_html_usable(html, url):
        return html, ''
    return html, 'no_details'


def _amazon_browser_html(url: str, marketplace: str, browser_state: dict) -> str | None:
    """One Chrome page after HTTP retries fail. The driver is reused for this job."""
    lock = browser_state.setdefault('_lock', threading.Lock())
    with lock:
        try:
            from scrapers.amazon_us_scraper import AmazonDriver, AmazonScraper
            from scrapers.core import random_delay
        except Exception:
            return None
        driver = browser_state.get('amazon_driver')
        if driver is None:
            try:
                driver = AmazonDriver.create()
            except Exception:
                logger.warning('discovery amazon browser did not start', exc_info=True)
                return None
            browser_state['amazon_driver'] = driver
            if marketplace == 'amazon_au':
                try:
                    from scrapers.amazon_au_scraper import _ensure_au_location

                    _ensure_au_location(driver, browser_state)
                except Exception:
                    logger.warning('discovery amazon au location was not set')
            elif not browser_state.get('amazon_us_location_set'):
                try:
                    AmazonScraper.set_zip_on_product_page(driver, url)
                    browser_state['amazon_us_location_set'] = True
                except Exception:
                    logger.warning('discovery amazon us zip was not set in the browser')
        try:
            driver.get(url)
            random_delay(2, 4)
            html = driver.page_source or ''
        except Exception:
            logger.info('discovery amazon browser missed %s', url)
            return None
        if _amazon_html_usable(html, url):
            return html
        return None


def _load_amazon_product_html(
    url: str,
    marketplace: str,
    http_state: dict,
    browser_state: dict,
    zip_code: str,
) -> tuple[str | None, str]:
    """HTTP with the catalog retry, then one Chrome open if the page is still blocked."""
    from scrapers.amazon_us_scraper import RETRY_LIMIT
    from scrapers.core import backoff_delay, get_random_headers

    last_err = 'unknown_error'
    is_au = marketplace == 'amazon_au'
    use_proxies = False
    if is_au:
        try:
            from scrapers.amazon_au_proxies import proxies_configured

            use_proxies = bool(proxies_configured())
        except Exception:
            use_proxies = False
    for attempt in range(RETRY_LIMIT):
        html, err = _amazon_http_once(url, marketplace, http_state, zip_code)
        if not err:
            if is_au:
                try:
                    from scrapers.amazon_au_proxies import mark_success
                    from scrapers.amazon_au_scraper import _SESSION_PROXY_KEY

                    mark_success(http_state.get(_SESSION_PROXY_KEY))
                except Exception:
                    pass
            return html, ''
        last_err = err or last_err
        if last_err in ('http_404', 'not_found'):
            return None, last_err
        if attempt + 1 >= RETRY_LIMIT:
            break
        ip_problem = (
            last_err.startswith(('blocked', 'http_5', 'http_403', 'http_429', 'request_error'))
            or last_err in ('timeout',)
        )
        if is_au and use_proxies and ip_problem:
            try:
                from scrapers.amazon_au_proxies import mark_blocked
                from scrapers.amazon_au_scraper import AmazonAUHTTP, _SESSION_PROXY_KEY

                mark_blocked(http_state.get(_SESSION_PROXY_KEY))
                AmazonAUHTTP._get_session(http_state, True, rotate=True)
                http_state.pop('_discovery_zip_set', None)
            except Exception:
                logger.warning('discovery amazon au proxy did not rotate')
            continue
        backoff_delay(attempt + 1, base=2.0, jitter=1.5)
        if last_err.startswith('blocked'):
            try:
                session = _amazon_requests_session(marketplace, http_state, zip_code)
                host = 'https://www.amazon.com.au/' if is_au else 'https://www.amazon.com/'
                session.headers.update(get_random_headers(host))
            except Exception:
                pass
    html = _amazon_browser_html(url, marketplace, browser_state)
    if html:
        return html, ''
    return None, last_err


def _catalog_amazon_product(row, marketplace, errors, zip_code='', browser_state=None):
    stamped = stamp_identity(marketplace, row)
    url = stamped.get('url') or ''
    if not url:
        errors.append('A row had no product URL or id.')
        return None
    http_state = _amazon_catalog_http_state(marketplace, zip_code)
    if browser_state is None:
        browser_state = {}
    html, err = _load_amazon_product_html(url, marketplace, http_state, browser_state, zip_code)
    if not html:
        if err in ('http_404', 'not_found', 'no_details'):
            errors.append(f'{url}: product details were not in the page')
        else:
            errors.append(f'{url}: the site blocked the request')
        return None
    parsed = parse_amazon_product(html, url)
    if not parsed.get('title') and parsed.get('price') is None:
        errors.append(f'{url}: product details were not in the page')
        return None
    return parsed


def _fetch_amazon_products(rows, marketplace, errors, session, table, stop, zip_code='') -> None:
    """Open a few Amazon product pages at once while they come back clean.

    Each thread keeps its own catalog HTTP session and delivery zip. Two blocks
    in a row drop the job back to one page at a time. ``session`` is the category
    curl session and is not used for these product pages.
    """
    del session
    browser_state = {'_lock': threading.Lock()}
    pending = iter(rows)
    streak = 0
    degraded = False

    def one(row):
        local_errors = []
        parsed = _catalog_amazon_product(
            row, marketplace, local_errors, zip_code, browser_state,
        )
        if parsed:
            fill_image_columns(parsed)
        return row, parsed, local_errors

    try:
        with ThreadPoolExecutor(max_workers=AMAZON_FETCH_WORKERS) as pool:
            inflight = set()

            def submit_one() -> bool:
                try:
                    row = next(pending)
                except StopIteration:
                    return False
                inflight.add(pool.submit(one, row))
                return True

            def fill() -> None:
                target = 1 if degraded else AMAZON_FETCH_WORKERS
                while len(inflight) < target:
                    if not submit_one():
                        break

            fill()
            while inflight:
                if stop():
                    for fut in inflight:
                        fut.cancel()
                    raise DiscoveryCancelled()
                done, inflight = wait(inflight, timeout=1, return_when=FIRST_COMPLETED)
                for fut in done:
                    try:
                        row, parsed, local_errors = fut.result()
                    except Exception as exc:
                        row, parsed, local_errors = {}, None, [str(exc)]
                    url = str((row or {}).get('url') or '')
                    if local_errors:
                        errors.extend(local_errors)
                        table.warning = local_errors[-1]
                        logger.info('discovery amazon miss %s (%s)', url, local_errors[-1])
                    if parsed:
                        streak = 0
                        degraded = False
                        logger.info('discovery amazon kept %s', url)
                        table.add([parsed])
                    elif local_errors:
                        streak += 1
                        if streak >= 2:
                            degraded = True
                        table.scraped += 1
                        table.publish()
                    fill()
    finally:
        try:
            from scrapers.amazon_us_scraper import AmazonDriver

            AmazonDriver.quit_safe(browser_state.get('amazon_driver'))
        except Exception:
            logger.warning('discovery amazon browser did not close')


def _fetch_ebay_products(rows, marketplace, errors, session, table, stop) -> None:
    """Open eBay product pages one at a time on the catalog session.

    ``session`` is unused. The catalog HTTP client and browser live in a
    state dict that stays warm for this file and is closed at the end.
    """
    del session
    from scrapers.ebay_common import EBAY_MARKET_AU, EBAY_MARKET_US, close_ebay_market_session

    market = EBAY_MARKET_AU if marketplace == 'ebay_au' else EBAY_MARKET_US
    state = {}
    try:
        for row in rows:
            if stop():
                raise DiscoveryCancelled()
            stamped = stamp_identity(marketplace, row)
            url = stamped.get('url') or ''
            if not url:
                message = 'A row had no product URL or id.'
                errors.append(message)
                table.warning = message
                table.scraped += 1
                table.publish()
                continue
            html, err = _load_ebay_product_html(url, marketplace, state)
            parsed = parse_ebay_product(html, url) if html else None
            if parsed and (parsed.get('title') or parsed.get('price') is not None):
                fill_image_columns(parsed)
                logger.info('discovery ebay kept %s', url)
                table.add([parsed])
                continue
            message = _ebay_miss_message(url, err)
            errors.append(message)
            table.warning = message
            logger.info('discovery ebay miss %s (%s)', url, message)
            table.scraped += 1
            table.publish()
    finally:
        try:
            close_ebay_market_session(state, market)
        except Exception:
            logger.warning('discovery ebay session did not close')


def _promote_discovery(job: DiscoveryJob) -> None:
    """Start the next queued file for this account and region."""
    try:
        from discovery.queue import promote_after_job

        promote_after_job(job.id, job.owner_id, job.marketplace)
    except Exception:
        logger.exception('discovery queue did not advance after %s', getattr(job, 'id', None))


def execute_job(job_id, lock_owner=None) -> None:
    """Run one discovery job.

    A broker redelivery passes the same job id. Saved products stay, and the
    scrape continues from the first id that is not saved yet.
    """
    token = lock_owner_var.set(lock_owner)
    try:
        _execute_job(job_id, lock_owner)
    finally:
        lock_owner_var.reset(token)


def _execute_job(job_id, lock_owner=None) -> None:
    try:
        job = DiscoveryJob.objects.get(id=job_id)
    except DiscoveryJob.DoesNotExist:
        return
    if job.status == DiscoveryJob.Status.SUCCEEDED:
        _promote_discovery(job)
        return
    if job.cancel_requested:
        job.status = DiscoveryJob.Status.CANCELLED
        job.error_message = 'Stopped.'
        job.finished_at = timezone.now()
        job.save(update_fields=['status', 'error_message', 'finished_at'])
        _promote_discovery(job)
        return
    previous = dict(job.stats or {})
    explicit_resume = bool(previous.pop('resume', False))
    # Products are written as each page is accepted. A redelivery must keep
    # them. Deleting here is what sent a long Amazon run back to zero.
    resume = explicit_resume or job.products.exists()
    job.status = DiscoveryJob.Status.RUNNING
    job.error_message = ''
    job.finished_at = None
    if resume:
        job.stats = previous
        if job.started_at is None:
            job.started_at = timezone.now()
    else:
        job.products.all().delete()
        job.started_at = timezone.now()
    job.save(update_fields=['status', 'started_at', 'error_message', 'finished_at', 'stats'])

    errors: list[str] = []
    columns = list(job.columns or [])
    session = None
    try:
        inputs = _load_inputs(job)
        rules = normalize_rules(job.rules)
        columns = output_columns(
            job.marketplace,
            clean_columns(job.marketplace, job.mode, job.columns),
            job.mode,
        )
        job.columns = columns
        job.save(update_fields=['columns'])
        input_count = len(inputs)
        table = _LiveTable(job, rules, input_count, lock_owner=lock_owner)
        if resume:
            saved_keys = {
                key for key in job.products.values_list('product_key', flat=True) if key
            }
            table.seen.update(saved_keys)
            table.resumed.update(saved_keys)
            table.kept = len(saved_keys)
            table.scraped = max(int(previous.get('scraped') or 0), len(saved_keys))
            table.duplicates = int(previous.get('duplicates_removed') or 0)
            table.removed_by_rules = int(previous.get('removed_by_rules') or 0)
        refresh_discovery_lock(job.id, lock_owner, force=True)
        stop = lambda: _should_stop(job.id)
        session = None
        # Product pages open their own catalog session. Category pages still
        # use the discovery curl session warmed here.
        if not job.use_sample and job.mode == DiscoveryJob.Mode.CATEGORY:
            if is_amazon(job.marketplace):
                session = _amazon_session(job.marketplace, (job.zip_code or '').strip())
            elif is_ebay(job.marketplace):
                session = _ebay_session(job.marketplace)

        if job.mode == DiscoveryJob.Mode.CATEGORY:
            urls = _category_urls(job, inputs)
            if job.use_sample:
                table.add(_sample_category_rows(job, urls))
            else:
                for url in urls:
                    _raise_if_stopped(job.id)
                    if not looks_like_category_url(url):
                        errors.append(f'{url}: this looks like a product URL, not a category URL')
                        continue
                    _live_category_rows(
                        url,
                        job.marketplace,
                        errors,
                        on_batch=table.add,
                        session=session,
                        zip_code=(job.zip_code or '').strip(),
                        stop=stop,
                    )
        else:
            stamped = [stamp_identity(job.marketplace, row) for row in inputs]
            unique_inputs, pre_dupes = dedupe_rows(job.marketplace, stamped)
            if not resume:
                table.duplicates += pre_dupes
            ebay_pending = []
            amazon_pending = []
            live_ebay = not job.use_sample and is_ebay(job.marketplace)
            live_amazon = not job.use_sample and is_amazon(job.marketplace)
            if live_ebay or live_amazon:
                _raise_if_stopped(job.id)
            for row in unique_inputs:
                if not live_ebay and not live_amazon:
                    _raise_if_stopped(job.id)
                done_key = product_key(job.marketplace, row)
                if done_key and done_key in table.resumed:
                    continue
                if row_has_rule_fields(row, rules) and row_removed(row, rules):
                    key = product_key(job.marketplace, row)
                    if key:
                        table.seen.add(key)
                    table.removed_by_rules += 1
                    table.scraped += 1
                    continue
                if live_ebay:
                    ebay_pending.append(row)
                    continue
                if live_amazon:
                    amazon_pending.append(row)
                    continue
                parsed = (
                    _sample_product_row(job, row)
                    if job.use_sample
                    else _live_product_row(row, job.marketplace, errors, session)
                )
                if parsed:
                    fill_image_columns(parsed)
                    table.add([parsed])
            if amazon_pending:
                _fetch_amazon_products(
                    amazon_pending,
                    job.marketplace,
                    errors,
                    session,
                    table,
                    stop,
                    zip_code=(job.zip_code or '').strip(),
                )
            if ebay_pending:
                _fetch_ebay_products(
                    ebay_pending,
                    job.marketplace,
                    errors,
                    session,
                    table,
                    stop,
                )

        kept_rows = [
            item.data for item in job.products.order_by('created_at')
        ]
        _raise_if_stopped(job.id)
        if not job.use_sample and not kept_rows and errors and table.scraped == 0:
            raise DiscoveryError(errors[0])

        payload = workbook_bytes(columns, [_project(row, columns) for row in kept_rows])
        filename = f'{job.marketplace}-{job.mode}-{job.id}.xlsx'
        job.result_bytes = payload
        try:
            job.result_file.save(filename, ContentFile(payload), save=False)
        except OSError:
            logger.warning('discovery job %s result stayed in the database', job_id)
        job.columns = columns
        job.rules = rules
        warning = table.warning
        missed = 0
        if job.mode != DiscoveryJob.Mode.CATEGORY:
            missed = max(
                0,
                input_count - table.duplicates - table.removed_by_rules - len(kept_rows),
            )
        if (
            not job.use_sample
            and job.mode != DiscoveryJob.Mode.CATEGORY
            and is_amazon(job.marketplace)
            and missed
        ):
            warning = (
                f'Amazon did not return {missed} of {input_count} products. '
                'Those pages were refused or had no title and price, so they were not saved.'
            )
        elif (
            not job.use_sample
            and job.mode == DiscoveryJob.Mode.CATEGORY
            and is_amazon(job.marketplace)
            and len(table.seen) < 12
        ):
            warning = (
                f'Amazon only sent {len(table.seen)} products to this server. '
                'The rest of that category is loaded in a normal browser and was not included.'
            )
        job.stats = {
            'input_rows': input_count,
            'scraped': table.scraped,
            'duplicates_removed': table.duplicates,
            'removed_by_rules': table.removed_by_rules,
            'kept': len(kept_rows),
            'missed': missed,
            'errors': errors[:20],
            'warning': warning,
        }
        job.status = DiscoveryJob.Status.SUCCEEDED
        job.finished_at = timezone.now()
        job.save()
    except DiscoveryCancelled:
        try:
            job.refresh_from_db()
        except DiscoveryJob.DoesNotExist:
            return
        kept_rows = [item.data for item in job.products.order_by('created_at')]
        try:
            payload = workbook_bytes(columns, [_project(row, columns) for row in kept_rows])
            job.result_bytes = payload
            try:
                job.result_file.save(
                    f'{job.marketplace}-{job.mode}-{job.id}.xlsx',
                    ContentFile(payload),
                    save=False,
                )
            except OSError:
                logger.warning('discovery job %s partial result stayed in the database', job_id)
        except Exception:
            logger.exception('discovery job %s could not save a partial result', job_id)
        stats = dict(job.stats or {})
        stats['errors'] = errors[:20]
        stats['kept'] = len(kept_rows)
        job.stats = stats
        job.status = DiscoveryJob.Status.CANCELLED
        job.error_message = 'Stopped.'
        job.finished_at = timezone.now()
        job.save()
    except Exception as exc:
        logger.exception('discovery job %s failed', job_id)
        job.status = DiscoveryJob.Status.FAILED
        job.error_message = str(exc)[:2000]
        job.finished_at = timezone.now()
        job.stats = {
            **(job.stats or {}),
            'errors': errors[:20],
        }
        job.save(update_fields=['status', 'error_message', 'finished_at', 'stats'])
    finally:
        _close_ebay_browser(session)
        _promote_discovery(job)
