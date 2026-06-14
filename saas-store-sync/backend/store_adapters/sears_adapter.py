"""
Sears Marketplace API adapter.

Expected Store.api_token format (JSON):
{
  "seller_id": "10673110",
  "email": "seller@example.com",
  "secret_key": "base64-or-plain-secret",
  "location_id": "12345",
  "base_url": "https://seller.marketplace.sears.com/SellerPortal/api"
}

``location_id`` is optional for account connection verification but required for FBM-LMP inventory sync.

Pricing: PUT /pricing/fbm/v6 (XML v6 — standard-price + optional sale block)
Inventory: PUT /inventory/fbm-lmp/v7 (FBM-LMP, default) or /inventory/fbm/v7 (legacy FBM)

After each feed PUT, polls the processing report until Sears accepts or rejects the feed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from xml.sax.saxutils import escape

import requests

from .base import BaseStoreAdapter

SEARS_API_BASE = "https://seller.marketplace.sears.com/SellerPortal/api"
PRICING_NS = "http://seller.marketplace.sears.com/pricing/v6"
INVENTORY_NS = "http://seller.marketplace.sears.com/inventory/v7"
STORE_INVENTORY_NS = "http://seller.marketplace.sears.com/catalog/v7"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
INVENTORY_XSD = (
    "https://seller.marketplace.sears.com/SellerPortal/s/schema/rest/"
    "inventory/import/v7/inventory.xsd"
)
STORE_INVENTORY_XSD = (
    "https://seller.marketplace.sears.com/SellerPortal/s/schema/rest/"
    "inventory/import/v7/store-inventory.xsd"
)
INVENTORY_PATH_LMP = "/inventory/fbm-lmp/v7"
INVENTORY_PATH_FBM = "/inventory/fbm/v7"
PROCESSING_REPORT_PATH = "/reports/v1/processing-report"
SEARS_AUTH_PROBE_PATH = "/oms/purchaseorder/v19"
SEARS_VALIDATE_PROBE_SKU = "0000000000"
DEFAULT_SALE_END_DATE = date(2035, 1, 1)
REPORT_POLL_INTERVAL_SEC = 2
REPORT_POLL_MAX_ATTEMPTS = 15
DEFAULT_SEARS_BULK_BATCH_SIZE = 100

MSG_SEARS_CONNECTED = "Sears account connected successfully."
MSG_SEARS_INVALID_CREDS = "Invalid Sears API credentials."
MSG_SEARS_LOCATION_WARNING = (
    "Connected, but location_id could not be verified for inventory sync."
)

logger = logging.getLogger(__name__)


class SearsAPIError(Exception):
    """Sears API call failed."""

    def __init__(self, message, status_code=None, response_body=None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


def _format_amount(amount) -> str:
    return str(
        Decimal(str(amount)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    )


def _xml_item_id(item_id: str) -> str:
    return escape(str(item_id).strip(), {'"': '&quot;', "'": '&apos;'})


def _xml_attr_value(value: str) -> str:
    return escape(str(value).strip(), {'"': '&quot;', "'": '&apos;'})


def parse_document_id(response_body: str) -> str | None:
    """Extract ``document-id`` from a Sears feed PUT response."""
    if not response_body:
        return None
    match = re.search(r'<document-id>(\d+)</document-id>', response_body)
    return match.group(1) if match else None


def processing_report_pending(response_body: str) -> bool:
    """True while Sears has accepted the feed but not finished processing."""
    if not response_body:
        return True
    if '<records-accepted>' in response_body or '<records-with-errors>' in response_body:
        return False
    return '<status>Submitted</status>' in response_body or '<report>' not in response_body


def parse_processing_report_summary(response_body: str) -> dict:
    """Parse accepted/error counts and messages from a processing report."""
    accepted = re.search(r'<records-accepted>(\d+)</records-accepted>', response_body or '')
    errors = re.search(r'<records-with-errors>(\d+)</records-with-errors>', response_body or '')
    status = re.search(r'<status>([^<]+)</status>', response_body or '')
    return {
        'status': status.group(1) if status else None,
        'accepted': int(accepted.group(1)) if accepted else 0,
        'errors': int(errors.group(1)) if errors else 0,
        'error_infos': re.findall(r'<error-info>([^<]+)</error-info>', response_body or ''),
    }


def parse_processing_report_item_errors(response_body: str) -> dict[str, str]:
    """Map Sears Child SKU (item-id) -> error message when present in report detail."""
    body = response_body or ''
    errors_by_item: dict[str, str] = {}

    for match in re.finditer(
        r'<error\b[^>]*>.*?<item-id>([^<]+)</item-id>.*?<error-info>([^<]+)</error-info>',
        body,
        re.DOTALL | re.IGNORECASE,
    ):
        errors_by_item[match.group(1).strip()] = match.group(2).strip()

    for match in re.finditer(
        r'<item\b[^>]*item-id="([^"]+)"[^>]*>.*?<error-info>([^<]+)</error-info>',
        body,
        re.DOTALL | re.IGNORECASE,
    ):
        sku = match.group(1).strip()
        if sku not in errors_by_item:
            errors_by_item[sku] = match.group(2).strip()

    return errors_by_item


def classify_bulk_feed_results(
    response_body: str,
    expected_item_ids: set[str],
) -> tuple[set[str], list[dict]]:
    """
    Determine per-SKU success/failure from a Sears processing report.

    When item-level errors are present, only SKUs without an error entry are OK.
    When the feed fully succeeds, all expected SKUs are OK.
    When errors exist but cannot be mapped to SKUs, the whole batch is treated as failed
    (safe default — avoids marking the wrong rows synced).
    """
    expected = {str(x).strip() for x in expected_item_ids if str(x).strip()}
    if not expected:
        return set(), []

    summary = parse_processing_report_summary(response_body)
    item_errors = parse_processing_report_item_errors(response_body)

    if summary['errors'] == 0 and summary['accepted'] >= len(expected):
        return set(expected), []

    if item_errors:
        ok = {sku for sku in expected if sku not in item_errors}
        failed = [{'sku': sku, 'error': msg} for sku, msg in item_errors.items() if sku in expected]
        unreported = expected - ok - {f['sku'] for f in failed}
        if unreported and summary['errors'] > 0:
            detail = summary['error_infos'][0] if summary['error_infos'] else 'feed rejected'
            failed.extend({'sku': sku, 'error': detail[:400]} for sku in sorted(unreported))
            ok -= unreported
        return ok, failed

    detail = summary['error_infos'][0] if summary['error_infos'] else 'feed rejected'
    return set(), [{'sku': sku, 'error': detail[:400]} for sku in sorted(expected)]


def _pricing_item_xml_lines(
    item_id: str,
    *,
    standard_price,
    sale_price=None,
    sale_start_date: date | None = None,
    sale_end_date: date | None = None,
) -> list[str]:
    std = _format_amount(standard_price)
    iid = _xml_item_id(item_id)
    lines = [
        f'    <item item-id="{iid}">',
        f'      <standard-price>{std}</standard-price>',
    ]
    use_sale = sale_price is not None
    if use_sale:
        posted = Decimal(std)
        sale_dec = Decimal(_format_amount(sale_price))
        use_sale = sale_dec < posted
    if use_sale:
        start = sale_start_date or (date.today() - timedelta(days=1))
        end = sale_end_date or DEFAULT_SALE_END_DATE
        lines.extend([
            '      <sale>',
            f'        <sale-price>{_format_amount(sale_price)}</sale-price>',
            f'        <sale-start-date>{start.isoformat()}</sale-start-date>',
            f'        <sale-end-date>{end.isoformat()}</sale-end-date>',
            '      </sale>',
        ])
    lines.append('    </item>')
    return lines


def _pricing_fields_for_item(*, price, rrp) -> tuple[str | None, str | None]:
    """Return (standard_price, sale_price) strings for Sears pricing XML."""
    if price is None:
        return None, None
    posted = _format_amount(price)
    if rrp is not None:
        std = _format_amount(rrp)
        if Decimal(std) > Decimal(posted):
            return std, posted
    return posted, None


def build_pricing_feed_xml(
    item_id: str,
    *,
    standard_price,
    sale_price=None,
    sale_start_date: date | None = None,
    sale_end_date: date | None = None,
) -> str:
    """
    Sears pricing v6 XML for one Child SKU (``item-id``).

    When ``sale_price`` is set and below ``standard_price``, includes a ``<sale>`` block
    (posted price + RRP strike-through on Sears). Otherwise only ``standard-price``.
    """
    return build_pricing_feed_xml_bulk([{
        'sku': item_id,
        'standard_price': standard_price,
        'sale_price': sale_price,
        'sale_start_date': sale_start_date,
        'sale_end_date': sale_end_date,
    }])


def build_pricing_feed_xml_bulk(items: list[dict]) -> str:
    """Sears pricing v6 XML for one or more Child SKUs in a single feed."""
    if not items:
        raise SearsAPIError('Sears bulk pricing feed requires at least one item')

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<pricing-feed xmlns="{PRICING_NS}">',
        '  <fbm-pricing>',
    ]
    for raw in items:
        sku = str(raw.get('sku') or raw.get('item_id') or '').strip()
        if not sku:
            raise SearsAPIError('Sears bulk pricing feed item missing sku')

        if raw.get('standard_price') is not None or raw.get('sale_price') is not None:
            std = raw.get('standard_price')
            sale = raw.get('sale_price')
        else:
            std, sale = _pricing_fields_for_item(
                price=raw.get('price'),
                rrp=raw.get('rrp'),
            )
        if std is None:
            continue

        parts.extend(_pricing_item_xml_lines(
            sku,
            standard_price=std,
            sale_price=sale,
            sale_start_date=raw.get('sale_start_date'),
            sale_end_date=raw.get('sale_end_date'),
        ))

    if parts[-1] == '  <fbm-pricing>':
        raise SearsAPIError('Sears bulk pricing feed has no price rows')

    parts.extend(['  </fbm-pricing>', '</pricing-feed>'])
    return '\n'.join(parts)


def _inventory_item_xml_lines_lmp(
    item_id: str,
    quantity: int,
    *,
    location_id: str,
    pick_up_now_eligible: bool,
    inventory_timestamp: str | None,
) -> list[str]:
    iid = _xml_item_id(item_id)
    qty = max(0, int(quantity))
    loc_attr = _xml_attr_value(location_id)
    pick = "true" if pick_up_now_eligible else "false"
    ts_line = ""
    if inventory_timestamp:
        ts_line = f"        <inventory-timestamp>{inventory_timestamp}</inventory-timestamp>\n"
    return [
        f'  <item item-id="{iid}">',
        '    <locations>',
        f'      <location location-id="{loc_attr}">',
        f'        <quantity>{qty}</quantity>',
        f'        <pick-up-now-eligible>{pick}</pick-up-now-eligible>',
        f'{ts_line.rstrip()}',
        '      </location>',
        '    </locations>',
        '  </item>',
    ]


def build_inventory_feed_xml(
    item_id: str,
    quantity: int,
    *,
    lmp: bool = True,
    location_id: str | None = None,
    pick_up_now_eligible: bool = False,
    inventory_timestamp: str | None = None,
) -> str:
    """
    Sears inventory v7 XML for one Child SKU.

    FBM-LMP (default): ``store-inventory`` root (``catalog/v7`` namespace) with per-location
    quantity → PUT /inventory/fbm-lmp/v7. Requires ``location_id`` (Seller Portal fulfillment
    location).

    Legacy FBM: ``inventory-feed`` + ``fbm-inventory`` → PUT /inventory/fbm/v7.
    """
    return build_inventory_feed_xml_bulk([{
        'sku': item_id,
        'stock': quantity,
    }], lmp=lmp, location_id=location_id, pick_up_now_eligible=pick_up_now_eligible,
        inventory_timestamp=inventory_timestamp)


def build_inventory_feed_xml_bulk(
    items: list[dict],
    *,
    lmp: bool = True,
    location_id: str | None = None,
    pick_up_now_eligible: bool = False,
    inventory_timestamp: str | None = None,
) -> str:
    """Sears inventory v7 XML for one or more Child SKUs in a single feed."""
    if not items:
        raise SearsAPIError('Sears bulk inventory feed requires at least one item')

    if lmp:
        loc = (location_id or "").strip()
        if not loc:
            raise SearsAPIError(
                "Sears FBM-LMP inventory requires location_id in store API credentials "
                "(Seller Portal → Account Settings → Fulfillment Location)."
            )
        parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<store-inventory xmlns="{STORE_INVENTORY_NS}"',
            f'    xmlns:xsi="{XSI_NS}"',
            f'    xsi:schemaLocation="{STORE_INVENTORY_NS} {STORE_INVENTORY_XSD}">',
        ]
        for raw in items:
            sku = str(raw.get('sku') or raw.get('item_id') or '').strip()
            if not sku:
                raise SearsAPIError('Sears bulk inventory feed item missing sku')
            if raw.get('stock') is None:
                continue
            parts.extend(_inventory_item_xml_lines_lmp(
                sku,
                raw['stock'],
                location_id=loc,
                pick_up_now_eligible=pick_up_now_eligible,
                inventory_timestamp=inventory_timestamp,
            ))
        if len(parts) == 4:
            raise SearsAPIError('Sears bulk inventory feed has no stock rows')
        parts.append('</store-inventory>')
        return '\n'.join(parts)

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<inventory-feed xmlns="{INVENTORY_NS}"',
        f'    xmlns:xsi="{XSI_NS}"',
        f'    xsi:schemaLocation="{INVENTORY_NS} {INVENTORY_XSD}">',
        '  <fbm-inventory>',
    ]
    for raw in items:
        sku = str(raw.get('sku') or raw.get('item_id') or '').strip()
        if not sku:
            raise SearsAPIError('Sears bulk inventory feed item missing sku')
        if raw.get('stock') is None:
            continue
        iid = _xml_item_id(sku)
        qty = max(0, int(raw['stock']))
        parts.extend([
            f'    <item item-id="{iid}">',
            f'      <quantity>{qty}</quantity>',
            '    </item>',
        ])
    if parts[-1] == '  <fbm-inventory>':
        raise SearsAPIError('Sears bulk inventory feed has no stock rows')
    parts.extend(['  </fbm-inventory>', '</inventory-feed>'])
    return '\n'.join(parts)


class SearsAdapter(BaseStoreAdapter):
    """Sears API adapter: price (standard + sale/RRP) and inventory by Child SKU."""

    def __init__(self, store):
        super().__init__(store)
        self._session = requests.Session()
        self._creds = self._parse_credentials(self._token)
        self._seller_id = (self._creds.get("seller_id") or "").strip()
        self._email = (self._creds.get("email") or "").strip()
        self._secret_key = (self._creds.get("secret_key") or "").strip()
        self._base_url = (self._creds.get("base_url") or SEARS_API_BASE).rstrip("/")
        self._inventory_lmp = self._resolve_inventory_lmp()
        self._location_id = self._resolve_location_id()
        self._pick_up_now_eligible = self._resolve_pick_up_now_eligible()

    @staticmethod
    def _resolve_inventory_lmp_from_creds(creds: dict) -> bool:
        """True = FBM-LMP inventory API (default for newer Sears seller accounts)."""
        for key in ("inventory_program", "sears_inventory_program"):
            val = (creds.get(key) or "").strip().lower()
            if val in ("fbm", "legacy"):
                return False
            if val in ("fbm-lmp", "lmp", "fbm_lmp"):
                return True
        if creds.get("use_fbm_inventory") is True:
            return False
        if creds.get("use_lmp_inventory") is False:
            return False
        return True

    def _resolve_inventory_lmp(self) -> bool:
        return self._resolve_inventory_lmp_from_creds(self._creds)

    @staticmethod
    def _resolve_location_id_from_creds(creds: dict) -> str:
        for key in ("location_id", "sears_location_id", "warehouse_location_id"):
            val = (creds.get(key) or "").strip()
            if val:
                return val
        return ""

    def _resolve_location_id(self) -> str:
        return self._resolve_location_id_from_creds(self._creds)

    @staticmethod
    def _resolve_pick_up_now_eligible_from_creds(creds: dict) -> bool:
        for key in ("pick_up_now_eligible", "pickup_now_eligible"):
            if key in creds:
                return bool(creds.get(key))
        return False

    def _resolve_pick_up_now_eligible(self) -> bool:
        return self._resolve_pick_up_now_eligible_from_creds(self._creds)

    @staticmethod
    def _parse_credentials(raw_token):
        if not raw_token:
            return {}
        txt = str(raw_token).strip()
        if txt.startswith("{") and txt.endswith("}"):
            try:
                data = json.loads(txt)
                if isinstance(data, dict):
                    return data
            except Exception:
                return {}
        return {}

    def _has_auth_creds(self):
        return bool(self._seller_id and self._email and self._secret_key)

    def _has_minimum_creds(self):
        """Full creds for inventory push (includes location_id for FBM-LMP)."""
        if not self._has_auth_creds():
            return False
        if self._inventory_lmp and not self._location_id:
            return False
        return True

    @staticmethod
    def _response_indicates_auth_failure(body: str) -> bool:
        if not body:
            return False
        lower = body.lower()
        markers = (
            'unauthorized',
            'authentication failed',
            'invalid signature',
            'access denied',
            'invalid credentials',
            'forbidden',
        )
        return any(m in lower for m in markers)

    @staticmethod
    def _location_error_in_body(body: str) -> bool:
        if not body:
            return False
        lower = body.lower()
        return any(
            m in lower
            for m in ('location', 'location-id', 'fulfillment location', 'invalid location')
        )

    def _log_verify(self, *, path: str, status_code, body: str | None, level: str = 'info'):
        store_id = getattr(self.store, 'id', None)
        snippet = (body or '')[:500]
        log_fn = logger.warning if level == 'warning' else logger.info
        log_fn(
            "Sears connection verify store_id=%s path=%s status=%s body=%s",
            store_id,
            path,
            status_code,
            snippet,
        )

    def _verify_auth_credentials(self) -> tuple[bool, int | None, str]:
        """Lightweight HMAC-authenticated GET to confirm seller credentials."""
        path = SEARS_AUTH_PROBE_PATH
        params = {"sellerId": self._seller_id, "status": "New"}
        try:
            body = self._request("GET", path, params=params)
            if self._response_indicates_auth_failure(body):
                self._log_verify(path=path, status_code=200, body=body, level='warning')
                return False, 401, body
            self._log_verify(path=path, status_code=200, body=body)
            return True, 200, body
        except SearsAPIError as exc:
            code = exc.status_code or 401
            self._log_verify(
                path=path,
                status_code=code,
                body=exc.response_body,
                level='warning',
            )
            return False, code, exc.response_body or ''

    def _verify_location_id_optional(self) -> bool:
        """
        Optional probe: PUT FBM-LMP inventory feed with probe SKU.
        Success when Sears accepts the feed (document-id), without waiting for SKU acceptance.
        """
        if not self._location_id:
            return True
        import os
        from django.utils import timezone

        probe_sku = (os.getenv("SEARS_VALIDATE_PROBE_SKU") or SEARS_VALIDATE_PROBE_SKU).strip()
        path = INVENTORY_PATH_LMP
        ts = timezone.now().strftime("%Y-%m-%dT%H:%M:%S")
        xml = build_inventory_feed_xml(
            probe_sku,
            0,
            lmp=True,
            location_id=self._location_id,
            pick_up_now_eligible=self._pick_up_now_eligible,
            inventory_timestamp=ts,
        )
        try:
            resp = self._request(
                "PUT",
                path,
                params={"sellerId": self._seller_id},
                data=xml,
            )
            document_id = parse_document_id(resp)
            if document_id:
                self._log_verify(path=path, status_code=200, body=resp)
                return True
            self._log_verify(path=path, status_code=200, body=resp, level='warning')
            return False
        except SearsAPIError as exc:
            code = exc.status_code or 400
            body = exc.response_body or ''
            self._log_verify(path=path, status_code=code, body=body, level='warning')
            if code in (401, 403) or self._location_error_in_body(body):
                return False
            return False

    def test_sears_connection(self) -> tuple[bool, str, int | None, bool | None]:
        """
        Verify Sears credentials (HMAC auth) and optionally location_id.
        Returns (ok, user_message, http_status, location_verified).
        Never logs secret_key.
        """
        if not self._has_auth_creds():
            self._log_verify(
                path='credentials',
                status_code=None,
                body='missing seller_id, email, or secret_key',
                level='warning',
            )
            return False, MSG_SEARS_INVALID_CREDS, None, None

        auth_ok, status_code, _body = self._verify_auth_credentials()
        if not auth_ok:
            return False, MSG_SEARS_INVALID_CREDS, status_code, None

        message = MSG_SEARS_CONNECTED
        location_verified = None
        if self._location_id:
            location_verified = self._verify_location_id_optional()
            if not location_verified:
                message = f"{MSG_SEARS_CONNECTED} {MSG_SEARS_LOCATION_WARNING}"

        return True, message, status_code, location_verified

    def validate_connection(self):
        ok, _msg, _code, _loc = self.test_sears_connection()
        return ok

    def _signature(self, timestamp):
        payload = f"{self._seller_id}:{self._email}:{timestamp}".encode("utf-8")
        key = self._secret_key.encode("utf-8")
        return hmac.new(key, payload, hashlib.sha256).hexdigest()

    def _headers(self, timestamp):
        sig = self._signature(timestamp)
        return {
            "Authorization": (
                f"HMAC-SHA256 emailaddress={self._email},"
                f"timestamp={timestamp},signature={sig}"
            ),
            "Accept": "application/xml",
        }

    def _request(self, method, path, *, params=None, data=None, timeout=30):
        from django.utils import timezone

        timestamp = timezone.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        url = f"{self._base_url}{path}"
        headers = self._headers(timestamp)
        if data is not None:
            headers["Content-Type"] = "application/xml"
        try:
            resp = self._session.request(
                method, url, params=params, data=data, headers=headers, timeout=timeout
            )
        except requests.RequestException as exc:
            raise SearsAPIError(str(exc))
        if resp.status_code >= 400:
            raise SearsAPIError(
                f"Sears API {method} {path}: {resp.status_code}",
                status_code=resp.status_code,
                response_body=resp.text[:500] if resp.text else None,
            )
        return resp.text or ""

    def _wait_for_processing_report(self, document_id: str, *, allow_partial: bool = False) -> str:
        """Poll until Sears finishes async feed processing or raise."""
        path = f"{PROCESSING_REPORT_PATH}/{document_id}"
        params = {"sellerId": self._seller_id}
        last_body = ""
        for attempt in range(REPORT_POLL_MAX_ATTEMPTS):
            if attempt:
                time.sleep(REPORT_POLL_INTERVAL_SEC)
            last_body = self._request("GET", path, params=params)
            if not processing_report_pending(last_body):
                break
        else:
            raise SearsAPIError(
                f"Sears processing report timed out for document {document_id}",
                response_body=last_body[:500] if last_body else None,
            )

        summary = parse_processing_report_summary(last_body)
        if allow_partial:
            if summary["accepted"] < 1 and summary["errors"] > 0:
                detail = summary["error_infos"][0] if summary["error_infos"] else "feed rejected"
                raise SearsAPIError(
                    f"Sears feed rejected (document {document_id}): {detail[:400]}",
                    response_body=last_body[:500] if last_body else None,
                )
            return last_body

        if summary["errors"] > 0 or summary["accepted"] < 1:
            detail = summary["error_infos"][0] if summary["error_infos"] else "feed rejected"
            raise SearsAPIError(
                f"Sears feed rejected (document {document_id}): {detail[:400]}",
                response_body=last_body[:500] if last_body else None,
            )
        return last_body

    def _put_feed_and_verify(
        self,
        path: str,
        xml: str,
        *,
        feed_label: str,
        expected_item_ids: set[str] | None = None,
    ) -> str:
        """PUT XML feed, poll processing report, raise on hard failure."""
        resp = self._request(
            "PUT",
            path,
            params={"sellerId": self._seller_id},
            data=xml,
        )
        document_id = parse_document_id(resp)
        if not document_id:
            raise SearsAPIError(
                f"Sears {feed_label} PUT did not return document-id",
                response_body=resp[:500] if resp else None,
            )
        allow_partial = bool(expected_item_ids and len(expected_item_ids) > 1)
        return self._wait_for_processing_report(document_id, allow_partial=allow_partial)

    def _put_feed_and_classify(
        self,
        path: str,
        xml: str,
        *,
        feed_label: str,
        expected_item_ids: set[str],
    ) -> tuple[set[str], list[dict], str]:
        """PUT XML feed and return per-SKU ok/failed sets from the processing report."""
        resp = self._request(
            "PUT",
            path,
            params={"sellerId": self._seller_id},
            data=xml,
        )
        document_id = parse_document_id(resp)
        if not document_id:
            raise SearsAPIError(
                f"Sears {feed_label} PUT did not return document-id",
                response_body=resp[:500] if resp else None,
            )
        report_body = self._wait_for_processing_report(
            document_id,
            allow_partial=len(expected_item_ids) > 1,
        )
        ok, failed = classify_bulk_feed_results(report_body, expected_item_ids)
        return ok, failed, report_body

    def lookup_listing_by_sku(self, sku: str):
        """Child SKU is the Sears ``item-id`` for price/inventory feeds."""
        return str(sku).strip() if sku else None

    def create_product(self, sku, title, price, stock, **kwargs):
        raise NotImplementedError(
            "Sears create_product requires your finalized Sears listing upload template format."
        )

    def update_product(self, external_id, **kwargs):
        """
        Update listing by Marketplace Child SKU (``item-id``).

        kwargs:
            price: posted/sale price (``store_price``)
            rrp: standard price for strike-through (computed RRP)
            stock: inventory quantity

        When pricing succeeds but inventory fails, pricing is kept and
        ``last_inventory_warning`` is set — push still returns True.

        Pricing and inventory PUTs wait for Sears processing reports; rejected feeds
        raise ``SearsAPIError`` (inventory-only failure after successful pricing
        becomes a warning, not a hard failure).
        """
        self.last_inventory_warning = None
        sku = str(external_id or "").strip()
        if not sku:
            raise SearsAPIError("Missing Sears Child SKU (item-id) for update_product")
        result = self.update_products_bulk([{
            'sku': sku,
            'price': kwargs.get('price'),
            'rrp': kwargs.get('rrp'),
            'stock': kwargs.get('stock'),
        }])
        failed = {str(it.get('sku')): it.get('error') for it in (result.get('failed') or [])}
        if sku in failed:
            raise SearsAPIError(failed[sku] or 'Sears update failed')
        warnings = result.get('warnings') or {}
        self.last_inventory_warning = warnings.get(sku)
        return True

    def update_products_bulk(self, items: list[dict]) -> dict:
        """
        Bulk update many Sears Child SKUs using multi-item XML feeds.

        Each item dict supports: ``sku``, ``price``, ``rrp``, ``stock``.

        Returns ``{'ok': set[str], 'failed': [{'sku': str, 'error': str}], 'warnings': {sku: msg}}``.
        """
        from django.conf import settings

        self.last_inventory_warning = None
        if not items:
            return {'ok': set(), 'failed': [], 'warnings': {}}

        batch_size = max(
            1,
            int(getattr(settings, 'SEARS_BULK_BATCH_SIZE', DEFAULT_SEARS_BULK_BATCH_SIZE) or DEFAULT_SEARS_BULK_BATCH_SIZE),
        )

        ok_all: set[str] = set()
        failed_all: list[dict] = []
        warnings_all: dict[str, str] = {}

        normalized: list[dict] = []
        for raw in items:
            sku = str(raw.get('sku') or raw.get('item_id') or '').strip()
            if not sku:
                failed_all.append({'sku': '', 'error': 'missing sku'})
                continue
            normalized.append({
                'sku': sku,
                'price': raw.get('price'),
                'rrp': raw.get('rrp'),
                'stock': raw.get('stock'),
            })

        for start in range(0, len(normalized), batch_size):
            chunk = normalized[start:start + batch_size]
            chunk_ok, chunk_failed, chunk_warnings = self._bulk_update_chunk(chunk)
            ok_all |= chunk_ok
            failed_all.extend(chunk_failed)
            warnings_all.update(chunk_warnings)

        if warnings_all:
            first = next(iter(warnings_all.values()))
            self.last_inventory_warning = first
        return {'ok': ok_all, 'failed': failed_all, 'warnings': warnings_all}

    def _bulk_update_chunk(self, items: list[dict]) -> tuple[set[str], list[dict], dict[str, str]]:
        """Update one batch of SKUs: one pricing feed + one inventory feed when needed."""
        from django.utils import timezone

        skus = [it['sku'] for it in items]
        sku_set = set(skus)
        price_items = [it for it in items if it.get('price') is not None]
        stock_items = [it for it in items if it.get('stock') is not None]

        price_ok = set(skus)
        price_failed: list[dict] = []

        if price_items:
            try:
                xml = build_pricing_feed_xml_bulk(price_items)
                price_ok, price_failed, _ = self._put_feed_and_classify(
                    "/pricing/fbm/v6",
                    xml,
                    feed_label="pricing",
                    expected_item_ids={it['sku'] for it in price_items},
                )
            except SearsAPIError as exc:
                price_ok = set()
                err = str(exc)[:400]
                price_failed = [{'sku': it['sku'], 'error': err} for it in price_items]

        price_failed_skus = {f['sku'] for f in price_failed}
        final_ok: set[str] = set()
        final_failed: list[dict] = list(price_failed)
        warnings: dict[str, str] = {}

        inv_candidates = [
            it for it in stock_items
            if it['sku'] not in price_failed_skus
        ]

        inv_ok = {it['sku'] for it in inv_candidates}
        inv_failed: list[dict] = []

        if inv_candidates:
            try:
                ts = timezone.now().strftime("%Y-%m-%dT%H:%M:%S")
                xml = build_inventory_feed_xml_bulk(
                    inv_candidates,
                    lmp=self._inventory_lmp,
                    location_id=self._location_id if self._inventory_lmp else None,
                    pick_up_now_eligible=self._pick_up_now_eligible,
                    inventory_timestamp=ts if self._inventory_lmp else None,
                )
                path = INVENTORY_PATH_LMP if self._inventory_lmp else INVENTORY_PATH_FBM
                inv_ok, inv_failed, _ = self._put_feed_and_classify(
                    path,
                    xml,
                    feed_label="inventory",
                    expected_item_ids={it['sku'] for it in inv_candidates},
                )
            except SearsAPIError as exc:
                inv_ok = set()
                err = str(exc)[:400]
                inv_failed = [{'sku': it['sku'], 'error': err} for it in inv_candidates]

        inv_failed_map = {f['sku']: f['error'] for f in inv_failed}

        for it in items:
            sku = it['sku']
            if sku in price_failed_skus:
                continue
            has_price = it.get('price') is not None
            has_stock = it.get('stock') is not None

            if has_price and sku not in price_ok:
                final_failed.append({'sku': sku, 'error': 'pricing not accepted'})
                continue

            if has_stock:
                if sku in inv_failed_map:
                    if has_price and sku in price_ok:
                        final_ok.add(sku)
                        warnings[sku] = f'Price updated on Sears; inventory not updated ({inv_failed_map[sku]})'
                    else:
                        final_failed.append({'sku': sku, 'error': inv_failed_map[sku]})
                    continue
                if sku not in inv_ok:
                    final_failed.append({'sku': sku, 'error': 'inventory not accepted'})
                    continue

            if has_price or has_stock:
                final_ok.add(sku)

        return final_ok, final_failed, warnings

    def update_pricing(
        self,
        item_id: str,
        *,
        standard_price,
        sale_price=None,
        sale_start_date: date | None = None,
        sale_end_date: date | None = None,
    ):
        xml = build_pricing_feed_xml(
            item_id,
            standard_price=standard_price,
            sale_price=sale_price,
            sale_start_date=sale_start_date,
            sale_end_date=sale_end_date,
        )
        self._put_feed_and_verify("/pricing/fbm/v6", xml, feed_label="pricing")
        return True

    def update_price(self, sku, price, currency="USD"):
        """Legacy single-price helper — sets standard-price only."""
        del currency
        return self.update_pricing(str(sku), standard_price=price)

    def update_inventory(self, external_id, stock):
        from django.utils import timezone

        sku = str(external_id or "").strip()
        if not sku:
            raise SearsAPIError("Missing Sears Child SKU (item-id) for update_inventory")
        lmp = self._inventory_lmp
        ts = timezone.now().strftime("%Y-%m-%dT%H:%M:%S")
        xml = build_inventory_feed_xml(
            sku,
            stock,
            lmp=lmp,
            location_id=self._location_id if lmp else None,
            pick_up_now_eligible=self._pick_up_now_eligible,
            inventory_timestamp=ts if lmp else None,
        )
        path = INVENTORY_PATH_LMP if lmp else INVENTORY_PATH_FBM
        self._put_feed_and_verify(path, xml, feed_label="inventory")
        return True

    def delete_product(self, external_id):
        raise NotImplementedError("Sears delete_product not implemented yet.")
