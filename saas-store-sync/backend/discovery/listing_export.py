"""Turn a finished product-details scrape into a Reverb or Etsy listing workbook.

The column layout matches the two listing bots exactly:
- Reverb: Selimum Reverb Listing Automation/create_listing/Create.xlsx
- Etsy:   Selimum Etsy Listing Automation/data/sample_listings.xlsx

SKU and UPC stay empty for the user to fill. Images are copied to Bunny and
the CDN links go in the file. Products that fail a policy rule are listed on
the "Skipped" sheet with the reason.
"""

from __future__ import annotations

import io
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from . import bunny
from . import listing_rules as policy
from .columns import ID_FIELD, IMAGE_COLUMNS, is_amazon
from .models import DiscoveryJob, ListingExport

logger = logging.getLogger(__name__)

REVERB_COLUMNS = (
    'store_name', 'title', 'description', 'remove_branding', 'price', 'quantity',
    'Category', 'Subcategory', 'sku', 'photos', 'status', 'upc', 'free_shipping', 'amazon_url',
)
ETSY_COLUMNS = (
    'title', 'description', 'images', 'price', 'quantity', 'sku', 'allow_offers', 'category',
    'tags', 'shipping_profile', 'item_weight', 'package_length', 'package_width',
    'package_height', 'return_policy', 'who_made_it', 'what_is_it', 'when_made',
    'shop_section', 'featured', 'renewal', 'status',
)
ETSY_DEFAULTS = {
    'allow_offers': 'no',
    'shipping_profile': 'Standard Ground',
    'return_policy': '30 days',
    'who_made_it': 'Another company',
    'what_is_it': 'A supply or tool',
    'when_made': '2020–2026',
    'featured': 'no',
    'renewal': 'Automatic',
    'status': 'Pending',
}
REVERB_MAX_PHOTOS = 10
ETSY_MAX_IMAGES = 10
ETSY_TITLE_MAX = 140
UPLOAD_WORKERS = 12


class ExportRefused(Exception):
    """The job cannot be turned into listings (wrong marketplace, ZIP, mode…)."""


def refusal_for(job: DiscoveryJob, target: str) -> str:
    if target not in policy.TARGETS:
        return 'Choose Reverb or Etsy.'
    label = policy.TARGET_LABELS[target]
    if job.mode != DiscoveryJob.Mode.PRODUCT:
        return f'{label} listings are built from a Product details scrape. Run Product details first.'
    if job.marketplace not in policy.SUPPORTED_MARKETPLACES:
        return f'{label} listings use Amazon US or eBay US scrapes (New York ZIP).'
    if job.status in (DiscoveryJob.Status.QUEUED, DiscoveryJob.Status.RUNNING):
        return 'Wait until the scrape finishes.'
    allowed = policy.ALLOWED_ZIPS[target]
    if str(job.zip_code or '').strip() not in allowed:
        return (
            f'{label} policy needs the scrape to use ZIP {" or ".join(allowed)}. '
            f'This scrape used {job.zip_code or "no ZIP"}.'
        )
    return ''


# ---------------------------------------------------------------- per product

def _product_id(job: DiscoveryJob, row: dict) -> str:
    return str(row.get(ID_FIELD[job.marketplace]) or '').strip()


def _source_images(row: dict) -> list[str]:
    urls = []
    for column in IMAGE_COLUMNS:
        value = str(row.get(column) or '').strip()
        if value and value not in urls:
            urls.append(value)
    if not urls:
        for part in str(row.get('images') or row.get('image') or '').split('|'):
            value = part.strip()
            if value and value not in urls:
                urls.append(value)
    return urls


def check_row(job: DiscoveryJob, row: dict, target: str, scraped_on) -> tuple[dict | None, str]:
    """Apply the policy. Returns (prepared listing fields, '') or (None, reason)."""
    title = str(row.get('title') or '').strip()
    if not title:
        return None, 'No title'
    price = policy.to_number(row.get('price'))
    if price is None or price <= 0:
        return None, 'No price'
    amazon = is_amazon(job.marketplace)

    banned = policy.restricted_brand(row)
    if banned:
        return None, f'Restricted brand: {banned}'

    rating, reviews = row.get('rating'), row.get('review_count')
    if target == policy.REVERB:
        if amazon and not policy.reverb_rating_ok(rating, reviews):
            return None, f'Rating / reviews below policy ({rating or "-"}★, {reviews or 0} reviews)'
    else:
        if (amazon or policy.to_number(rating) is not None) and not policy.etsy_rating_ok(rating, reviews):
            return None, f'Rating / reviews below policy ({rating or "-"}★, {reviews or 0} reviews)'

    quantity = policy.quantity_for_stock(row.get('inventory'))
    if quantity is None:
        stock = row.get('inventory')
        return None, f'Stock under 7 ({stock})' if stock not in (None, '') else 'Stock unknown'

    if amazon:
        ok, reason = policy.free_delivery_within_week(row, scraped_on)
        if not ok:
            return None, reason

    images = _source_images(row)
    if not images:
        return None, 'No images'

    brand = policy.clean_brand(row.get('brand'))
    clean_title = policy.remove_brand(policy.strip_contact_info(title) or title, brand)
    bullets = policy.clean_points(policy.split_points(row.get('bullets')), brand)
    description = policy.remove_brand(policy.strip_contact_info(row.get('description')), brand)
    return {
        'id': _product_id(job, row),
        'url': str(row.get('url') or '').strip(),
        'title': clean_title,
        'brand': brand,
        'price': price,
        'quantity': quantity,
        'bullets': bullets,
        'description': description,
        'category': str(row.get('category') or '').strip(),
        'images': images,
    }, ''


def reverb_row(item: dict, photos: list[str], store_name: str) -> dict:
    lines = [item['title']] + item['bullets']
    if not item['bullets'] and item['description']:
        lines += [part for part in policy.split_points(item['description'].replace('. ', '.\n')) if part]
    category, subcategory = policy.reverb_category(item['category'], item['title'])
    return {
        'store_name': store_name,
        'title': item['title'],
        'description': '\n'.join(f'- {line}' for line in lines),
        'remove_branding': item['brand'],
        'price': f'{item["price"]:.2f}',
        'quantity': str(item['quantity']),
        'Category': category,
        'Subcategory': subcategory,
        'sku': '',
        'photos': '\n'.join(photos[:REVERB_MAX_PHOTOS]),
        'status': 'Draft',
        'upc': '',
        'free_shipping': 'True',
        'amazon_url': item['url'],
    }


def _etsy_title(title: str) -> str:
    if len(title) <= ETSY_TITLE_MAX:
        return title
    cut = title[:ETSY_TITLE_MAX].rsplit(' ', 1)[0]
    return cut.rstrip(' ,-|&')


def etsy_description(item: dict) -> str:
    """Intro paragraph, then every point under its own heading (no * bullets)."""
    blocks = []
    intro = item['description'] or item['title']
    if intro:
        blocks.append(intro)
    used: set[str] = set()
    for point in item['bullets']:
        heading, body = policy.heading_point(point, used)
        blocks.append(f'{heading}\n{body}')
    return '\n\n'.join(blocks)


def etsy_row(item: dict, photos: list[str]) -> dict:
    leaf = policy.leaf_category(item['category'])
    row = dict.fromkeys(ETSY_COLUMNS, '')
    row.update(ETSY_DEFAULTS)
    row.update({
        'title': _etsy_title(item['title']),
        'description': etsy_description(item),
        'images': '; '.join(photos[:ETSY_MAX_IMAGES]),
        'price': f'{item["price"]:.2f}',
        'quantity': str(item['quantity']),
        'sku': '',
        'category': leaf,
        'tags': ', '.join(policy.etsy_tags(item['title'], item['category'], item['bullets'])),
        'shop_section': leaf,
    })
    return row


# ---------------------------------------------------------------- workbook

_HEADER_FILL = PatternFill('solid', fgColor='1F2937')
_HEADER_FONT = Font(bold=True, color='FFFFFF')


def _sheet(ws, columns, rows, widths=None):
    ws.append(list(columns))
    for cell in ws[1]:
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
    for row in rows:
        ws.append([row.get(column, '') for column in columns])
    for index, column in enumerate(columns, start=1):
        letter = ws.cell(row=1, column=index).column_letter
        ws.column_dimensions[letter].width = (widths or {}).get(column, 18)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.number_format = '@'
            cell.alignment = Alignment(vertical='top', wrap_text=False)
    ws.freeze_panes = 'A2'


def build_workbook(target: str, listings: list[dict], skipped: list[dict]) -> bytes:
    wb = Workbook()
    ws = wb.active
    if target == policy.REVERB:
        ws.title = 'create'
        _sheet(ws, REVERB_COLUMNS, listings, {'title': 60, 'description': 60, 'photos': 60, 'amazon_url': 45})
    else:
        ws.title = 'Sheet1'
        _sheet(ws, ETSY_COLUMNS, listings, {'title': 60, 'description': 60, 'images': 60, 'tags': 60})
    _sheet(
        wb.create_sheet('Skipped'),
        ('product_id', 'url', 'title', 'reason'),
        skipped,
        {'url': 45, 'title': 60, 'reason': 45},
    )
    checklist = wb.create_sheet('Checklist')
    checklist.append(['Still to do by hand before publishing'])
    checklist['A1'].font = Font(bold=True)
    for step in policy.MANUAL_CHECKS[target]:
        checklist.append([step])
    checklist.column_dimensions['A'].width = 100
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# ---------------------------------------------------------------- run

def _save_progress(export: ListingExport, **stats) -> None:
    merged = dict(export.stats or {})
    merged.update(stats)
    export.stats = merged
    ListingExport.objects.filter(pk=export.pk).update(stats=merged)


def run_export(export_id: str) -> None:
    export = ListingExport.objects.select_related('job').filter(pk=export_id).first()
    if export is None:
        return
    export.status = ListingExport.Status.RUNNING
    export.error_message = ''
    export.save(update_fields=['status', 'error_message'])
    try:
        _build(export)
    except (ExportRefused, bunny.BunnyNotConfigured) as exc:
        _fail(export, str(exc))
    except Exception as exc:  # noqa: BLE001 - shown to the user, full trace in logs
        logger.exception('listing export %s failed', export_id)
        _fail(export, f'Could not build the file: {exc}')


def _fail(export: ListingExport, message: str) -> None:
    export.status = ListingExport.Status.FAILED
    export.error_message = message
    export.finished_at = timezone.now()
    export.save(update_fields=['status', 'error_message', 'finished_at'])


def _build(export: ListingExport) -> None:
    job = export.job
    target = export.target
    refused = refusal_for(job, target)
    if refused:
        raise ExportRefused(refused)
    cfg = bunny.config()
    scraped_on = policy.scraped_day(job.finished_at or job.started_at or job.created_at)

    accepted: list[dict] = []
    skipped: list[dict] = []
    stored = job.products.order_by('created_at').values_list('data', flat=True).iterator(chunk_size=500)
    for data in stored:
        row = data or {}
        item, reason = check_row(job, row, target, scraped_on)
        if item is None:
            skipped.append({
                'product_id': _product_id(job, row),
                'url': row.get('url') or '',
                'title': row.get('title') or '',
                'reason': reason,
            })
        else:
            accepted.append(item)

    total_images = sum(len(item['images'][:REVERB_MAX_PHOTOS]) for item in accepted)
    _save_progress(
        export,
        products=len(accepted) + len(skipped),
        passed_rules=len(accepted),
        skipped=len(skipped),
        images_total=total_images,
        images_done=0,
    )

    uploaded: dict[str, list[str | None]] = {
        item['id']: [None] * len(item['images'][:REVERB_MAX_PHOTOS]) for item in accepted
    }
    lock = threading.Lock()
    done = 0
    last_save = time.monotonic()
    local = threading.local()

    def session() -> requests.Session:
        if not hasattr(local, 'session'):
            local.session = requests.Session()
        return local.session

    def work(product_id: str, index: int, url: str):
        path = cfg.path_for(product_id, index + 1)
        return product_id, index, bunny.copy_image(session(), cfg, url, path)

    with ThreadPoolExecutor(max_workers=UPLOAD_WORKERS) as pool:
        futures = [
            pool.submit(work, item['id'], index, url)
            for item in accepted
            for index, url in enumerate(item['images'][:REVERB_MAX_PHOTOS])
        ]
        for future in as_completed(futures):
            product_id, index, cdn_url = future.result()
            with lock:
                uploaded[product_id][index] = cdn_url or None
                done += 1
                if time.monotonic() - last_save > 2:
                    last_save = time.monotonic()
                    _save_progress(export, images_done=done)

    listings = []
    for item in accepted:
        photos = [url for url in uploaded.get(item['id'], []) if url]
        if not photos:
            skipped.append({
                'product_id': item['id'],
                'url': item['url'],
                'title': item['title'],
                'reason': 'Images could not be copied to Bunny',
            })
            continue
        if target == policy.REVERB:
            listings.append(reverb_row(item, photos, export.store_name))
        else:
            listings.append(etsy_row(item, photos))

    export.result_bytes = build_workbook(target, listings, skipped)
    export.stats = {
        **(export.stats or {}),
        'images_done': done,
        'listings': len(listings),
        'skipped': len(skipped),
        'skip_reasons': _reason_counts(skipped),
    }
    export.status = ListingExport.Status.SUCCEEDED
    export.finished_at = timezone.now()
    export.save(update_fields=['result_bytes', 'stats', 'status', 'finished_at'])


def _reason_counts(skipped: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for row in skipped:
        reason = str(row.get('reason') or '')
        key = reason.split(':')[0].split('(')[0].strip() or 'Other'
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda pair: -pair[1]))
