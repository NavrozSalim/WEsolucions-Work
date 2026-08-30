"""Parse Lasoo Connect BulkUpsert / Variants_Search bodies.

Lasoo can return HTTP 200 while the variant is only in seller inventory
(unmapped / not advertised), so callers must inspect mapping fields instead
of treating ``ok`` as "live on lasoo.com.au".
"""
from __future__ import annotations

SEARCH_DATA_FLAGS = {
    "take": 25,
    "page": 1,
    "returnDataObject": True,
    "dataMappingErrors": True,
    "returnMappingInfo": True,
}

_ERROR_KEY_HINTS = (
    "datamappingerrors",
    "mappingerrors",
    "mappingerror",
    "mapping_errors",
    "data_mapping_errors",
    "mappinginfo",
    "mapping_info",
    "catalogruleserrors",
    "errormessages",
    "unmapped",
    "unmappedfields",
    "unmapped_fields",
    "validationerrors",
)

_ADVERTISED_TRUE = {
    "active",
    "advertised",
    "published",
    "live",
    "displayable",
    "mapped",
    "online",
    "approved",
}

_ADVERTISED_FALSE = {
    "unpublished",
    "unmapped",
    "draft",
    "pending",
    "inactive",
    "hidden",
    "rejected",
    "notadvertised",
    "not_advertised",
}


def collect_variant_rows(body) -> list:
    """Best-effort extract of variant dicts from a Search / Upsert envelope."""
    if not isinstance(body, dict):
        return []
    candidates = []
    results = body.get("results")
    if isinstance(results, dict):
        for key in ("variants", "items", "records", "data", "rows", "results"):
            val = results.get(key)
            if isinstance(val, list):
                candidates.append(val)
        for nest_key in ("body", "data"):
            nested = results.get(nest_key)
            if isinstance(nested, dict):
                for key in ("variants", "items", "records", "rows"):
                    val = nested.get(key)
                    if isinstance(val, list):
                        candidates.append(val)
            elif isinstance(nested, list):
                candidates.append(nested)
    elif isinstance(results, list):
        candidates.append(results)
    for key in ("variants", "items", "data", "records"):
        val = body.get(key)
        if isinstance(val, list):
            candidates.append(val)
    for rows in candidates:
        if rows:
            return [r for r in rows if isinstance(r, dict)]
    return []


def _first_str(row: dict, *keys: str) -> str:
    for key in keys:
        val = row.get(key)
        if val is None:
            continue
        if isinstance(val, bool):
            continue
        text = str(val).strip()
        if text:
            return text
    return ""


def _truthy_flag(row: dict, *keys: str):
    """Return True/False if an explicit boolean-like flag is present, else None."""
    for key in keys:
        if key not in row:
            continue
        val = row.get(key)
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, float)) and val in (0, 1):
            return bool(val)
        if isinstance(val, str):
            text = val.strip().lower()
            if text in ("true", "yes", "1"):
                return True
            if text in ("false", "no", "0"):
                return False
    return None


def collect_mapping_errors(body, limit: int = 20) -> list[str]:
    """Collect human-readable Lasoo data-mapping / validation errors."""
    found: list[str] = []
    seen: set[str] = set()

    def add(text: str):
        msg = " ".join(str(text).split())
        if not msg:
            return
        key = msg.casefold()
        if key in seen:
            return
        seen.add(key)
        found.append(msg)

    def walk(node, parent_key: str = ""):
        if len(found) >= limit:
            return
        hint = parent_key.replace(" ", "").replace("-", "").lower()
        is_error_key = any(h in hint for h in _ERROR_KEY_HINTS) if hint else False

        if isinstance(node, bool):
            if node is True and is_error_key:
                add("Lasoo reported data mapping errors for this listing.")
            return
        if isinstance(node, str):
            if is_error_key and node.strip():
                add(node.strip())
            return
        if isinstance(node, dict):
            if is_error_key:
                msg = _first_str(
                    node,
                    "error",
                    "message",
                    "detail",
                    "description",
                )
                field = _first_str(node, "fieldName", "field", "name")
                sku = _first_str(
                    node,
                    "externalVariantKey",
                    "variantKey",
                    "sku",
                    "SKU",
                )
                extras = node.get("errors") or node.get("messages")
                if isinstance(extras, list) and extras:
                    for item in extras:
                        if isinstance(item, str) and item.strip():
                            prefix = sku or field
                            add(f"{prefix}: {item.strip()}" if prefix else item.strip())
                        elif isinstance(item, dict):
                            walk(item, parent_key)
                elif msg:
                    prefix = sku or field
                    add(f"{prefix}: {msg}" if prefix and prefix.casefold() not in msg.casefold() else msg)
                elif field:
                    add(f"Unmapped field: {field}")
            for k, v in node.items():
                walk(v, str(k))
            return
        if isinstance(node, list):
            for item in node:
                walk(item, parent_key)

    walk(body)
    return found[:limit]


def results_success_false(body) -> bool:
    if not isinstance(body, dict):
        return False
    if body.get("success") is False:
        return True
    results = body.get("results")
    return isinstance(results, dict) and results.get("success") is False


def interpret_bulk_upsert(result) -> tuple[bool, str, list[str]]:
    """Return ``(ok, message, errors)`` for a Variants_BulkUpsert result.

    Explicit mapping errors or ``results.success is False`` mean the listing
    is not live on the public site even when HTTP succeeded.
    """
    body = result.data if getattr(result, "ok", False) else (result.data or result.error)
    errors = collect_mapping_errors(body)

    if not getattr(result, "ok", False):
        msg = (getattr(result, "message", None) or "").strip() or "Lasoo rejected the upload."
        if errors and errors[0].casefold() not in msg.casefold():
            msg = f"{msg} {errors[0]}"
        return False, msg, errors

    if results_success_false(body):
        nested = ""
        if isinstance(body, dict):
            results = body.get("results")
            if isinstance(results, dict):
                nested = str(results.get("message") or results.get("error") or "").strip()
            if not nested:
                nested = str(body.get("message") or body.get("error") or "").strip()
            if nested.lower() == "no message":
                nested = ""
        msg = nested or (getattr(result, "message", None) or "").strip() or "Lasoo did not accept the listing."
        if errors:
            extra = "; ".join(errors[:3])
            if extra.casefold() not in msg.casefold():
                msg = f"{msg} {extra}"
        return False, msg, errors

    if errors:
        return (
            False,
            "Lasoo saved the SKU in seller inventory, but data mapping failed "
            "so it will not appear on the public website. "
            + "; ".join(errors[:5]),
            errors,
        )
    return True, (getattr(result, "message", None) or "").strip(), []


def advertised_from_row(row: dict, mapping_errors: list[str] | None = None) -> bool | None:
    """True = live/advertised, False = explicitly not live, None = unknown."""
    if mapping_errors:
        return False
    flag = _truthy_flag(
        row,
        "advertised",
        "isAdvertised",
        "published",
        "isPublished",
        "displayable",
        "isDisplayable",
        "mapped",
        "isMapped",
        "online",
        "isOnline",
    )
    if flag is not None:
        return flag
    status = _first_str(
        row,
        "status",
        "Status",
        "state",
        "State",
        "listingStatus",
        "publishStatus",
        "publishedStatus",
        "mappingStatus",
        "advertisedStatus",
    ).replace(" ", "").replace("_", "").lower()
    if status in _ADVERTISED_TRUE:
        return True
    if status in _ADVERTISED_FALSE:
        return False
    return None


def normalize_variant_hit(row: dict) -> dict:
    mapping_errors = collect_mapping_errors(row)
    advertised = advertised_from_row(row, mapping_errors)
    status = _first_str(
        row,
        "status",
        "Status",
        "state",
        "State",
        "listingStatus",
        "publishStatus",
        "publishedStatus",
        "mappingStatus",
        "advertisedStatus",
    )
    if not status:
        if advertised is True:
            status = "advertised"
        elif advertised is False:
            status = "not advertised"
        else:
            status = "in seller catalog"
    product_key = _first_str(
        row,
        "externalProductKey",
        "ExternalProductKey",
        "productKey",
        "product_key",
    )
    variant_key = _first_str(
        row,
        "externalVariantKey",
        "ExternalVariantKey",
        "variantKey",
        "variant_key",
        "sku",
        "SKU",
    )
    return {
        "product_key": product_key,
        "variant_key": variant_key,
        "sku": variant_key or product_key,
        "title": _first_str(row, "title", "Title", "name", "Name", "productName"),
        "status": status,
        "advertised": advertised,
        "mapping_errors": mapping_errors,
        "created_at": _first_str(
            row,
            "createdAt",
            "created_at",
            "CreatedAt",
            "dateCreated",
            "createdDate",
            "insertedAt",
        ) or None,
        "updated_at": _first_str(
            row,
            "updatedAt",
            "updated_at",
            "UpdatedAt",
            "dateUpdated",
            "modifiedAt",
        ) or None,
        "marketplace_id": _first_str(row, "id", "Id", "variantId", "VariantId") or None,
        "url": _first_str(row, "url", "Url", "webUrl", "permalink", "publicUrl") or None,
    }


def lookup_message(*, found: bool, advertised, mapping_errors: list[str]) -> str:
    if not found:
        return "Not found on Lasoo for this product/variant key."
    if mapping_errors:
        return (
            "Found in Lasoo seller inventory, but data mapping failed — "
            "it will not appear on the public website. "
            + "; ".join(mapping_errors[:5])
        )
    if advertised is True:
        return "Found on Lasoo and advertised (should appear on the public website)."
    if advertised is False:
        return (
            "Found in Lasoo seller inventory, but it is not advertised on the "
            "public website. Check data mapping in Lasoo Connect."
        )
    return (
        "Found in Lasoo seller inventory. Lasoo did not return an advertised / "
        "live status or public URL, so it may not be visible on lasoo.com.au yet "
        "(often a data-mapping issue in Lasoo Connect)."
    )
