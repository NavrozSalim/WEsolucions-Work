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
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog.models import IngestToken, ProductMapping
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


def _coerce_price(value: Any) -> Decimal | None:
    if value in (None, ''):
        return None
    try:
        d = Decimal(str(value).replace(',', '').strip())
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

        try:
            heb_vendor = Vendor.objects.get(code='heb')
        except Vendor.DoesNotExist:
            return Response(
                {'error': 'HEB vendor is not seeded in this environment.'},
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

            product = (
                Product.objects.filter(vendor=heb_vendor, vendor_url=url).first()
                or Product.objects.filter(vendor=heb_vendor, vendor_url__iexact=url).first()
            )
            if product is None:
                results.append({'index': idx, 'status': 'unmatched', 'url': url})
                stats['skipped'] += 1
                continue

            stats['matched'] += 1

            try:
                with transaction.atomic():
                    VendorPrice.objects.create(
                        product=product,
                        price=price if price is not None else None,
                        stock=stock if (stock is not None and stock >= 0) else None,
                        error_code=error_code[:50] if error_code else None,
                    )
                    applied = 0
                    if price is not None:
                        applied = _apply_to_mappings(
                            product,
                            price,
                            0 if stock is None else stock,
                            title,
                        )
                        stats['applied'] += applied
                results.append({
                    'index': idx,
                    'status': 'ok',
                    'url': url,
                    'product_id': str(product.id),
                    'mappings_updated': applied,
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
