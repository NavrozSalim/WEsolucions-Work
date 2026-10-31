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
# MyDeal refuses a listing-status batch above 100 product groups, and a
# price-and-quantity batch above 250. Stay under both.
_LISTING_BATCH = 80
_QUANTITY_BATCH = 200


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


def _raw_field(row: dict, *names):
    wanted = {name.casefold() for name in names}
    for key, value in row.items():
        if str(key).casefold() in wanted:
            return value
    return None


def _money(value) -> float:
    text = str(value if value is not None else "").strip().replace(",", "")
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _qty(value) -> int:
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


def _flag(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


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
            "price": _money(_raw_field(buyable, "Price")),
            "rrp": _money(_raw_field(buyable, "RRP")),
            "quantity": _qty(_raw_field(buyable, "Quantity")),
            "unlimited": _flag(_raw_field(buyable, "ProductUnlimited")),
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


def _chunks(items: list, size: int):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _product_token(entry: dict) -> tuple[str, str]:
    return (_key(entry.get("external_product_id")), _key(entry.get("sku")))


def _quantity_group(members: list[dict], zero_keys: set[str]) -> dict:
    """One product group with every variant. Only the chosen variants go to 0."""
    buyables = []
    for entry in members:
        price = _money(entry.get("price"))
        rrp = _money(entry.get("rrp")) or price
        zero = bool(_entry_keys(entry) & zero_keys)
        buyables.append({
            "ExternalBuyableProductID": entry.get("external_buyable_id") or entry.get("buyable_sku"),
            "SKU": entry.get("buyable_sku") or entry.get("external_buyable_id"),
            "Price": price,
            "RRP": rrp,
            "Quantity": 0 if zero else _qty(entry.get("quantity")),
            "ProductUnlimited": False if zero else bool(entry.get("unlimited")),
        })
    first = members[0]
    return {
        "ExternalProductID": first.get("external_product_id") or first.get("sku"),
        "ProductSKU": first.get("sku") or first.get("external_product_id"),
        "BuyableProducts": buyables,
    }


def _quantity_groups(entries: list[dict], zero_keys: set[str]) -> list[dict]:
    products: dict[tuple[str, str], list[dict]] = {}
    for entry in entries:
        products.setdefault(_product_token(entry), []).append(entry)
    groups = []
    for members in products.values():
        if any(_entry_keys(member) & zero_keys for member in members):
            groups.append(_quantity_group(members, zero_keys))
    return groups


def delete_unmapped_rows(store, rows: list[dict], progress=None) -> tuple[list[dict], int, str]:
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
    catalog_entries: list[dict] = []
    template_keys: set[str] = set()
    if api:
        catalog_entries = _catalog_entries(store)
        for entry in catalog_entries:
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

    succeeded: list[tuple[dict, list[dict]]] = []
    quantity_error = ""
    if api:
        from .client import MyDealClient

        client = MyDealClient(store)
        done = 0
        for batch in _chunks(pending, _LISTING_BATCH):
            items = [target for _row, targets in batch for target in targets]
            ended = client.end_listings(items)
            if ended.ok:
                succeeded.extend(batch)
            else:
                message = ended.message or "Could not set MyDeal listings to NotLive."
                for row_result, _targets in batch:
                    _fail(row_result, message)
            done += len(batch)
            if progress is not None:
                progress(done, len(succeeded), len(pending) - len(succeeded))
        if succeeded:
            zero_keys: set[str] = set()
            for _row, targets in succeeded:
                for target in targets:
                    zero_keys.update(_entry_keys(target))
            for batch in _chunks(_quantity_groups(catalog_entries, zero_keys), _QUANTITY_BATCH):
                priced = client.update_quantity_price(batch)
                if not priced.ok and not quantity_error:
                    quantity_error = priced.message or "Could not set MyDeal inventory to 0."
    else:
        succeeded = pending

    skus: list[str] = []
    for _row, targets in succeeded:
        for item in targets:
            skus.extend((item.get("buyable_sku"), item.get("external_buyable_id")))
    mark_inventory_discontinued(store, skus, discontinued=True)

    for row_result, _targets in succeeded:
        row_result["imported"] = True
    deleted = len(succeeded)
    if not deleted:
        failed = next((row["errors"][0] for row in preview if row.get("errors")), "")
        return preview, 0, failed[:500]
    if api and quantity_error:
        message = (
            f"Set {deleted} MyDeal product(s) to NotLive and marked them Discontinued TRUE. "
            f"Stock was not changed: {quantity_error}"
        )
    elif api:
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
