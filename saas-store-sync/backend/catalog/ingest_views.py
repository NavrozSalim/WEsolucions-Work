"""Ingest API for off-server scrapers (e.g. desktop HEB runner).

Authentication: ``Authorization: Bearer <token>`` (see ``catalog.IngestToken``).

POST /api/v1/ingest/heb/   body = {"items": [ {url, price, stock, title, scraped_at?, error_code?, error_message?}, ... ]}

The endpoint mirrors what ``run_catalog_scrape`` does per product: creates a
``VendorPrice`` history row, applies per-store pricing / inventory rules, then
updates ``ProductMapping.store_price`` / ``store_stock`` / ``last_scrape_time``.
"""

from __future__ import annotations

import hashlib
import logging
import re
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog.models import HebScrapeJob, IngestToken, ProductMapping
from products.models import Product
from vendor.models import Vendor, VendorPrice


logger = logging.getLogger(__name__)


MAX_BATCH_ITEMS = 500


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _client_ip(request) -> str | None:
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip() or None
    return request.META.get('REMOTE_ADDR') or None


def _authenticate(request, required_scope: str) -> IngestToken:
    header = request.META.get('HTTP_AUTHORIZATION', '') or ''
    if not header.lower().startswith('bearer '):
        raise AuthenticationFailed('Missing Bearer token.')
    raw = header.split(' ', 1)[1].strip()
    if not raw:
        raise AuthenticationFailed('Empty bearer token.')

    token_hash = _hash_token(raw)
    try:
        tok = IngestToken.objects.get(token_hash=token_hash)
    except IngestToken.DoesNotExist:
        raise AuthenticationFailed('Invalid token.')

    if not tok.is_active:
        raise AuthenticationFailed('Token disabled.')

    scopes = tok.scopes or []
    if required_scope not in scopes:
        raise PermissionDenied(f'Token does not have scope "{required_scope}".')

    try:
        tok.last_used_at = timezone.now()
        tok.last_used_ip = _client_ip(request)
        tok.last_used_count = (tok.last_used_count or 0) + 1
        tok.save(update_fields=['last_used_at', 'last_used_ip', 'last_used_count'])
    except Exception:
        logger.debug('Failed to update IngestToken usage stats', exc_info=True)

    return tok


_PRICE_NUM_RE = re.compile(r'(\d+(?:\.\d+)?)')


def _coerce_price(value: Any) -> Decimal | None:
    """Tolerant price parser.

    Accepts numbers (``11.99``), clean strings (``"11.99"``) and dirty
    strings produced by the desktop scraper such as ``"$11.99 each"``,
    ``"$4 generics"`` or ``"$2.50 / each"``. Returns ``None`` when no
    numeric portion is present.
    """
    if value is None or value == '':
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            d = Decimal(str(value))
        except Exception:
            return None
    else:
        s = str(value).replace(',', '').strip()
        if not s or s.upper() in ('N/A', 'NONE', 'NULL', 'NAN'):
            return None
        try:
            d = Decimal(s)
        except Exception:
            m = _PRICE_NUM_RE.search(s)
            if not m:
                return None
            try:
                d = Decimal(m.group(1))
            except Exception:
                return None
    if d < 0:
        return None
    return d


def _coerce_stock(value: Any) -> int | None:
    if value in (None, ''):
        return None
    try:
        s = int(float(value))
    except Exception:
        return None
    if s < 0:
        return 0
    return s


def _apply_to_mappings(product: Product, vendor_price: Decimal, vendor_stock: int, title: str | None):
    """Mirror per-row logic from ``catalog.tasks.run_catalog_scrape`` for a single product."""
    from sync.tasks import (
        _apply_pricing,
        _apply_inventory,
        _get_pricing_for_vendor,
        _get_inventory_for_vendor,
        _is_walmart_store,
    )

    mappings = list(
        ProductMapping.objects.select_related('store', 'store__marketplace')
        .filter(product=product, is_active=True)
    )
    if not mappings:
        return 0

    now = timezone.now()
    applied = 0
    for pm in mappings:
        store = pm.store
        try:
            pricing = _get_pricing_for_vendor(store, product.vendor_id)
            inventory = _get_inventory_for_vendor(store, product.vendor_id)
            new_price = (
                _apply_pricing(
                    vendor_price,
                    pricing,
                    is_walmart=_is_walmart_store(store),
                    pack_qty=getattr(pm, 'pack_qty', None),
                    prep_fees=getattr(pm, 'prep_fees', None),
                    shipping_fees=getattr(pm, 'shipping_fees', None),
                )
                if vendor_price is not None else None
            )
            if new_price is None and vendor_price is not None:
                new_price = Decimal(str(vendor_price))
            new_stock = _apply_inventory(vendor_stock, inventory)

            pm.store_price = new_price
            pm.store_stock = new_stock
            pm.sync_status = 'scraped'
            pm.failed_sync_count = 0
            pm.last_scrape_time = now
            save_fields = [
                'store_price', 'store_stock', 'sync_status',
                'failed_sync_count', 'last_scrape_time',
            ]
            if title:
                pm.title = title[:500]
                save_fields.append('title')
            pm.save(update_fields=save_fields)
            applied += 1
        except Exception:
            logger.exception(
                'Ingest apply failed for product %s store %s',
                product.vendor_sku, store.id,
            )
            pm.failed_sync_count = (pm.failed_sync_count or 0) + 1
            pm.sync_status = 'needs_attention' if pm.failed_sync_count >= 3 else 'failed'
            pm.save(update_fields=['failed_sync_count', 'sync_status'])
    return applied


class HebIngestView(APIView):
    """Accept a batch of HEB scrape results from an external runner."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, *args, **kwargs):
        _authenticate(request, required_scope='heb')

        payload = request.data if isinstance(request.data, dict) else {}
        items = payload.get('items')
        if not isinstance(items, list) or not items:
            return Response(
                {'error': 'Body must be {"items": [...]} with at least one row.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(items) > MAX_BATCH_ITEMS:
            return Response(
                {'error': f'Batch too large (max {MAX_BATCH_ITEMS}).'},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )

        heb_vendor_ids = list(
            Vendor.objects.filter(Q(code='heb') | Q(code__istartswith='heb_'))
            .values_list('id', flat=True)
        )
        if not heb_vendor_ids:
            return Response(
                {'error': 'No HEB vendor seeded in this environment.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        results = []
        stats = {'received': len(items), 'matched': 0, 'applied': 0, 'skipped': 0, 'errors': 0}

        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                results.append({'index': idx, 'status': 'error', 'reason': 'not an object'})
                stats['errors'] += 1
                continue

            url = (item.get('url') or '').strip()
            if not url:
                results.append({'index': idx, 'status': 'error', 'reason': 'missing url'})
                stats['errors'] += 1
                continue
            if 'heb.com' not in url.lower():
                results.append({'index': idx, 'status': 'error', 'reason': 'not an HEB url'})
                stats['errors'] += 1
                continue

            price = _coerce_price(item.get('price'))
            stock = _coerce_stock(item.get('stock'))
            if stock is None:
                stock = _coerce_stock(item.get('inventory'))
            title = (item.get('title') or '').strip() or None
            error_code = (item.get('error_code') or '').strip() or None

            if price is None and error_code is None:
                results.append({'index': idx, 'status': 'skipped', 'url': url, 'reason': 'no price and no error_code'})
                stats['skipped'] += 1
                continue

            products = list(
                Product.objects.filter(vendor_id__in=heb_vendor_ids, vendor_url=url)
            )
            if not products:
                products = list(
                    Product.objects.filter(vendor_id__in=heb_vendor_ids, vendor_url__iexact=url)
                )
            if not products:
                results.append({'index': idx, 'status': 'unmatched', 'url': url})
                stats['skipped'] += 1
                continue

            stats['matched'] += 1

            try:
                applied_total = 0
                product_ids = []
                with transaction.atomic():
                    for product in products:
                        VendorPrice.objects.create(
                            product=product,
                            price=price if price is not None else None,
                            stock=stock if (stock is not None and stock >= 0) else None,
                            error_code=error_code[:50] if error_code else None,
                        )
                        if price is not None:
                            applied_total += _apply_to_mappings(
                                product,
                                price,
                                0 if stock is None else stock,
                                title,
                            )
                        product_ids.append(str(product.id))
                stats['applied'] += applied_total
                results.append({
                    'index': idx,
                    'status': 'ok',
                    'url': url,
                    'product_ids': product_ids,
                    'mappings_updated': applied_total,
                })
            except Exception as exc:
                logger.exception('HEB ingest failure for %s', url)
                stats['errors'] += 1
                results.append({
                    'index': idx,
                    'status': 'error',
                    'url': url,
                    'reason': str(exc)[:300],
                })

        return Response({'stats': stats, 'results': results}, status=status.HTTP_200_OK)


class HebIngestUrlsView(APIView):
    """Return the canonical list of HEB product URLs the catalog wants scraped.

    The desktop runner calls this once at the start of each pass to pull the
    fresh URL list from the SaaS app instead of relying on a stale local
    ``links.txt``. URLs are pulled from active ProductMappings whose product
    belongs to any HEB vendor (``code='heb'`` or ``code__istartswith='heb_'``).

    Auth: ``Authorization: Bearer <token>`` with the same ``heb`` scope as
    the POST endpoint.

    Query params:
        ?store_id=<uuid>  - optional, restrict to a single store
        ?limit=<int>      - optional cap (default no cap)

    Response:
        {
          "count": 879,
          "fetched_at": "2026-04-17T22:00:00+00:00",
          "urls": ["https://www.heb.com/...", ...]
        }
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, *args, **kwargs):
        _authenticate(request, required_scope='heb')

        heb_vendor_ids = list(
            Vendor.objects.filter(Q(code='heb') | Q(code__istartswith='heb_'))
            .values_list('id', flat=True)
        )
        if not heb_vendor_ids:
            return Response({'count': 0, 'urls': [], 'fetched_at': timezone.now().isoformat()})

        qs = (
            ProductMapping.objects
            .filter(is_active=True, product__vendor_id__in=heb_vendor_ids)
            .exclude(product__vendor_url__isnull=True)
            .exclude(product__vendor_url='')
        )

        store_id = (request.query_params.get('store_id') or '').strip()
        if store_id:
            qs = qs.filter(store_id=store_id)

        urls_iter = (
            qs.order_by('product__vendor_url')
            .values_list('product__vendor_url', flat=True)
            .distinct()
        )

        try:
            limit = int(request.query_params.get('limit') or 0)
        except (TypeError, ValueError):
            limit = 0
        if limit > 0:
            urls_iter = urls_iter[:limit]

        urls = [u for u in urls_iter if u]
        return Response({
            'count': len(urls),
            'fetched_at': timezone.now().isoformat(),
            'urls': urls,
        })


def _collect_heb_urls(store_id: str | None) -> list[str]:
    """Return distinct HEB vendor_url values across all (or one) store(s)."""
    heb_vendor_ids = list(
        Vendor.objects.filter(Q(code='heb') | Q(code__istartswith='heb_'))
        .values_list('id', flat=True)
    )
    if not heb_vendor_ids:
        return []
    qs = (
        ProductMapping.objects
        .filter(is_active=True, product__vendor_id__in=heb_vendor_ids)
        .exclude(product__vendor_url__isnull=True)
        .exclude(product__vendor_url='')
    )
    if store_id:
        qs = qs.filter(store_id=store_id)
    return list(
        qs.order_by('product__vendor_url')
        .values_list('product__vendor_url', flat=True)
        .distinct()
    )


class HebIngestNextJobView(APIView):
    """Long-running desktop runner calls this every N seconds.

    - If a ``HebScrapeJob`` is ``pending``, atomically claims it (flips to
      ``claimed``), embeds the current URL list so the runner doesn't need a
      second call, and returns it.
    - If nothing is pending, returns ``{"job_id": null}`` so the caller can
      sleep and poll again.

    Auth: Bearer token, scope ``heb``.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, *args, **kwargs):
        token = _authenticate(request, required_scope='heb')

        with transaction.atomic():
            job = (
                HebScrapeJob.objects
                .select_for_update(skip_locked=True)
                .filter(status=HebScrapeJob.Status.PENDING)
                .order_by('requested_at')
                .first()
            )
            if job is None:
                return Response({'job_id': None, 'checked_at': timezone.now().isoformat()})

            urls = _collect_heb_urls(str(job.store_id) if job.store_id else None)
            job.status = HebScrapeJob.Status.CLAIMED
            job.claimed_at = timezone.now()
            job.claimed_by_token = token
            job.claimed_by_ip = _client_ip(request)
            job.url_count = len(urls)
            job.save(update_fields=['status', 'claimed_at', 'claimed_by_token', 'claimed_by_ip', 'url_count'])

        return Response({
            'job_id': str(job.id),
            'store_id': str(job.store_id) if job.store_id else None,
            'requested_at': job.requested_at.isoformat(),
            'url_count': len(urls),
            'urls': urls,
        })


class HebIngestJobStatusView(APIView):
    """Runner-facing job status probe.

    Lets the desktop poller check ``GET /ingest/heb/jobs/<id>/`` mid-run so it
    can detect cancellation (status flipped to ``cancelled`` by the web UI)
    and stop its worker subprocesses instead of finishing a job nobody wants.

    Auth: Bearer token, scope ``heb``.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, job_id, *args, **kwargs):
        _authenticate(request, required_scope='heb')
        try:
            job = HebScrapeJob.objects.get(id=job_id)
        except HebScrapeJob.DoesNotExist:
            return Response({'error': 'Job not found.'}, status=status.HTTP_404_NOT_FOUND)

        return Response({
            'job_id': str(job.id),
            'status': job.status,
            'requested_at': job.requested_at.isoformat(),
            'claimed_at': job.claimed_at.isoformat() if job.claimed_at else None,
            'completed_at': job.completed_at.isoformat() if job.completed_at else None,
            'cancelled': job.status == HebScrapeJob.Status.CANCELLED,
        })


class HebIngestCompleteJobView(APIView):
    """Runner reports a claimed job as done (or failed).

    Body (all fields optional):
        {
          "status": "done" | "failed",
          "stats":  {"received": N, "matched": N, "applied": N, ...},
          "note":   "free-form"
        }

    Auth: Bearer token, scope ``heb``. Only the token that claimed the job
    may complete it (prevents cross-runner collisions when multiple desktops
    share a token).
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, job_id, *args, **kwargs):
        token = _authenticate(request, required_scope='heb')

        try:
            job = HebScrapeJob.objects.get(id=job_id)
        except HebScrapeJob.DoesNotExist:
            return Response({'error': 'Job not found.'}, status=status.HTTP_404_NOT_FOUND)

        if job.status not in (HebScrapeJob.Status.CLAIMED, HebScrapeJob.Status.PENDING):
            return Response(
                {'error': f'Job is already {job.status}.'},
                status=status.HTTP_409_CONFLICT,
            )

        payload = request.data if isinstance(request.data, dict) else {}
        wanted_status = (payload.get('status') or 'done').strip().lower()
        if wanted_status not in ('done', 'failed'):
            wanted_status = 'done'

        stats_obj = payload.get('stats') if isinstance(payload.get('stats'), dict) else None
        note = str(payload.get('note') or '')[:2000]

        job.status = (
            HebScrapeJob.Status.DONE if wanted_status == 'done' else HebScrapeJob.Status.FAILED
        )
        job.completed_at = timezone.now()
        if stats_obj:
            job.stats = stats_obj
        if note:
            job.note = note
        job.save(update_fields=['status', 'completed_at', 'stats', 'note'])

        return Response({
            'job_id': str(job.id),
            'status': job.status,
            'completed_at': job.completed_at.isoformat() if job.completed_at else None,
        })
