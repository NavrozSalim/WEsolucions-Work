"""Delete MyDeal products that are on the marketplace but not in this app.

A SKU counts as mapped when a listing in this app or an active catalog
mapping uses it. Those are deleted with the normal "Mapped products" file.

API stores: the buyable is found in the live MyDeal catalog, its quantity is
set to 0, its ListingStatus is set to NotLive, and the inventory-template row
is marked Discontinued TRUE. Upload-template stores have no product API, so
only the inventory-template row is marked Discontinued TRUE (stock 0 on the
next inventory CSV).
"""
from __future__ import annotations

from django.db.models import Q

from ..errors import MarketplaceError
from ..models import StoreListing

DELETE_SCOPE_MAPPED = "mapped"
DELETE_SCOPE_UNMAPPED = "unmapped"


def normalize_delete_scope(raw) -> str:
    scope = str(raw or "").strip().lower()
    if scope in (DELETE_SCOPE_MAPPED, DELETE_SCOPE_UNMAPPED):
        return scope
    return ""


def _key(value) -> str:
    return str(value or "").strip().casefold()


def uses_api(store) -> bool:
    method = (getattr(store, "mydeal_setup_method", None) or "upload").strip().lower()
    return method == "api"


def mapped_sku_keys(store) -> set[str]:
    """Casefolded SKUs used by listings in this app or active catalog mappings."""
    from catalog.models import ProductMapping

    keys: set[str] = set()
    listings = StoreListing.objects.filter(store=store).values_list(
        "sku", "external_variant_key", "external_product_key",
    )
    for values in listings.iterator(chunk_size=1000):
        for value in values:
            key = _key(value)
            if key:
                keys.add(key)
    mappings = (
        ProductMapping.objects.filter(store=store, is_active=True)
        .values_list("marketplace_child_sku", "marketplace_parent_sku", "product__vendor_sku")
    )
    for values in mappings.iterator(chunk_size=1000):
        for value in values:
            key = _key(value)
            if key:
                keys.add(key)
    return keys


def _buyable_entries(group: dict) -> list[dict]:
    """One delete item per buyable in a MyDeal product group."""
    from ..marketplace_lookup import _ci_get, _ci_value

    product_sku = _ci_get(group, "ProductSKU")
    product_id = _ci_get(group, "ExternalProductId", "ExternalProductID")
    entries = []
    for buyable in _ci_value(group, "BuyableProducts") or []:
        if not isinstance(buyable, dict):
            continue
        sku = _ci_get(buyable, "SKU")
        buyable_id = _ci_get(buyable, "ExternalBuyableProductId", "ExternalBuyableProductID")
        if not (sku or buyable_id):
            continue
        entries.append({
            "sku": product_sku or product_id,
            "external_product_id": product_id or product_sku,
            "buyable_sku": sku or buyable_id,
            "external_buyable_id": buyable_id or sku,
            "title": _ci_get(group, "Title"),
            "listing_status": _ci_get(buyable, "ListingStatus"),
        })
    return entries


def _entry_keys(entry: dict) -> set[str]:
    return {k for k in (_key(entry.get("buyable_sku")), _key(entry.get("external_buyable_id"))) if k}


def _catalog_groups(client) -> list[dict]:
    """Every product group on the MyDeal seller account (Live and NotLive)."""
    from .. import marketplace_lookup as ml

    groups: list[dict] = []
    seen: set[str] = set()
    for listing_status in ml._MYDEAL_LIST_STATUSES:
        page = 1
        while page <= ml._MYDEAL_LIST_MAX_PAGES:
            result = client.list_products(
                page=page,
                limit=ml._MYDEAL_LIST_PAGE_LIMIT,
                listing_status=listing_status,
                fields=ml._MYDEAL_LIST_FIELDS,
            )
            if not result.ok:
                if ml._mydeal_is_not_found(result):
                    break
                raise MarketplaceError(result.message or "Could not list MyDeal products.")
            page_groups = ml._mydeal_groups(result.data)
            if not page_groups:
                break
            for group in page_groups:
                gid = "|".join(sorted(ml._mydeal_group_ids(group)))
                buyables = ",".join(sorted(
                    "|".join(sorted(ml._mydeal_buyable_ids(b)))
                    for b in (ml._ci_value(group, "BuyableProducts") or [])
                    if isinstance(b, dict)
                ))
                token = f"{gid}#{buyables}".casefold()
                if token in seen:
                    continue
                seen.add(token)
                groups.append(group)
            if len(page_groups) < ml._MYDEAL_LIST_PAGE_LIMIT:
                break
            page += 1
    return groups


def _catalog_entries(store) -> list[dict]:
    from .client import MyDealClient

    client = MyDealClient(store)
    entries: list[dict] = []
    seen: set[str] = set()
    for group in _catalog_groups(client):
        for entry in _buyable_entries(group):
            token = _key(entry["buyable_sku"]) + "|" + _key(entry["external_buyable_id"])
            if token in seen:
                continue
            seen.add(token)
            entry["group_keys"] = {
                k for k in (_key(entry["sku"]), _key(entry["external_product_id"])) if k
            }
            entries.append(entry)
    return entries


def _inventory_template_skus(store) -> list[str]:
    from catalog.models import MydealTemplateRow

    out: list[str] = []
    seen: set[str] = set()
    rows = (
        MydealTemplateRow.objects.filter(store=store, kind=MydealTemplateRow.Kind.INVENTORY)
        .order_by("row_number")
        .only("sku")
    )
    for row in rows.iterator(chunk_size=1000):
        sku = str(row.sku or "").strip()
        key = _key(sku)
        if key and key not in seen:
            seen.add(key)
            out.append(sku)
    return out


def unmapped_skus(store) -> list[str]:
    """SKUs on MyDeal that are not mapped in this app, in MyDeal order."""
    mapped = mapped_sku_keys(store)
    if not uses_api(store):
        return [sku for sku in _inventory_template_skus(store) if _key(sku) not in mapped]
    out: list[str] = []
    for entry in _catalog_entries(store):
        if _entry_keys(entry) & mapped:
            continue
        out.append(entry["buyable_sku"])
    return out


def _result_row(row: dict, sku: str) -> dict:
    return {
        "row_number": row.get("row_number"),
        "sku": sku,
        "variant_key": sku,
        "errors": [],
        "valid": True,
        "imported": False,
    }


def _fail(row_result: dict, message: str) -> None:
    row_result["errors"] = [message]
    row_result["valid"] = False


def delete_unmapped_rows(store, rows: list[dict]) -> tuple[list[dict], int, str]:
    """Process a "Not mapped (on MyDeal only)" delete file.

    Returns (row results, deleted count, message).
    """
    from catalog.mydeal_templates import mark_inventory_discontinued

    mapped = mapped_sku_keys(store)
    api = uses_api(store)
    preview: list[dict] = []
    pending: list[tuple[dict, list[dict]]] = []
    seen: set[str] = set()

    entries_by_buyable: dict[str, dict] = {}
    entries_by_group: dict[str, list[dict]] = {}
    template_keys: set[str] = set()
    if api:
        for entry in _catalog_entries(store):
            for key in _entry_keys(entry):
                entries_by_buyable.setdefault(key, entry)
            for key in entry["group_keys"]:
                entries_by_group.setdefault(key, []).append(entry)
    else:
        template_keys = {_key(sku) for sku in _inventory_template_skus(store)}
        if not template_keys:
            raise MarketplaceError(
                "Upload the MyDeal inventory template before deleting products "
                "that are on MyDeal but not mapped."
            )

    for row in rows:
        sku = str(row.get("sku") or "").strip()
        row_result = _result_row(row, sku)
        preview.append(row_result)
        key = _key(sku)
        if not key:
            _fail(row_result, "SKU is required to delete a listing.")
            continue
        if key in seen:
            _fail(row_result, f'SKU "{sku}" is listed more than once in this file.')
            continue
        seen.add(key)
        if key in mapped:
            _fail(
                row_result,
                f'SKU "{sku}" is mapped in this app. Choose "Mapped products" to delete it.',
            )
            continue
        if not api:
            if key not in template_keys:
                _fail(row_result, f'SKU "{sku}" is not on the MyDeal inventory template.')
                continue
            pending.append((row_result, [{"buyable_sku": sku, "external_buyable_id": sku}]))
            continue

        entry = entries_by_buyable.get(key)
        if entry is not None:
            targets = [entry]
        else:
            targets = entries_by_group.get(key) or []
        if not targets:
            _fail(row_result, f'SKU "{sku}" was not found on MyDeal.')
            continue
        mapped_variants = [t["buyable_sku"] for t in targets if _entry_keys(t) & mapped]
        if mapped_variants:
            _fail(
                row_result,
                f'Product "{sku}" has variants mapped in this app '
                f'({", ".join(mapped_variants[:5])}). List only the variant SKUs to delete.',
            )
            continue
        pending.append((row_result, targets))

    if not pending:
        return preview, 0, ""

    items = [target for _row, targets in pending for target in targets]
    if api:
        from .client import MyDealClient

        client = MyDealClient(store)
        zero = client.zero_quantity(items)
        error = "" if zero.ok else (zero.message or "Could not set MyDeal inventory to 0.")
        if not error:
            ended = client.end_listings(items)
            if not ended.ok:
                error = ended.message or "Could not set MyDeal listings to NotLive."
        if error:
            for row_result, _targets in pending:
                _fail(row_result, error)
            return preview, 0, error[:500]

    skus: list[str] = []
    for item in items:
        skus.extend((item.get("buyable_sku"), item.get("external_buyable_id")))
    mark_inventory_discontinued(store, skus, discontinued=True)

    for row_result, _targets in pending:
        row_result["imported"] = True
    deleted = len(pending)
    if api:
        message = (
            f"Set {deleted} MyDeal product(s) to stock 0 and NotLive, "
            "and marked them Discontinued TRUE."
        )
    else:
        message = (
            f"Marked {deleted} product(s) Discontinued TRUE with stock 0 "
            "on the MyDeal inventory template."
        )
    return preview, deleted, message
