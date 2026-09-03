"""Remove variants from Lasoo Connect (Variants_BulkDelete + Search verify).

Hub must not delete a local listing while Connect still has the SKU. BulkDelete
has crashed with a Node ``.map`` error on thin payloads; we try several body
shapes and only treat delete as success when Variants_Search no longer matches.
"""
from __future__ import annotations

import logging

from ..errors import MarketplaceError
from . import mapper
from .connect_search import search_variant

logger = logging.getLogger("listings.lasoo")

DELETE_STILL_PRESENT = (
    'Lasoo Connect still has SKU "{skus}". '
    "It was left in this app so you can retry. "
    "Delete the variant in Lasoo Connect if this keeps failing."
)

SEARCH_FAILED = (
    'Could not verify SKU "{sku}" on Lasoo Connect before deleting. '
    "The listing was left in this app. {reason}"
)


def _norm(value) -> str:
    return str(value or "").strip()


def _item_keys(item: dict) -> tuple[str, str, str]:
    sku = _norm(item.get("sku") or item.get("variant_key") or item.get("product_key"))
    variant_key = _norm(item.get("variant_key") or sku)
    product_key = _norm(item.get("product_key") or sku)
    return product_key, variant_key, sku or variant_key


def _humanize_delete_error(message: str) -> str:
    text = (message or "").strip()
    low = text.lower()
    if "reading 'map'" in low or "cannot read properties of undefined" in low:
        return (
            "Lasoo Connect rejected BulkDelete (API error). "
            "The listing was not removed on Lasoo."
        )
    return text


def lookup_present(client, items: list[dict]) -> list[dict]:
    """Return the subset of items Variants_Search still finds.

    Raises MarketplaceError if Search itself fails — do not Hub-delete then.
    """
    present = []
    for item in items:
        product_key, variant_key, sku = _item_keys(item)
        if not variant_key:
            continue
        searched = search_variant(
            client,
            product_key=product_key,
            variant_key=variant_key,
            sku=sku,
        )
        if not searched.get("ok"):
            reason = (searched.get("message") or "Lasoo search failed.").strip()
            raise MarketplaceError(SEARCH_FAILED.format(sku=sku or variant_key, reason=reason))
        if searched.get("found"):
            present.append({
                "product_key": product_key,
                "variant_key": variant_key,
                "sku": sku or variant_key,
            })
    return present


def _try_payload_shapes(client, items: list[dict]) -> tuple[list[dict], str]:
    """Send BulkDelete shapes until Search is empty or shapes are exhausted."""
    remaining = list(items)
    last_error = ""
    variant_keys = [row["variant_key"] for row in remaining]
    product_keys = [row["product_key"] for row in remaining]
    for name, payload in mapper.iter_bulk_delete_payloads(
        variant_keys, client.auth_key, product_keys=product_keys,
    ):
        result = client.send("bulk_delete", payload)
        if not getattr(result, "ok", False):
            last_error = (getattr(result, "message", None) or last_error or "").strip()
            logger.info(
                "Lasoo BulkDelete shape=%s failed skus=%s: %s",
                name, variant_keys, last_error,
            )
        else:
            logger.info("Lasoo BulkDelete shape=%s HTTP ok skus=%s", name, variant_keys)
        remaining = lookup_present(client, remaining)
        if not remaining:
            return [], last_error
    return remaining, last_error


def delete_connect_variants(client, items: list[dict]) -> int:
    """Delete Connect variants. Returns how many were present and are now gone.

    Raises MarketplaceError if any requested SKU is still in Connect afterwards.
    Callers must not delete Hub rows when this raises.
    """
    wanted = []
    seen = set()
    for raw in items or []:
        product_key, variant_key, sku = _item_keys(raw if isinstance(raw, dict) else {})
        if not variant_key:
            continue
        token = variant_key.casefold()
        if token in seen:
            continue
        seen.add(token)
        wanted.append({
            "product_key": product_key,
            "variant_key": variant_key,
            "sku": sku,
        })
    if not wanted:
        return 0

    present = lookup_present(client, wanted)
    if not present:
        return 0
    initial = len(present)

    remaining, last_error = _try_payload_shapes(client, present)
    if remaining and len(remaining) > 1:
        leftover = []
        for item in remaining:
            still, err = _try_payload_shapes(client, [item])
            if err:
                last_error = err
            leftover.extend(still)
        remaining = leftover

    if remaining:
        skus = ", ".join(row["sku"] for row in remaining)
        extra = _humanize_delete_error(last_error)
        message = DELETE_STILL_PRESENT.format(skus=skus)
        if extra:
            message = f"{message} {extra}"
        logger.warning("Lasoo delete left SKUs on Connect: %s last_error=%s", skus, last_error)
        raise MarketplaceError(message)
    return initial
