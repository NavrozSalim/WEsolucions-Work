"""Run one discovery job: fetch, drop duplicates, apply rules, write the file."""

from __future__ import annotations

import logging
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import requests
from django.core.files.base import ContentFile
from django.utils import timezone

from .columns import HOSTS, clean_columns, is_amazon, is_ebay, output_columns
from .files import (
    looks_like_category_url,
    product_url,
    read_spreadsheet,
    stamp_identity,
    workbook_bytes,
)
from .identity import dedupe_rows, extract_asin, extract_ebay_item_id, product_key
from .models import DiscoveryJob, DiscoveryProduct
from .parsers import (
    amazon_results_url,
    listing_follow_urls,
    next_page_url,
    parse_amazon_category,
    parse_amazon_product,
    parse_ebay_category,
    parse_ebay_product,
    with_page,
)
from .rules import normalize_rules, row_has_rule_fields, row_removed
from .sample_data import sample_by_key, sample_catalog

logger = logging.getLogger(__name__)

MAX_CATEGORY_URLS = 50
MAX_PRODUCT_URLS = 200
MAX_CATEGORY_PAGES = 20
MAX_CATEGORY_PRODUCTS = 300
FETCH_TIMEOUT = 25

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


def _ebay_session(marketplace: str):
    """Browser-style eBay session. A normal request is answered with 403."""
    session = _browser_session()
    host = HOSTS.get(marketplace) or HOSTS['ebay_us']
    if marketplace == 'ebay_au':
        session.headers['Accept-Language'] = 'en-AU,en;q=0.9,en-US;q=0.8'
    else:
        session.headers['Accept-Language'] = 'en-US,en;q=0.9'
    session.headers['Referer'] = host + '/'
    try:
        session.get(host + '/', timeout=FETCH_TIMEOUT)
    except Exception:
        logger.warning('Could not open eBay before the scrape')
    return session


def _blocked(html: str) -> bool:
    sample = (html or '')[:8000].lower()
    markers = ('captcha', 'robot check', 'enter the characters you see', 'sorry, we just need to make sure')
    return any(marker in sample for marker in markers)


def _project(row: dict, columns: list[str]) -> dict:
    return {column: row.get(column, '') for column in columns}


def _load_inputs(job: DiscoveryJob) -> list[dict]:
    with job.source_file.open('rb') as handle:
        return read_spreadsheet(handle)


def _sample_category_rows(job: DiscoveryJob, urls: list[str]) -> list[dict]:
    catalog = sample_catalog(job.marketplace)
    rows = []
    for _url in urls:
        rows.extend(dict(item) for item in catalog)
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

    def __init__(self, job: DiscoveryJob, rules: dict, input_count: int):
        self.job = job
        self.rules = rules
        self.input_count = input_count
        self.seen = set()
        self.duplicates = 0
        self.removed_by_rules = 0
        self.scraped = 0

    def add(self, batch: list[dict]) -> None:
        fresh = []
        for row in batch:
            self.scraped += 1
            key = product_key(self.job.marketplace, row)
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
        self.job.stats = {
            'input_rows': self.input_count,
            'scraped': self.scraped,
            'duplicates_removed': self.duplicates,
            'removed_by_rules': self.removed_by_rules,
            'kept': max(0, len(self.seen) - self.removed_by_rules),
            'errors': [],
            'warning': '',
        }
        self.job.save(update_fields=['stats'])


def _live_category_rows(
    url: str,
    marketplace: str,
    errors: list[str],
    on_batch=None,
    session: requests.Session | None = None,
    zip_code: str = '',
) -> list[dict]:
    """Walk the category grid page by page.

    An Amazon ``/b/?node=`` link is opened as the search grid for that node,
    then ``page=2``, ``page=3``, and so on, until a page adds no new products.
    """
    rows = []
    if is_amazon(marketplace):
        grid = amazon_results_url(url) or url
        seen_asins = set()
        for page in range(1, MAX_CATEGORY_PAGES + 1):
            if len(rows) >= MAX_CATEGORY_PRODUCTS:
                break
            current = with_page(grid, page)
            try:
                html = _fetch(current, session)
            except requests.RequestException as exc:
                errors.append(f'{current}: {exc}')
                break
            if _blocked(html):
                errors.append(f'{current}: the site blocked the request')
                break
            parsed = parse_amazon_category(html, current)
            fresh = [row for row in parsed if row.get('asin') not in seen_asins]
            if not fresh:
                if page == 1:
                    errors.append(f'{current}: no product cards found')
                break
            for row in fresh:
                seen_asins.add(row.get('asin'))
            rows.extend(fresh)
            if on_batch:
                on_batch(fresh)
        return rows[:MAX_CATEGORY_PRODUCTS]

    pages = [_with_ebay_zip(url, zip_code)]
    seen = set()
    discovered_follows = False
    while pages and len(seen) < MAX_CATEGORY_PAGES and len(rows) < MAX_CATEGORY_PRODUCTS:
        current = pages.pop(0)
        if not current or current in seen:
            continue
        seen.add(current)
        try:
            html = _fetch(current, session)
        except requests.RequestException as exc:
            if '403' in str(exc):
                errors.append(
                    f'{current}: eBay refused this server (HTTP 403). '
                    'The link is valid. eBay is blocking automated requests from this network.'
                )
            else:
                errors.append(f'{current}: {exc}')
            continue
        if _blocked(html):
            errors.append(f'{current}: the site blocked the request')
            continue
        parsed = parse_ebay_category(html, current)
        if not parsed and not rows:
            errors.append(f'{current}: no product cards found')
        rows.extend(parsed)
        if on_batch and parsed:
            on_batch(parsed)
        if not discovered_follows:
            discovered_follows = True
            for follow in listing_follow_urls(html, current):
                _queue_page(pages, seen, follow)
        _queue_page(pages, seen, next_page_url(html, current))
    return rows[:MAX_CATEGORY_PRODUCTS]


def _live_product_row(row: dict, marketplace: str, errors: list[str], session: requests.Session | None = None) -> dict | None:
    stamped = stamp_identity(marketplace, row)
    url = stamped.get('url') or ''
    if not url:
        errors.append('A row had no product URL or id.')
        return None
    try:
        html = _fetch(url, session)
    except requests.RequestException as exc:
        if is_ebay(marketplace) and '403' in str(exc):
            errors.append(
                f'{url}: eBay refused this server (HTTP 403). '
                'eBay is blocking automated requests from this network.'
            )
        else:
            errors.append(f'{url}: {exc}')
        return None
    if _blocked(html):
        errors.append(f'{url}: the site blocked the request')
        return None
    parsed = parse_amazon_product(html, url) if is_amazon(marketplace) else parse_ebay_product(html, url)
    if not parsed.get('title') and parsed.get('price') is None:
        errors.append(f'{url}: product details were not in the page')
        return None
    return parsed


def _category_urls(job: DiscoveryJob, inputs: list[dict]) -> list[str]:
    urls = []
    for row in inputs:
        url = str(row.get('url') or '').strip()
        if url:
            urls.append(url)
    if len(urls) > MAX_CATEGORY_URLS:
        raise DiscoveryError(f'Category files are limited to {MAX_CATEGORY_URLS} URLs.')
    if not urls:
        raise DiscoveryError('Add at least one category URL.')
    return urls


def execute_job(job_id) -> None:
    job = DiscoveryJob.objects.get(id=job_id)
    job.status = DiscoveryJob.Status.RUNNING
    job.started_at = timezone.now()
    job.error_message = ''
    job.products.all().delete()
    job.save(update_fields=['status', 'started_at', 'error_message'])

    errors: list[str] = []
    try:
        inputs = _load_inputs(job)
        rules = normalize_rules(job.rules)
        columns = output_columns(
            job.marketplace,
            clean_columns(job.marketplace, job.mode, job.columns),
        )
        job.columns = columns
        job.save(update_fields=['columns'])
        input_count = len(inputs)
        table = _LiveTable(job, rules, input_count)
        session = None
        if not job.use_sample and is_amazon(job.marketplace):
            session = _amazon_session(job.marketplace, (job.zip_code or '').strip())
        elif not job.use_sample and is_ebay(job.marketplace):
            session = _ebay_session(job.marketplace)

        if job.mode == DiscoveryJob.Mode.CATEGORY:
            urls = _category_urls(job, inputs)
            if job.use_sample:
                table.add(_sample_category_rows(job, urls))
            else:
                for url in urls:
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
                    )
        else:
            stamped = [stamp_identity(job.marketplace, row) for row in inputs]
            unique_inputs, pre_dupes = dedupe_rows(job.marketplace, stamped)
            table.duplicates += pre_dupes
            if len(unique_inputs) > MAX_PRODUCT_URLS:
                raise DiscoveryError(f'Product files are limited to {MAX_PRODUCT_URLS} products after duplicates are removed.')
            for row in unique_inputs:
                if row_has_rule_fields(row, rules) and row_removed(row, rules):
                    key = product_key(job.marketplace, row)
                    if key:
                        table.seen.add(key)
                    table.removed_by_rules += 1
                    table.scraped += 1
                    continue
                parsed = (
                    _sample_product_row(job, row)
                    if job.use_sample
                    else _live_product_row(row, job.marketplace, errors, session)
                )
                if parsed:
                    table.add([parsed])

        kept_rows = [
            item.data for item in job.products.order_by('created_at')
        ]
        if not job.use_sample and not kept_rows and errors and table.scraped == 0:
            raise DiscoveryError(errors[0])

        payload = workbook_bytes(columns, [_project(row, columns) for row in kept_rows])
        filename = f'{job.marketplace}-{job.mode}-{job.id}.xlsx'
        job.result_file.save(filename, ContentFile(payload), save=False)
        job.columns = columns
        job.rules = rules
        warning = ''
        if (
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
            'errors': errors[:20],
            'warning': warning,
        }
        job.status = DiscoveryJob.Status.SUCCEEDED
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
