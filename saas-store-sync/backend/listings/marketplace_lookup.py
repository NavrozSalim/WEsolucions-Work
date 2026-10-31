"""Live marketplace SKU lookup for managed stores (Lasoo + Reverb + Bunnings + MyDeal + Temu)."""
from __future__ import annotations

import logging
import time

from django.db.models import Q

from store_adapters import get_adapter
from store_adapters.reverb_adapter import ReverbAPIError
from stores.credentials import marketplace_kind

from .errors import MarketplaceError
from .lasoo.client import LasooClient
from .lasoo.connect_search import search_variant
from .lasoo.response import lookup_message
from .models import StoreListing

logger = logging.getLogger("listings")

LOOKUP_KINDS = ("lasoo", "reverb", "bunnings", "mydeal", "temu")


def _normalize_reverb_hit(row: dict) -> dict:
    state = row.get("state")
    if isinstance(state, dict):
        status = str(state.get("slug") or state.get("description") or state.get("name") or "").strip()
    else:
        status = str(state or "").strip()
    sku = str(row.get("sku") or "").strip()
    lid = row.get("id") or row.get("uuid")
    links = row.get("_links") if isinstance(row.get("_links"), dict) else {}
    web = links.get("web") if isinstance(links.get("web"), dict) else {}
    self_link = links.get("self") if isinstance(links.get("self"), dict) else {}
    url = (
        str(web.get("href") or "").strip()
        or str(self_link.get("href") or "").strip()
        or None
    )
    return {
        "product_key": str(lid or "").strip() or sku,
        "variant_key": sku,
        "sku": sku,
        "title": str(row.get("title") or "").strip(),
        "status": status or "unknown",
        "created_at": row.get("created_at") or row.get("createdAt") or None,
        "updated_at": row.get("updated_at") or row.get("updatedAt") or None,
        "published_at": row.get("published_at") or row.get("publishedAt") or None,
        "marketplace_id": str(lid) if lid is not None else None,
        "url": url,
    }


def _local_listing_summary(store, sku: str) -> dict | None:
    listing = (
        StoreListing.objects.filter(store=store)
        .filter(Q(sku=sku) | Q(external_variant_key=sku) | Q(external_product_key=sku))
        .order_by("-updated_at")
        .first()
    )
    if not listing:
        return None
    return {
        "id": str(listing.id),
        "sku": listing.sku,
        "variant_key": listing.external_variant_key,
        "product_key": listing.external_product_key,
        "title": listing.title,
        "status": listing.status,
        "action": listing.action,
        "created_at": listing.created_at.isoformat() if listing.created_at else None,
        "updated_at": listing.updated_at.isoformat() if listing.updated_at else None,
    }


def _lookup_lasoo(store, sku: str) -> dict:
    listing = (
        StoreListing.objects.filter(store=store)
        .filter(Q(sku=sku) | Q(external_variant_key=sku) | Q(external_product_key=sku))
        .first()
    )
    product_key = (
        (listing.external_product_key if listing else "") or sku
    ).strip()
    variant_key = (
        (listing.external_variant_key if listing else "") or sku
    ).strip()

    client = LasooClient(store)
    searched = search_variant(
        client,
        product_key=product_key,
        variant_key=variant_key,
        sku=sku,
    )
    if not searched.get("ok"):
        raise MarketplaceError(
            searched.get("message") or "Could not search Lasoo for this SKU."
        )

    hits = [searched["hit"]] if searched.get("found") and searched.get("hit") else []
    advertised = searched.get("advertised")
    mapping_errors = list(searched.get("mapping_errors") or [])
    return {
        "ok": True,
        "found": bool(searched.get("found")),
        "advertised": advertised,
        "mapping_errors": mapping_errors,
        "marketplace": "lasoo",
        "environment": client.environment,
        "query": searched.get("query") or {
            "sku": sku, "product_key": product_key, "variant_key": variant_key,
        },
        "message": searched.get("message") or lookup_message(
            found=bool(searched.get("found")),
            advertised=advertised,
            mapping_errors=mapping_errors,
        ),
        "results": hits[:10],
        "local_listing": _local_listing_summary(store, sku),
    }


def _lookup_reverb(store, sku: str) -> dict:
    if not (getattr(store, "api_token", None) or "").strip():
        raise MarketplaceError(
            "No Reverb API token configured for this store. Add it in store settings."
        )
    adapter = get_adapter(store)
    try:
        raw_listings = adapter.find_listings_by_sku(sku)
    except ReverbAPIError as exc:
        raise MarketplaceError(str(exc) or "Reverb listing lookup failed.") from exc

    hits = [_normalize_reverb_hit(r) for r in raw_listings if isinstance(r, dict)]
    found = bool(hits)
    return {
        "ok": True,
        "found": found,
        "marketplace": "reverb",
        "environment": "production",
        "query": {"sku": sku},
        "message": (
            "Found on Reverb."
            if found
            else "Not found on Reverb for this SKU (checked live + draft)."
        ),
        "results": hits[:10],
        "local_listing": _local_listing_summary(store, sku),
    }


def _lookup_bunnings(store, sku: str) -> dict:
    from .bunnings import products as bunnings_products

    try:
        offer = bunnings_products.lookup_offer(store, sku)
    except MarketplaceError as exc:
        raise MarketplaceError(str(exc) or "Bunnings offer lookup failed.") from exc

    found = bool(offer)
    hits = []
    if offer:
        shop_sku = str(offer.get("shop_sku") or offer.get("sku") or sku).strip()
        hits.append({
            "product_key": str(offer.get("product_id") or shop_sku).strip(),
            "variant_key": shop_sku,
            "sku": shop_sku,
            "title": str(offer.get("product_title") or offer.get("description") or "").strip(),
            "status": str(offer.get("active") if offer.get("active") is not None else offer.get("state_code") or "unknown"),
            "created_at": offer.get("date_created"),
            "updated_at": offer.get("last_updated_date") or offer.get("date_created"),
            "published_at": None,
            "marketplace_id": str(offer.get("offer_id") or offer.get("id") or shop_sku),
            "url": None,
        })
    return {
        "ok": True,
        "found": found,
        "marketplace": "bunnings",
        "environment": getattr(store, "bunnings_environment", None) or "production",
        "query": {"sku": sku},
        "message": "Found on Bunnings." if found else "Not found on Bunnings for this SKU.",
        "results": hits[:10],
        "local_listing": _local_listing_summary(store, sku),
    }


def _norm_id(value) -> str:
    return str(value or "").strip()


def _mydeal_groups(payload) -> list[dict]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    inner = payload.get("Data")
    if inner is None:
        inner = payload.get("data")
    if isinstance(inner, list):
        return [
            row
            for row in inner
            if isinstance(row, dict)
            and (row.get("ProductSKU") or row.get("BuyableProducts") or row.get("ExternalProductId") or row.get("ExternalProductID"))
        ]
    if isinstance(inner, dict):
        nested = inner.get("Items") or inner.get("items")
        if isinstance(nested, list):
            return [
                row
                for row in nested
                if isinstance(row, dict)
                and (row.get("ProductSKU") or row.get("BuyableProducts"))
            ]
        if inner.get("ProductSKU") or inner.get("BuyableProducts") or inner.get("ExternalProductId") or inner.get("ExternalProductID"):
            return [inner]
        return []
    if payload.get("ProductSKU") or payload.get("BuyableProducts") or payload.get("ExternalProductId"):
        return [payload]
    return []


def _mydeal_is_not_found(result) -> bool:
    if getattr(result, "status", 0) == 404:
        return True
    parts = [getattr(result, "message", "") or "", getattr(result, "response_status", "") or ""]
    data = getattr(result, "data", None)
    if isinstance(data, dict):
        parts.append(str(data.get("ResponseStatus") or ""))
        errors = data.get("Errors") or data.get("errors") or []
        if isinstance(errors, list):
            for err in errors:
                if isinstance(err, dict):
                    parts.append(str(err.get("ID") or err.get("Id") or err.get("Code") or ""))
                    parts.append(str(err.get("Message") or err.get("message") or ""))
                else:
                    parts.append(str(err))
    blob = " ".join(parts).lower()
    compact = blob.replace(" ", "")
    return "productnotfound" in compact or "not found" in blob or "5000" in blob


def _ci_get(row: dict, *names: str) -> str:
    """Read a MyDeal field regardless of ID/Id/SKU casing."""
    if not isinstance(row, dict):
        return ""
    wanted = {name.casefold() for name in names}
    for key, value in row.items():
        if str(key).casefold() in wanted:
            return _norm_id(value)
    return ""


def _mydeal_buyable_ids(buyable: dict) -> set[str]:
    return {
        value
        for value in (
            _ci_get(buyable, "SKU"),
            _ci_get(buyable, "ExternalBuyableProductId", "ExternalBuyableProductID"),
        )
        if value
    }


def _mydeal_group_ids(group: dict) -> set[str]:
    return {
        value
        for value in (
            _ci_get(group, "ProductSKU"),
            _ci_get(group, "ExternalProductId", "ExternalProductID"),
        )
        if value
    }


def _mydeal_group_key_candidates(sku: str) -> list[str]:
    """Parent ids implied by a variant code.

    GET /products/{idorsku} only accepts the product-group key
    (ExternalProductID or ProductSKU). A Shopify-style variant is
    ``{productId}-{variantId}``; the group key is the product id.
    """
    text = _norm_id(sku)
    if "-" not in text:
        return []
    left, right = text.split("-", 1)
    left = left.strip()
    right = right.strip()
    if left.isdigit() and right.isdigit() and len(left) >= 8 and left != text:
        return [left]
    return []


def _mydeal_id_match(ids: set[str], needles: list[str]) -> bool:
    wanted = {_norm_id(item) for item in needles if _norm_id(item)}
    if not wanted or not ids:
        return False
    if ids & wanted:
        return True
    folded = {item.casefold() for item in ids}
    return any(item.casefold() in folded for item in wanted)


def _mydeal_pick_buyable(group: dict, needles: list[str]) -> dict | None:
    buyables = [row for row in (group.get("BuyableProducts") or []) if isinstance(row, dict)]
    for buyable in buyables:
        if _mydeal_id_match(_mydeal_buyable_ids(buyable), needles):
            return buyable
    if buyables and _mydeal_id_match(_mydeal_group_ids(group), needles):
        return buyables[0]
    return buyables[0] if buyables else None


def _mydeal_hit(group: dict, buyable: dict | None, sku: str) -> dict:
    status = _ci_get(buyable or {}, "ListingStatus") or "unknown"
    advertised = status.lower() == "live"
    product_key = (
        _ci_get(group, "ProductSKU")
        or _ci_get(group, "ExternalProductId", "ExternalProductID")
        or sku
    )
    variant_key = (
        _ci_get(buyable or {}, "SKU")
        or _ci_get(buyable or {}, "ExternalBuyableProductId", "ExternalBuyableProductID")
        or sku
    )
    return {
        "product_key": product_key,
        "variant_key": variant_key,
        "sku": variant_key,
        "title": _norm_id(group.get("Title")),
        "status": status,
        "advertised": advertised,
        "approved": (buyable or {}).get("Approved"),
        "created_at": None,
        "updated_at": None,
        "published_at": None,
        "marketplace_id": variant_key or product_key,
        "url": None,
    }


_MYDEAL_LIST_FIELDS = (
    "ExternalProductId,ProductSKU,Title,"
    "BuyableProducts(ExternalBuyableProductId,SKU,ListingStatus)"
)
_MYDEAL_LIST_STATUSES = ("Live", "NotLive")
_MYDEAL_CATALOG_INDEX: dict[tuple[str, str], tuple[float, dict[str, dict]]] = {}
_MYDEAL_CATALOG_INDEX_TTL = 120.0


def _mydeal_related_keys(store, sku: str) -> list[str]:
    """Other ids for this SKU from the inventory template or the catalog.

    A portal row often stores the buyable SKU in SKU and the parent id in
    ExternalID. The catalog may store the parent separately from the child.
    """
    from catalog.models import MydealTemplateRow, ProductMapping

    wanted = _norm_id(sku).casefold()
    if not wanted:
        return []
    found: list[str] = []
    seen = {wanted}

    def add(raw) -> None:
        text = _norm_id(raw)
        if not text:
            return
        key = text.casefold()
        if key in seen:
            return
        seen.add(key)
        found.append(text)

    rows = (
        MydealTemplateRow.objects.filter(store=store, kind=MydealTemplateRow.Kind.INVENTORY)
        .filter(Q(sku__iexact=sku) | Q(external_id__iexact=sku))
        .only("sku", "external_id")[:20]
    )
    for row in rows:
        add(row.external_id)
        add(row.sku)

    mappings = (
        ProductMapping.objects.filter(store=store)
        .filter(
            Q(marketplace_child_sku__iexact=sku)
            | Q(marketplace_parent_sku__iexact=sku)
            | Q(product__vendor_sku__iexact=sku)
        )
        .select_related("product")[:20]
    )
    for pm in mappings:
        add(pm.marketplace_parent_sku)
        add(pm.marketplace_child_sku)
        product = getattr(pm, "product", None)
        if product is not None:
            add(getattr(product, "vendor_sku", None))
    return found


def _mydeal_index_group(index: dict[str, dict], group: dict) -> None:
    ids = set(_mydeal_group_ids(group))
    for buyable in group.get("BuyableProducts") or []:
        if isinstance(buyable, dict):
            ids.update(_mydeal_buyable_ids(buyable))
    for value in ids:
        index.setdefault(value.casefold(), group)


def _mydeal_build_catalog_index(client) -> dict[str, dict] | None:
    """Map parent, external, and buyable ids to a product group.

    Returns None when this client cannot list products (test doubles).
    """
    from .mydeal.client import MyDealResult

    list_products = getattr(client, "list_products", None)
    if not callable(list_products):
        return None
    index: dict[str, dict] = {}
    limit = 250
    saw_result = False
    for listing_status in _MYDEAL_LIST_STATUSES:
        page = 1
        while page <= 40:
            result = list_products(
                page=page,
                limit=limit,
                listing_status=listing_status,
                fields=_MYDEAL_LIST_FIELDS,
            )
            if not isinstance(result, MyDealResult):
                return None if not saw_result else index
            saw_result = True
            if not result.ok:
                if _mydeal_is_not_found(result):
                    break
                raise MarketplaceError(result.message or "Could not list MyDeal products.")
            groups = _mydeal_groups(result.data)
            if not groups:
                break
            for group in groups:
                _mydeal_index_group(index, group)
            if len(groups) < limit:
                break
            page += 1
    return index if saw_result else None


def _mydeal_catalog_index(client, store) -> dict[str, dict]:
    cache_key = (str(getattr(store, "id", "") or ""), str(getattr(client, "environment", "") or ""))
    now = time.monotonic()
    cached = _MYDEAL_CATALOG_INDEX.get(cache_key)
    if cached and now - cached[0] < _MYDEAL_CATALOG_INDEX_TTL:
        return cached[1]
    index = _mydeal_build_catalog_index(client)
    if index is None:
        return {}
    _MYDEAL_CATALOG_INDEX[cache_key] = (now, index)
    return index


def _mydeal_fetch_group(client, key: str, *, by: str):
    result = client.get_product(key, by=by)
    if getattr(result, "ok", False):
        groups = _mydeal_groups(getattr(result, "data", None))
        return groups[0] if groups else None
    if _mydeal_is_not_found(result):
        return None
    raise MarketplaceError(getattr(result, "message", None) or "Could not search MyDeal for this SKU.")


def _mydeal_message(*, found: bool, advertised, status: str) -> str:
    if not found:
        return "Not found on MyDeal for this SKU."
    if advertised is True:
        return "Found on MyDeal and live."
    if (status or "").lower() == "pending":
        return (
            "Found on MyDeal but still pending WMP review — "
            "not live on the website yet."
        )
    return "Found on MyDeal in the seller catalog — not live on the website."


def _lookup_mydeal(store, sku: str) -> dict:
    from .mydeal.client import MyDealClient
    from .mydeal.products import listing_sku, parent_product_id

    listing = (
        StoreListing.objects.filter(store=store)
        .filter(Q(sku=sku) | Q(external_variant_key=sku) | Q(external_product_key=sku))
        .first()
    )
    parent_key = parent_product_id(listing) if listing else sku
    variant_key = listing_sku(listing) if listing else sku
    keys = []
    for key in (parent_key, sku, variant_key):
        text = _norm_id(key)
        if text and text not in keys:
            keys.append(text)

    client = MyDealClient(store)

    attempts: list[tuple[str, str]] = []
    seen_attempts: set[tuple[str, str]] = set()

    def _queue(key: str, by: str) -> None:
        text = _norm_id(key)
        if not text:
            return
        token = (text, by)
        if token in seen_attempts:
            return
        seen_attempts.add(token)
        attempts.append(token)

    for key in keys:
        _queue(key, "sku")
        _queue(key, "id")
        # A variant code is not a product-group key. Ask for the parent id
        # with both ?sku= and ?id=, matching the store's Product Key Identifier.
        for parent in _mydeal_group_key_candidates(key):
            _queue(parent, "sku")
            _queue(parent, "id")
    for key in _mydeal_related_keys(store, sku):
        _queue(key, "sku")
        _queue(key, "id")

    group = None
    for key, by in attempts:
        group = _mydeal_fetch_group(client, key, by=by)
        if group is not None:
            break
    if group is None:
        # Buyable SKUs are not accepted by GET /products/{sku}. The seller
        # catalog list includes those variant codes.
        folded = {_norm_id(key).casefold() for key, _by in attempts if _norm_id(key)}
        index = _mydeal_catalog_index(client, store)
        for needle in folded:
            group = index.get(needle)
            if group is not None:
                break

    found = group is not None
    buyable = _mydeal_pick_buyable(group, [sku, variant_key, parent_key]) if group else None
    hit = _mydeal_hit(group, buyable, sku) if group else None
    advertised = hit.get("advertised") if hit else None
    status = (hit or {}).get("status") or ""
    return {
        "ok": True,
        "found": found,
        "advertised": advertised,
        "marketplace": "mydeal",
        "environment": client.environment,
        "query": {
            "sku": sku,
            "product_key": parent_key,
            "variant_key": variant_key,
        },
        "message": _mydeal_message(found=found, advertised=advertised, status=status),
        "results": [hit] if hit else [],
        "local_listing": _local_listing_summary(store, sku),
    }


def _lookup_temu(store, sku: str) -> dict:
    """Match a Hub SKU against the Temu outGoodsSn / outSkuSn on the mall."""
    from .temu import products as temu_products

    found = temu_products.lookup_sku(store, sku)
    hit = None
    if found:
        hit = {
            "product_key": found.get("goods_id") or sku,
            "variant_key": found.get("out_sku_sn") or sku,
            "sku": found.get("out_sku_sn") or sku,
            "title": found.get("goods_name") or "",
            "status": found.get("status") or "unknown",
            "created_at": None,
            "updated_at": None,
            "published_at": None,
            "marketplace_id": found.get("sku_id") or found.get("goods_id") or None,
            "url": None,
        }
    return {
        "ok": True,
        "found": bool(found),
        "advertised": bool(found),
        "marketplace": "temu",
        "environment": "production",
        "query": {"sku": sku},
        "message": (
            f'SKU "{sku}" found on Temu.'
            if found
            else f'SKU "{sku}" is not in this Temu mall.'
        ),
        "results": [hit] if hit else [],
        "local_listing": _local_listing_summary(store, sku),
    }


def lookup_sku(store, sku: str) -> dict:
    """Search the store's marketplace for a SKU / variant key."""
    text = (sku or "").strip()
    if not text:
        raise MarketplaceError("SKU is required.")
    if getattr(store, "management_mode", None) != "full_store":
        raise MarketplaceError("Marketplace SKU check is only available for managed stores.")

    kind = marketplace_kind(store.marketplace)
    if kind == "lasoo":
        return _lookup_lasoo(store, text)
    if kind == "reverb":
        return _lookup_reverb(store, text)
    if kind == "bunnings":
        return _lookup_bunnings(store, text)
    if kind == "mydeal":
        return _lookup_mydeal(store, text)
    if kind == "temu":
        return _lookup_temu(store, text)
    raise MarketplaceError(
        f'Marketplace SKU check is not supported for "{kind or "this marketplace"}" yet.'
    )


BULK_MAX_SKUS = 20  # per HTTP request — keeps lookups under gunicorn timeout
BULK_TOTAL_MAX_SKUS = 2000  # max SKUs accepted for parse / overall client-driven run


def parse_sku_list(raw) -> list[str]:
    """Normalize a list/string of SKUs (dedupe, preserve order)."""
    items: list[str] = []
    if isinstance(raw, str):
        # Split on newlines, commas, or semicolons.
        for part in raw.replace(";", "\n").replace(",", "\n").splitlines():
            text = part.strip()
            if text:
                items.append(text)
    elif isinstance(raw, (list, tuple)):
        for part in raw:
            text = str(part or "").strip()
            if text:
                items.append(text)
    seen: set[str] = set()
    out: list[str] = []
    for sku in items:
        key = sku.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(sku)
    return out


def store_listing_skus(store) -> list[str]:
    """SKUs for every managed listing on this store (for reconcile-all)."""
    pairs = StoreListing.objects.filter(store=store).order_by("sku").values_list(
        "sku", "external_variant_key",
    )
    return parse_sku_list([sku or variant for sku, variant in pairs])


def parse_skus_from_file(content: bytes, filename: str = "") -> list[str]:
    """Parse SKUs from .txt / .csv / .xlsx (SKU column or first column / one per line)."""
    import csv
    from io import BytesIO, StringIO

    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        import openpyxl

        wb = openpyxl.load_workbook(BytesIO(content), data_only=True, read_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []
        headers = [str(c or "").strip().lower() for c in rows[0]]
        sku_headers = {"sku", "variant key", "variant_key", "external variant key"}
        sku_idx = 0
        for i, h in enumerate(headers):
            if h in sku_headers:
                sku_idx = i
                break
        start = 1 if any(h in sku_headers for h in headers) else 0
        values = []
        for row in rows[start:]:
            if not row or sku_idx >= len(row):
                continue
            val = row[sku_idx]
            if val is None:
                continue
            text = str(val).strip()
            if text:
                values.append(text)
        return parse_sku_list(values)

    text = content.decode("utf-8-sig", errors="replace")
    lines = text.lstrip().splitlines()
    if lines and "," in lines[0]:
        rows = list(csv.reader(StringIO(text)))
        if not rows:
            return []
        headers = [str(c or "").strip().lower() for c in rows[0]]
        sku_headers = {"sku", "variant key", "variant_key"}
        if any(h in sku_headers for h in headers):
            idx = next(i for i, h in enumerate(headers) if h in sku_headers)
            values = [
                str(row[idx]).strip()
                for row in rows[1:]
                if idx < len(row) and str(row[idx]).strip()
            ]
            return parse_sku_list(values)
    return parse_sku_list(text)


def _row_from_lookup(sku: str, result: dict | None, error: str = "") -> dict:
    hit = None
    if result and isinstance(result.get("results"), list) and result["results"]:
        hit = result["results"][0]
    local = (result or {}).get("local_listing") if result else None
    found = bool(result and result.get("found"))
    advertised = None
    if hit and "advertised" in hit:
        advertised = hit.get("advertised")
    elif result:
        advertised = result.get("advertised")
    if advertised is True:
        advertised_label = "Yes"
    elif advertised is False:
        advertised_label = "No"
    else:
        advertised_label = ""
    mapping_errors = []
    if hit and hit.get("mapping_errors"):
        mapping_errors = hit.get("mapping_errors") or []
    elif result:
        mapping_errors = result.get("mapping_errors") or []
    return {
        "sku": sku,
        "found": "Yes" if found else "No",
        "advertised": advertised_label,
        "marketplace_status": (hit or {}).get("status") or "",
        "mapping_errors": "; ".join(mapping_errors) if mapping_errors else "",
        "created_at": (hit or {}).get("created_at") or (hit or {}).get("published_at") or "",
        "published_at": (hit or {}).get("published_at") or "",
        "title": (hit or {}).get("title") or "",
        "marketplace_id": (hit or {}).get("marketplace_id") or "",
        "url": (hit or {}).get("url") or "",
        "local_status": (local or {}).get("status") or "",
        "local_action": (local or {}).get("action") or "",
        "local_title": (local or {}).get("title") or "",
        "local_created_at": (local or {}).get("created_at") or "",
        "message": error or ((result or {}).get("message") or ""),
    }


def lookup_skus_bulk(store, skus: list[str]) -> dict:
    """Look up many SKUs; returns summary + per-SKU rows for CSV/UI."""
    if getattr(store, "management_mode", None) != "full_store":
        raise MarketplaceError("Marketplace SKU check is only available for managed stores.")
    cleaned = parse_sku_list(skus)
    if not cleaned:
        raise MarketplaceError("Provide at least one SKU.")
    if len(cleaned) > BULK_MAX_SKUS:
        raise MarketplaceError(
            f'Too many SKUs in one batch ({len(cleaned)}). '
            f'Maximum is {BULK_MAX_SKUS} per request — the UI sends batches automatically.'
        )

    kind = marketplace_kind(store.marketplace)
    if kind not in LOOKUP_KINDS:
        raise MarketplaceError(
            f'Marketplace SKU check is not supported for "{kind or "this marketplace"}" yet.'
        )

    rows = []
    found_count = 0
    error_count = 0
    marketplace = kind
    environment = ""
    for sku in cleaned:
        try:
            result = lookup_sku(store, sku)
            marketplace = result.get("marketplace") or marketplace
            environment = result.get("environment") or environment
            row = _row_from_lookup(sku, result)
            if result.get("found"):
                found_count += 1
            rows.append(row)
        except MarketplaceError as exc:
            error_count += 1
            rows.append(_row_from_lookup(sku, None, error=str(exc)))
            rows[-1]["found"] = "Error"
        except Exception as exc:  # noqa: BLE001
            logger.exception("Bulk marketplace lookup failed for sku=%s", sku)
            error_count += 1
            rows.append(_row_from_lookup(sku, None, error=str(exc)[:300]))
            rows[-1]["found"] = "Error"

    not_found = sum(1 for r in rows if r["found"] == "No")
    return {
        "ok": True,
        "marketplace": marketplace,
        "environment": environment,
        "total": len(rows),
        "found": found_count,
        "not_found": not_found,
        "errors": error_count,
        "rows": rows,
        "message": (
            f"Checked {len(rows)} SKU(s): {found_count} found, {not_found} not found"
            + (f", {error_count} error(s)" if error_count else "")
            + "."
        ),
    }


CSV_COLUMNS = [
    ("sku", "SKU"),
    ("found", "Found"),
    ("advertised", "Advertised / Live"),
    ("marketplace_status", "Marketplace Status"),
    ("mapping_errors", "Mapping Errors"),
    ("created_at", "Created At"),
    ("published_at", "Published At"),
    ("title", "Title"),
    ("marketplace_id", "Marketplace ID"),
    ("url", "URL"),
    ("local_status", "Hub Status"),
    ("local_action", "Hub Action"),
    ("local_title", "Hub Title"),
    ("local_created_at", "Hub Created At"),
    ("message", "Message"),
]


def build_lookup_csv(bulk_result: dict) -> bytes:
    """Serialize bulk lookup rows to CSV bytes."""
    import csv
    from io import StringIO

    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow([header for _, header in CSV_COLUMNS])
    for row in bulk_result.get("rows") or []:
        writer.writerow([row.get(key, "") for key, _ in CSV_COLUMNS])
    return buf.getvalue().encode("utf-8-sig")


def run_marketplace_lookup_job(store_id, skus: list[str]) -> dict:
    """Process a queued bulk lookup (Celery / background thread). Checks cancel between SKUs."""
    from stores.models import Store

    from . import marketplace_lookup_progress as prog

    try:
        store = Store.objects.select_related("marketplace", "user").get(pk=store_id)
    except Store.DoesNotExist:
        prog.finish_lookup_progress(
            store_id,
            status="error",
            message="Store not found.",
        )
        return {"ok": False, "error": "not_found"}

    cleaned = parse_sku_list(skus)
    prog.set_lookup_progress(
        store.id,
        active=True,
        status="running",
        message=f"Checking 0 of {len(cleaned)}…",
    )

    for sku in cleaned:
        if prog.is_lookup_cancel_requested(store.id):
            return prog.finish_lookup_progress(store.id, status="cancelled")
        try:
            result = lookup_sku(store, sku)
            row = _row_from_lookup(sku, result)
            prog.append_lookup_row(
                store.id,
                row,
                marketplace=result.get("marketplace") or "",
                environment=result.get("environment") or "",
            )
        except MarketplaceError as exc:
            row = _row_from_lookup(sku, None, error=str(exc))
            row["found"] = "Error"
            prog.append_lookup_row(store.id, row)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Marketplace lookup job failed for sku=%s store=%s", sku, store_id)
            row = _row_from_lookup(sku, None, error=str(exc)[:300])
            row["found"] = "Error"
            prog.append_lookup_row(store.id, row)

    if prog.is_lookup_cancel_requested(store.id):
        return prog.finish_lookup_progress(store.id, status="cancelled")
    return prog.finish_lookup_progress(store.id, status="done")


def start_marketplace_lookup_async(store, skus) -> dict:
    """Start a background marketplace SKU check (survives leaving the page).

    Uses a daemon thread (same pattern as managed listing scrape). Progress and
    result rows live in cache until the user starts a new check.
    """
    import threading

    from . import marketplace_lookup_progress as prog

    if getattr(store, "management_mode", None) != "full_store":
        raise MarketplaceError("Marketplace SKU check is only available for managed stores.")

    cleaned = parse_sku_list(skus)
    if not cleaned:
        raise MarketplaceError("Provide at least one SKU.")
    if len(cleaned) > BULK_TOTAL_MAX_SKUS:
        raise MarketplaceError(
            f"Too many SKUs ({len(cleaned)}). Maximum is {BULK_TOTAL_MAX_SKUS} per run."
        )

    kind = marketplace_kind(store.marketplace)
    if kind not in LOOKUP_KINDS:
        raise MarketplaceError(
            f'Marketplace SKU check is not supported for "{kind or "this marketplace"}" yet.'
        )

    existing = prog.get_lookup_progress(store.id)
    if existing.get("active"):
        return {
            "started": False,
            "already_running": True,
            "ok": True,
            "message": "A marketplace check is already running. You can leave this page — progress continues.",
            **prog.public_lookup_progress(store.id),
        }

    prog.begin_lookup_progress(
        store.id,
        skus=cleaned,
        message=f"Starting check for {len(cleaned)} SKU(s)…",
    )

    store_id = str(store.id)
    sku_list = list(cleaned)

    def _run():
        try:
            run_marketplace_lookup_job(store_id, sku_list)
        except Exception:  # noqa: BLE001
            logger.exception("Marketplace lookup thread failed store=%s", store_id)
            prog.finish_lookup_progress(
                store_id,
                status="error",
                message="Marketplace check failed unexpectedly.",
            )

    threading.Thread(
        target=_run,
        daemon=True,
        name=f"marketplace-lookup-{store_id}",
    ).start()

    # Progress and CSV results live in cache until the next run.

    return {
        "started": True,
        "async": True,
        "via": "thread",
        "ok": True,
        "total": len(sku_list),
        "processed": 0,
        "message": (
            f"Started check for {len(sku_list)} SKU(s). "
            "You can leave this page — results stay available until you start a new check."
        ),
        **prog.public_lookup_progress(store.id),
    }
