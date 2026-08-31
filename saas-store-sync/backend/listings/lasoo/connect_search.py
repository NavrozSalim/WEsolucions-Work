"""Lasoo Connect Variants_Search helpers.

Used by Mapped import, save-and-push, inventory sync, and Check on marketplace.
A SKU is "found" only when Search returns a variant whose keys match. HTTP 200
with an empty variants list is not found — never treat envelope success as live.
"""
from __future__ import annotations

from .queries import build_payload
from .response import (
    SEARCH_DATA_FLAGS,
    collect_mapping_errors,
    collect_variant_rows,
    lookup_message,
    normalize_variant_hit,
)

NOT_IN_CONNECT_MAPPED = (
    'SKU "{sku}" was not found in Lasoo Connect seller inventory. '
    "Mapped only links products that already exist there. "
    "A listing on lasoo.com.au may use a different SKU. "
    "Fix the Hub SKU to match Connect, or use Create to add a new variant."
)

NOT_IN_CONNECT_PUSH = (
    'Saved here but not pushed to Lasoo. Connect has no variant for SKU "{sku}". '
    "Save and push cannot update the public page until this Hub SKU matches "
    "Lasoo Connect seller inventory."
)

VERIFY_FAILED = (
    "Could not verify SKU \"{sku}\" on Lasoo Connect ({reason}). "
    "Hub will not mark it uploaded or push until Connect can be checked."
)


def _norm(value) -> str:
    return str(value or "").strip()


def _wanted_keys(*values: str) -> set[str]:
    return {v.casefold() for v in values if _norm(v)}


def keys_match(hit: dict, *, product_key: str, variant_key: str, sku: str) -> bool:
    """True when a Search row is the variant we asked for (case-insensitive)."""
    wanted = _wanted_keys(product_key, variant_key, sku)
    if not wanted:
        return False
    for val in (
        hit.get("product_key"),
        hit.get("variant_key"),
        hit.get("sku"),
    ):
        text = _norm(val)
        if text and text.casefold() in wanted:
            return True
    return False


def search_variant(
    client,
    *,
    product_key: str,
    variant_key: str,
    sku: str = "",
) -> dict:
    """Search Connect for one variant.

    Returns:
        ok: API call succeeded
        found: a variant row matched our keys
        hit: normalized matching row or None
        mapping_errors: mapping errors from envelope and/or row
        advertised: True/False/None from the hit
        message: user-facing summary
        raw: API body (or error payload)
    """
    product_key = _norm(product_key) or _norm(sku) or _norm(variant_key)
    variant_key = _norm(variant_key) or _norm(sku) or product_key
    sku = _norm(sku) or variant_key
    empty = {
        "ok": False,
        "found": False,
        "hit": None,
        "mapping_errors": [],
        "advertised": None,
        "message": "",
        "raw": None,
        "query": {
            "sku": sku,
            "product_key": product_key,
            "variant_key": variant_key,
        },
    }
    if not sku:
        empty["message"] = "SKU is required."
        return empty

    payload = build_payload(
        "variants_search",
        data={
            "externalProductKey": product_key,
            "externalVariantKey": variant_key,
            **SEARCH_DATA_FLAGS,
        },
        auth=getattr(client, "auth_key", None),
    )
    result = client.send("variants_search", payload)
    body = result.data if getattr(result, "ok", False) else (result.data or result.error)
    empty["raw"] = body

    if not getattr(result, "ok", False):
        reason = (getattr(result, "message", None) or "Lasoo search failed.").strip()
        empty["message"] = VERIFY_FAILED.format(sku=sku, reason=reason)
        return empty

    rows = collect_variant_rows(body)
    envelope_errors = collect_mapping_errors(body)
    matched = []
    for row in rows:
        hit = normalize_variant_hit(row)
        if envelope_errors and not hit.get("mapping_errors"):
            hit["mapping_errors"] = list(envelope_errors)
            if hit.get("advertised") is None:
                hit["advertised"] = False
        if keys_match(
            hit,
            product_key=product_key,
            variant_key=variant_key,
            sku=sku,
        ):
            matched.append(hit)

    found = bool(matched)
    top = matched[0] if matched else None
    mapping_errors = list((top or {}).get("mapping_errors") or envelope_errors or [])
    advertised = (top or {}).get("advertised")
    message = lookup_message(
        found=found,
        advertised=advertised,
        mapping_errors=mapping_errors,
    )
    return {
        "ok": True,
        "found": found,
        "hit": top,
        "mapping_errors": mapping_errors,
        "advertised": advertised,
        "message": message,
        "raw": body,
        "query": {
            "sku": sku,
            "product_key": product_key,
            "variant_key": variant_key,
        },
    }
