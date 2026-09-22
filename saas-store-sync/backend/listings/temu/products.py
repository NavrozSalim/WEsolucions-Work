"""Map managed StoreListing rows to Temu goods payloads and publish them.

Temu identifies a seller's own codes as ``outGoodsSn`` (parent) and ``outSkuSn``
(child). Those are the Hub Parent SKU / SKU, so a listing can be found again for
stock pushes, mapped lookups, and unpublish without storing Temu ids up front.

Temu has no hard "delete listing" for a live product: removal is
``bg.local.goods.sale.status.set`` (off sale) and, when the API allows it,
``temu.local.goods.delete``.
"""
from __future__ import annotations

import json
import logging
from collections import OrderedDict
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from ..errors import MarketplaceError
from ..models import ListingStatus, StoreListing
from .client import TemuClient

logger = logging.getLogger("listings.temu")

# Temu prices are integers in the minor unit (cents) on the local seller APIs.
PRICE_MULTIPLIER = 100
PUBLISH_CHUNK = 20


def _dec(value, default="0") -> Decimal:
    try:
        return Decimal(str(value if value not in (None, "") else default))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _split_urls(raw) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    normalized = text.replace(";", "|").replace("\n", "|").replace(",", "|")
    return [p.strip() for p in normalized.split("|") if p.strip().startswith("http")]


def parse_extras(listing_or_json) -> dict:
    raw = listing_or_json
    if hasattr(listing_or_json, "external_data_object_json"):
        raw = listing_or_json.external_data_object_json
    if isinstance(raw, dict):
        data = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            data = {}
    else:
        data = {}
    if not isinstance(data, dict):
        return {}
    if data.get("marketplace") and str(data.get("marketplace")).lower() != "temu":
        return {}
    return data


def build_extras(data: dict) -> str:
    """Persist Temu-specific optional fields on StoreListing.external_data_object_json."""
    extras = {
        "marketplace": "temu",
        "warehouse_id": str(data.get("warehouse_id") or "").strip(),
        "shipping_template_id": str(data.get("shipping_template_id") or "").strip(),
        "currency": str(data.get("currency") or "").strip().upper() or "AUD",
        "weight": str(data.get("weight") or "").strip(),
        "weight_unit": str(data.get("weight_unit") or "").strip() or "kg",
        "length": str(data.get("length") or "").strip(),
        "width": str(data.get("width") or "").strip(),
        "height": str(data.get("height") or "").strip(),
        "dimension_unit": str(data.get("dimension_unit") or "").strip() or "cm",
        "goods_id": str(data.get("goods_id") or "").strip(),
        "publish_status": "live"
        if str(data.get("publish_status") or data.get("status") or "").strip().lower()
        in ("live", "publish", "published", "active", "on_sale", "true", "1")
        else "draft",
    }
    return json.dumps(extras)


def listing_sku(listing) -> str:
    return (
        getattr(listing, "sku", None) or getattr(listing, "external_variant_key", None) or ""
    ).strip()


def parent_sku(listing) -> str:
    sku = listing_sku(listing)
    return (getattr(listing, "external_product_key", None) or "").strip() or sku


def _photos(listing) -> list[str]:
    photos = []
    variant_img = str(getattr(listing, "variation_image_url", "") or "").strip()
    if variant_img.startswith("http"):
        photos.append(variant_img)
    for url in _split_urls(getattr(listing, "image_urls", "")):
        if url not in photos:
            photos.append(url)
    return photos[:20]


def collect_option_pairs(listing_or_data) -> list[tuple[str, str]]:
    data = listing_or_data if isinstance(listing_or_data, dict) else None
    pairs: list[tuple[str, str]] = []
    for i in (1, 2, 3):
        if data is not None:
            name = str(data.get(f"option_{i}_name") or "").strip()
            value = str(data.get(f"option_{i}_value") or "").strip()
        else:
            name = str(getattr(listing_or_data, f"option_{i}_name", "") or "").strip()
            value = str(getattr(listing_or_data, f"option_{i}_value", "") or "").strip()
        if name or value:
            pairs.append((name, value))
    return pairs


def validate_listing(data: dict) -> list[str]:
    """Human-readable errors for a Temu create/import row."""
    data = dict(data or {})
    errors: list[str] = []
    sku = str(
        data.get("sku") or data.get("variant_key") or data.get("product_key") or ""
    ).strip()
    label = sku or "unknown"
    if not sku:
        errors.append("SKU is required for Temu.")
    if not str(data.get("title") or "").strip():
        errors.append(f"Title is required for SKU {label}.")
    if not str(data.get("description") or "").strip():
        errors.append(f"Description is required for SKU {label}.")
    category = str(data.get("category") or data.get("cat_id") or "").strip()
    if not category.isdigit():
        errors.append(
            f"Temu Category ID (numeric leaf catId) is required for SKU {label}."
        )
    photos = _split_urls(data.get("image_urls"))
    variant_img = str(data.get("variation_image_url") or "").strip()
    if variant_img.startswith("http"):
        photos = [variant_img] + photos
    if not photos:
        errors.append(f'At least one image URL is required for SKU "{label}".')
    price = _dec(data.get("sale_price"))
    if price <= 0:
        price = _dec(data.get("original_price"))
    if price <= 0:
        errors.append(f"Price must be greater than 0 for SKU {label}.")
    if not str(data.get("warehouse_id") or "").strip():
        errors.append(
            f"Temu Warehouse ID is required for SKU {label} "
            "(from bg.logistics.warehouse.list.get)."
        )
    for name, value in collect_option_pairs(data):
        if not name or not value:
            errors.append(
                f"Option name and value must both be set for SKU {label} (e.g. Size / M)."
            )
            break
    return errors


def duplicate_child_sku_errors(rows: list[dict]) -> dict[int, str]:
    """Temu rejects a duplicate outSkuSn — flag both rows before publishing."""
    by_sku: OrderedDict[str, list[int]] = OrderedDict()
    for index, row in enumerate(rows or []):
        sku = str((row or {}).get("sku") or (row or {}).get("variant_key") or "").strip()
        if sku:
            by_sku.setdefault(sku, []).append(index)
    errors: dict[int, str] = {}
    for sku, indexes in by_sku.items():
        if len(indexes) < 2:
            continue
        labels = [
            str((rows[i] or {}).get("row_number") or i + 1) for i in indexes
        ]
        joined = " and ".join(labels) if len(labels) == 2 else ", ".join(labels)
        message = f'SKU "{sku}" is used on more than one row (rows {joined}).'
        for i in indexes:
            errors[i] = message
    return errors


def _price_minor(listing) -> int:
    price = _dec(getattr(listing, "sale_price", None))
    if price <= 0:
        price = _dec(getattr(listing, "original_price", None))
    if price <= 0:
        cents = (
            getattr(listing, "sale_price_cents", None)
            or getattr(listing, "original_price_cents", None)
            or 0
        )
        return int(cents or 0)
    return int((price * PRICE_MULTIPLIER).quantize(Decimal("1")))


def _quantity(listing) -> int:
    if getattr(listing, "infinite_quantity", False):
        return 9999
    try:
        return max(0, int(getattr(listing, "inventory", 0) or 0))
    except (TypeError, ValueError):
        return 0


def sku_payload(listing, extras: dict | None = None) -> dict:
    """One entry of the Temu goods ``skuList``."""
    extras = extras if extras is not None else parse_extras(listing)
    sku = listing_sku(listing)
    if not sku:
        raise MarketplaceError("SKU is required to publish to Temu.")
    price = _price_minor(listing)
    if price <= 0:
        raise MarketplaceError(f'Price must be greater than 0 for SKU "{sku}".')
    warehouse = str(extras.get("warehouse_id") or "").strip()
    if not warehouse:
        raise MarketplaceError(
            f'Temu Warehouse ID is required for SKU "{sku}". Add it on the listing.'
        )
    specs = [
        {"specName": name, "specValue": value}
        for name, value in collect_option_pairs(listing)
        if name and value
    ]
    payload = {
        "outSkuSn": sku,
        "specList": specs,
        "currencyCode": extras.get("currency") or "AUD",
        "basePrice": price,
        "siteSupplierPrices": [{"price": price}],
        "quantity": _quantity(listing),
        "skuStockList": [
            {"warehouseId": warehouse, "targetStockAvailable": _quantity(listing)}
        ],
    }
    variant_img = str(getattr(listing, "variation_image_url", "") or "").strip()
    if variant_img.startswith("http"):
        payload["skuThumbUrl"] = variant_img
    barcode = str(getattr(listing, "barcode", "") or "").strip()
    if barcode:
        payload["extCode"] = barcode
    weight = _dec(extras.get("weight"))
    if weight > 0:
        payload["weight"] = float(weight)
        payload["weightUnit"] = extras.get("weight_unit") or "kg"
    dims = {
        key: float(_dec(extras.get(key)))
        for key in ("length", "width", "height")
        if _dec(extras.get(key)) > 0
    }
    if dims:
        payload["volume"] = {**dims, "unit": extras.get("dimension_unit") or "cm"}
    return payload


def goods_payload(listings: list[StoreListing]) -> dict:
    """Build one Temu goods (parent) payload from rows sharing a Parent SKU."""
    if not listings:
        raise MarketplaceError("No listings to publish to Temu.")
    source = max(
        listings,
        key=lambda item: (
            bool((getattr(item, "title", "") or "").strip()),
            bool((getattr(item, "description", "") or "").strip()),
            len(_photos(item)),
        ),
    )
    extras = parse_extras(source)
    sku = listing_sku(source)
    category = str(getattr(source, "category", "") or "").strip()
    if not category.isdigit():
        raise MarketplaceError(
            f'Temu Category ID (numeric leaf catId) is required for SKU "{sku}".'
        )
    photos: list[str] = []
    for listing in listings:
        for url in _photos(listing):
            if url not in photos:
                photos.append(url)
    if not photos:
        raise MarketplaceError(f'At least one image URL is required for SKU "{sku}".')

    payload = {
        "outGoodsSn": parent_sku(source),
        "catId": int(category),
        "goodsName": (getattr(source, "title", "") or sku)[:500],
        "goodsDesc": getattr(source, "description", "") or getattr(source, "title", "") or sku,
        "carouselImageUrls": photos[:20],
        "goodsThumbUrl": photos[0],
        "skuList": [sku_payload(listing, parse_extras(listing)) for listing in listings],
    }
    brand = str(getattr(source, "brand", "") or "").strip()
    if brand:
        payload["brandName"] = brand
    shipping_template = str(extras.get("shipping_template_id") or "").strip()
    if shipping_template:
        payload["shipmentLimitTemplateId"] = shipping_template
    return payload


def bucket_by_parent(listings: list[StoreListing]) -> list[list[StoreListing]]:
    """Group rows by Parent SKU, dropping repeats of the same child SKU."""
    buckets: OrderedDict[str, list[StoreListing]] = OrderedDict()
    seen_skus: set[str] = set()
    for listing in listings:
        sku = listing_sku(listing)
        if sku in seen_skus:
            continue
        seen_skus.add(sku)
        buckets.setdefault(parent_sku(listing), []).append(listing)
    return list(buckets.values())


def group_by_parent(listings: list[StoreListing]) -> list[tuple[dict, list[StoreListing]]]:
    """Rows sharing a Parent SKU become one Temu goods with several SKUs."""
    return [(goods_payload(members), members) for members in bucket_by_parent(listings)]


def _mark_listing(listing: StoreListing, *, status: str, request=None, response=None, errors=None):
    listing.status = status
    if request is not None:
        listing.marketplace_request_json = request
    if response is not None:
        listing.marketplace_response_json = (
            response if isinstance(response, (dict, list)) else {"raw": response}
        )
    listing.validation_errors_json = errors
    if status in (ListingStatus.UPLOADED_PRODUCTION, ListingStatus.UPLOADED_STAGING):
        listing.last_uploaded_at = timezone.now()
    listing.save(
        update_fields=[
            "status",
            "marketplace_request_json",
            "marketplace_response_json",
            "last_uploaded_at",
            "validation_errors_json",
            "updated_at",
        ]
    )


def _store_goods_id(listing: StoreListing, goods_id: str) -> None:
    """Remember the Temu goodsId so stock/unpublish skip a lookup next time."""
    if not goods_id:
        return
    extras = parse_extras(listing)
    if str(extras.get("goods_id") or "") == str(goods_id):
        return
    extras["marketplace"] = "temu"
    extras["goods_id"] = str(goods_id)
    listing.external_data_object_json = json.dumps(extras)
    listing.save(update_fields=["external_data_object_json", "updated_at"])


def _goods_id_from_result(data) -> str:
    if isinstance(data, dict):
        for key in ("goodsId", "goods_id", "id"):
            val = data.get(key)
            if val not in (None, ""):
                return str(val).strip()
        nested = data.get("result") if isinstance(data.get("result"), dict) else None
        if nested:
            return _goods_id_from_result(nested)
    return ""


def _goods_rows(data) -> list[dict]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("goodsList", "goods_list", "data", "list", "items"):
            val = data.get(key)
            if isinstance(val, list):
                return [row for row in val if isinstance(row, dict)]
        if data.get("goodsId") or data.get("outGoodsSn"):
            return [data]
    return []


def _sku_rows(data) -> list[dict]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("skuList", "sku_list", "data", "list", "items"):
            val = data.get(key)
            if isinstance(val, list):
                return [row for row in val if isinstance(row, dict)]
        if data.get("skuId") or data.get("outSkuSn"):
            return [data]
    return []


def lookup_goods(store, sku: str, *, client: TemuClient | None = None) -> dict | None:
    """Find a Temu goods row by the seller's own SKU (outGoodsSn / outSkuSn)."""
    needle = (sku or "").strip()
    if not needle:
        return None
    client = client or TemuClient(store)
    result = client.list_goods(page_size=50, search_text=needle)
    if not result.ok:
        raise MarketplaceError(result.message or "Temu product lookup failed.")
    lowered = needle.lower()
    for row in _goods_rows(result.data):
        if str(row.get("outGoodsSn") or "").strip().lower() == lowered:
            return row
        for sku_row in _sku_rows(row.get("skuList") or row.get("sku_list")):
            if str(sku_row.get("outSkuSn") or "").strip().lower() == lowered:
                return row
    return None


def lookup_sku(store, sku: str, *, client: TemuClient | None = None) -> dict | None:
    """Resolve one seller SKU to its Temu ``{goodsId, skuId, ...}`` row."""
    needle = (sku or "").strip()
    if not needle:
        return None
    client = client or TemuClient(store)
    goods = lookup_goods(store, needle, client=client)
    if not goods:
        return None
    goods_id = str(goods.get("goodsId") or goods.get("goods_id") or "").strip()
    rows = _sku_rows(goods.get("skuList") or goods.get("sku_list"))
    if not rows and goods_id:
        sku_result = client.list_skus(goods_id)
        if sku_result.ok:
            rows = _sku_rows(sku_result.data)
    lowered = needle.lower()
    for row in rows:
        if str(row.get("outSkuSn") or "").strip().lower() == lowered:
            return {
                "goods_id": goods_id,
                "sku_id": str(row.get("skuId") or row.get("sku_id") or "").strip(),
                "out_sku_sn": str(row.get("outSkuSn") or "").strip(),
                "goods_name": str(goods.get("goodsName") or "").strip(),
                "status": str(
                    goods.get("goodsStatusDesc")
                    or goods.get("saleStatus")
                    or goods.get("goodsStatus")
                    or ""
                ).strip(),
                "_raw": {"goods": goods, "sku": row},
            }
    if goods_id:
        return {
            "goods_id": goods_id,
            "sku_id": "",
            "out_sku_sn": str(goods.get("outGoodsSn") or "").strip(),
            "goods_name": str(goods.get("goodsName") or "").strip(),
            "status": str(
                goods.get("goodsStatusDesc")
                or goods.get("saleStatus")
                or goods.get("goodsStatus")
                or ""
            ).strip(),
            "_raw": {"goods": goods},
        }
    return None


def publish_listings(user, store, listings: list[StoreListing]) -> dict:
    """bg.local.goods.add per parent SKU (update when Temu already has it)."""
    client = TemuClient(store)
    target_status = ListingStatus.UPLOADED_PRODUCTION

    prepared: list[StoreListing] = []
    for listing in listings:
        try:
            sku_payload(listing, parse_extras(listing))
        except MarketplaceError as exc:
            listing.status = ListingStatus.VALIDATION_FAILED
            listing.validation_errors_json = [str(exc)]
            listing.save(update_fields=["status", "validation_errors_json", "updated_at"])
            continue
        prepared.append(listing)

    if not prepared:
        raise MarketplaceError(
            "No valid listings to publish to Temu. Fix validation errors first."
        )

    # Build one payload per parent so a bad parent fails only its own rows.
    packed: list[tuple[dict, list[StoreListing]]] = []
    invalid = 0
    for members in bucket_by_parent(prepared):
        try:
            packed.append((goods_payload(members), members))
        except MarketplaceError as exc:
            for listing in members:
                listing.status = ListingStatus.VALIDATION_FAILED
                listing.validation_errors_json = [str(exc)]
                listing.save(update_fields=["status", "validation_errors_json", "updated_at"])
            invalid += len(members)

    if not packed:
        raise MarketplaceError(
            "No valid listings to publish to Temu. Fix validation errors first."
        )

    published = 0
    failed = invalid
    last_error = ""
    for payload, members in packed:
        existing_id = ""
        try:
            found = lookup_goods(store, payload["outGoodsSn"], client=client)
            existing_id = str(
                (found or {}).get("goodsId") or (found or {}).get("goods_id") or ""
            ).strip()
        except MarketplaceError as exc:
            logger.info("Temu pre-publish lookup skipped for %s: %s", payload["outGoodsSn"], exc)

        if existing_id:
            result = client.update_goods({**payload, "goodsId": existing_id})
        else:
            result = client.add_goods(payload)

        if result.ok:
            goods_id = _goods_id_from_result(result.data) or existing_id
            for listing in members:
                _mark_listing(
                    listing,
                    status=target_status,
                    request={"out_goods_sn": payload["outGoodsSn"], "goods_id": goods_id},
                    response=result.as_dict(),
                    errors=None,
                )
                _store_goods_id(listing, goods_id)
            published += len(members)
            continue

        err = (result.message or "Temu publish failed.")[:400]
        last_error = err
        for listing in members:
            _mark_listing(
                listing,
                status=ListingStatus.FAILED,
                request={"out_goods_sn": payload["outGoodsSn"]},
                response={"error": err, "data": result.data},
                errors=[err],
            )
        failed += len(members)
        if result.is_auth_error:
            break

    if published and failed:
        message = f"Published {published} listing(s) to Temu; {failed} failed."
    elif published:
        message = f"Published {published} listing(s) to Temu."
    else:
        message = last_error or "Temu publish failed."

    return {
        "ok": published > 0 and failed == 0,
        "published": published,
        "uploaded": published,
        "failed": failed,
        "message": message,
        "environment": "production",
    }


def push_inventory(listings: list[StoreListing], store) -> dict:
    """bg.local.goods.stock.edit (+ price change when Temu accepts it)."""
    if not listings:
        return {"ok": False, "message": "No listings to push.", "updated": 0}
    client = TemuClient(store)

    stock_changes: list[dict] = []
    price_changes: list[dict] = []
    skipped: list[str] = []
    for listing in listings:
        sku = listing_sku(listing)
        if not sku:
            continue
        extras = parse_extras(listing)
        warehouse = str(extras.get("warehouse_id") or "").strip()
        resolved = lookup_sku(store, sku, client=client)
        sku_id = str((resolved or {}).get("sku_id") or "").strip()
        if not sku_id:
            skipped.append(sku)
            continue
        change = {"skuId": sku_id, "targetStockAvailable": _quantity(listing)}
        if warehouse:
            change["warehouseId"] = warehouse
        stock_changes.append(change)
        price = _price_minor(listing)
        if price > 0:
            price_changes.append({"skuId": sku_id, "price": price})

    if not stock_changes:
        sample = ", ".join(skipped[:8])
        raise MarketplaceError(
            "None of these SKUs were found on Temu. Publish from Created products first, "
            "or fix the SKU so it matches the Temu outSkuSn."
            + (f" Skipped: {sample}." if sample else "")
        )

    result = client.edit_stock(stock_changes)
    price_message = ""
    if result.ok and price_changes:
        price_result = client.change_sku_price(price_changes)
        if not price_result.ok:
            price_message = (
                " Stock updated, but Temu rejected the price change: "
                f"{price_result.message or 'price update failed'}."
            )

    extra = ""
    if skipped:
        extra = (
            f" Skipped {len(skipped)} SKU(s) not on Temu"
            f" ({', '.join(skipped[:8])}{'…' if len(skipped) > 8 else ''})."
        )
    return {
        "ok": result.ok,
        "message": (
            result.message or ("Price/stock pushed to Temu." if result.ok else "Temu stock update failed.")
        )
        + price_message
        + extra,
        "updated": len(stock_changes) if result.ok else 0,
        "skipped": len(skipped),
    }


def end_listing(store, listing: StoreListing) -> bool:
    """Take the product off sale on Temu, then delete it when the API allows.

    Temu has no guaranteed hard delete for a live product, so off-sale is the
    real removal. A SKU that is already gone from Temu returns False so the
    local row can still be deleted.
    """
    sku = listing_sku(listing)
    if not sku:
        return False
    client = TemuClient(store)
    extras = parse_extras(listing)
    goods_id = str(extras.get("goods_id") or "").strip()
    if not goods_id:
        resolved = lookup_sku(store, sku, client=client)
        goods_id = str((resolved or {}).get("goods_id") or "").strip()
    if not goods_id:
        return False

    off_sale = client.set_sale_status(goods_id, on_sale=False)
    if not off_sale.ok:
        msg = (off_sale.message or "").lower()
        if "not found" in msg or "not exist" in msg:
            return False
        raise MarketplaceError(
            off_sale.message or "Could not take the Temu listing off sale."
        )

    deleted = client.delete_goods(goods_id)
    if not deleted.ok:
        logger.info(
            "Temu goods %s taken off sale; delete not available: %s",
            goods_id,
            deleted.message,
        )
    return True


def cancel_reason_options() -> list[dict]:
    """Temu seller cancellation is out-of-stock based (pre-ship only)."""
    return [
        {"value": "OUT_OF_STOCK", "label": "Out of stock"},
        {"value": "PRICE_ERROR", "label": "Price error"},
        {"value": "CANNOT_FULFILL", "label": "Cannot fulfill in time"},
        {"value": "OTHER", "label": "Other"},
    ]
