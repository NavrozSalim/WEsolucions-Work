"""Resolve Vendor Name / Marketplace Name / Store Name from listing templates."""
from __future__ import annotations

from stores.models import Store
from vendor.models import Vendor

# Human labels → preferred vendor codes (matched against Vendor.code / .name).
VENDOR_NAME_ALIASES: dict[str, str] = {
    "nora": "noraau",
    "nora inventory": "noraau",
    "nora au": "noraau",
    "noraau": "noraau",
    "amazon": "amazonus",
    "amazon us": "amazonus",
    "amazon.com": "amazonus",
    "amazonus": "amazonus",
    "amazon au": "amazonau",
    "amazon.com.au": "amazonau",
    "amazonau": "amazonau",
    "vevor": "vevor",
    "vevor au": "vevorau",
    "vevorau": "vevorau",
    "ebay": "ebay",
    "ebay us": "ebay",
    "ebay au": "ebayau",
    "ebayau": "ebayau",
    "costco au": "costcoau",
    "costcoau": "costcoau",
    "heb": "heb",
    "aliexpress": "aliexpress",
}

# Marketplace labels users may type (including the common "Lesso" misspelling).
MARKETPLACE_ALIASES: dict[str, str] = {
    "lasoo": "lasoo",
    "lesso": "lasoo",
    "lasso": "lasoo",
    "reverb": "reverb",
}


def _norm(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def resolve_vendor_code(vendor_name: str) -> str | None:
    """Return a canonical vendor code for a template Vendor Name, or None if blank."""
    label = _norm(vendor_name)
    if not label:
        return None
    if label in VENDOR_NAME_ALIASES:
        preferred = VENDOR_NAME_ALIASES[label]
        # Prefer DB code when present; otherwise keep the alias target.
        hit = (
            Vendor.objects.filter(code__iexact=preferred).first()
            or Vendor.objects.filter(name__iexact=vendor_name.strip()).first()
            or Vendor.objects.filter(name__iexact=label).first()
        )
        return (hit.code if hit else preferred).strip().lower()

    hit = (
        Vendor.objects.filter(code__iexact=label.replace(" ", "")).first()
        or Vendor.objects.filter(code__iexact=label).first()
        or Vendor.objects.filter(name__iexact=vendor_name.strip()).first()
        or Vendor.objects.filter(name__icontains=vendor_name.strip()).first()
    )
    if hit:
        return (hit.code or "").strip().lower() or None
    return None


def marketplace_code_from_label(label: str) -> str | None:
    key = _norm(label)
    if not key:
        return None
    if key in MARKETPLACE_ALIASES:
        return MARKETPLACE_ALIASES[key]
    # Direct code / name match via alias keys only for known managed marketplaces.
    compact = key.replace(" ", "")
    return MARKETPLACE_ALIASES.get(compact)


def marketplace_matches_store(store: Store, marketplace_name: str) -> bool:
    code = marketplace_code_from_label(marketplace_name)
    if not code:
        # Unknown label — compare loosely to store marketplace name/code.
        mp = getattr(store, "marketplace", None)
        store_code = _norm(getattr(mp, "code", "") or "")
        store_name = _norm(getattr(mp, "name", "") or "")
        label = _norm(marketplace_name)
        return bool(label) and label in (store_code, store_name)
    mp = getattr(store, "marketplace", None)
    store_code = _norm(getattr(mp, "code", "") or "")
    return store_code == code


def resolve_row_store(user, default_store: Store, row: dict) -> tuple[Store, list[str]]:
    """Resolve which store a template row targets.

    Blank Store Name / Marketplace Name → use the UI-selected ``default_store``.
    When Store Name is set, look up that store for the user (optionally filtered
    by Marketplace Name). Marketplace Name alone must match ``default_store``.
    """
    errors: list[str] = []
    store_name = (row.get("store_name") or "").strip()
    marketplace_name = (row.get("marketplace_name") or "").strip()

    if store_name:
        qs = Store.objects.filter(user=user, name__iexact=store_name).select_related(
            "marketplace"
        )
        if marketplace_name:
            wanted = marketplace_code_from_label(marketplace_name)
            if wanted:
                qs = qs.filter(marketplace__code__iexact=wanted)
            else:
                qs = qs.filter(marketplace__name__iexact=marketplace_name)
        matches = list(qs[:5])
        if not matches:
            tip = f' and marketplace "{marketplace_name}"' if marketplace_name else ""
            errors.append(f'No store named "{store_name}"{tip} was found for your account.')
            return default_store, errors
        if len(matches) > 1 and not marketplace_name:
            errors.append(
                f'Multiple stores named "{store_name}". '
                "Set Marketplace Name (Lasoo or Reverb) to pick one."
            )
            return default_store, errors
        target = matches[0]
        if marketplace_name and not marketplace_matches_store(target, marketplace_name):
            errors.append(
                f'Marketplace Name "{marketplace_name}" does not match store '
                f'"{target.name}" ({getattr(target.marketplace, "name", "")}).'
            )
        return target, errors

    if marketplace_name and not marketplace_matches_store(default_store, marketplace_name):
        mp_label = getattr(getattr(default_store, "marketplace", None), "name", "") or ""
        errors.append(
            f'Marketplace Name "{marketplace_name}" does not match this store '
            f'({default_store.name} / {mp_label}).'
        )
    return default_store, errors


def validate_vendor_name_row(row: dict) -> tuple[str, list[str]]:
    """Resolve Vendor Name and check URL / Vendor ID consistency.

    Returns (source_vendor_code, errors). Blank Vendor Name is allowed.
    """
    errors: list[str] = []
    raw_name = (row.get("vendor_name") or "").strip()
    if not raw_name:
        return "", []

    code = resolve_vendor_code(raw_name)
    if not code:
        errors.append(
            f'Unknown Vendor Name "{raw_name}". '
            "Use a known source such as Nora Inventory, Amazon US, or Amazon AU."
        )
        return "", errors

    url = (row.get("vendor_url") or "").strip().lower()
    vendor_id = (row.get("vendor_id") or "").strip()

    if code in ("noraau", "nora", "nora_au", "nora-au") or "nora" in code:
        if not vendor_id:
            errors.append(
                'Vendor ID is required when Vendor Name is Nora Inventory '
                "(supplier barcode)."
            )
    elif code in ("amazonus", "amazon") or code.startswith("amazon") and "au" not in code:
        if url and "amazon." in url and "amazon.com.au" in url:
            errors.append(
                'Vendor Name is Amazon US but Vendor URL looks like Amazon AU '
                "(amazon.com.au)."
            )
        elif url and "amazon." not in url and url.startswith("http"):
            errors.append(
                f'Vendor Name is Amazon US but Vendor URL does not look like Amazon: {url[:80]}'
            )
    elif "amazonau" in code or code in ("amazon_au", "amazon-au"):
        if url and "amazon." in url and "amazon.com.au" not in url:
            errors.append(
                "Vendor Name is Amazon AU but Vendor URL is not an amazon.com.au link."
            )
        elif url and "amazon." not in url and url.startswith("http"):
            errors.append(
                f'Vendor Name is Amazon AU but Vendor URL does not look like Amazon: {url[:80]}'
            )

    return code, errors
