"""Auto Purchasing: the vendors a store buys from, each with its purchase account.

Rows live in ``StoreAutoBuyVendor``. ``Store.auto_buy_vendor`` / ``auto_buy_account`` mirror the first row
so code and data from before several vendors per store keep working; a store with no rows but a vendor
in those fields is read as that single vendor.
"""
from __future__ import annotations


def auto_buy_entries(store) -> list[tuple[str, object]]:
    """[(vendor code, PurchaseAccount or None), ...] in the order they were added."""
    rows = list(store.auto_buy_entries.select_related('account').all()) if store.pk else []
    if rows:
        return [(r.vendor, r.account) for r in rows]
    if store.auto_buy_vendor:
        return [(store.auto_buy_vendor, store.auto_buy_account)]
    return []


def auto_buy_vendor_codes(store) -> list[str]:
    return [v for v, _ in auto_buy_entries(store)]


def entry_for_vendor_code(store, vendor_code: str):
    """The (auto-buy vendor, account) whose vendor sells this catalog vendor code, or None."""
    from listings.auto_purchase_check import vendor_matches_auto_buy

    for vendor, account in auto_buy_entries(store):
        if vendor_matches_auto_buy(vendor_code, vendor):
            return vendor, account
    return None


def set_auto_buy_entries(store, entries) -> None:
    """Replace the store's vendor rows with ``entries`` ([(vendor, account or None), ...]) and mirror the first."""
    from .models import StoreAutoBuyVendor

    seen, clean = set(), []
    for vendor, account in entries:
        if vendor and vendor not in seen:
            seen.add(vendor)
            clean.append((vendor, account))
    StoreAutoBuyVendor.objects.filter(store=store).exclude(vendor__in=list(seen)).delete()
    for vendor, account in clean:
        StoreAutoBuyVendor.objects.update_or_create(store=store, vendor=vendor, defaults={'account': account})
    store.auto_buy_vendor = clean[0][0] if clean else ''
    store.auto_buy_account = clean[0][1] if clean else None
    store.save(update_fields=['auto_buy_vendor', 'auto_buy_account'])


def entries_payload(store) -> list[dict]:
    from .models import Store

    labels = dict(Store.AUTO_BUY_VENDOR_CHOICES)
    out = []
    for vendor, a in auto_buy_entries(store):
        out.append({
            'vendor': vendor,
            'label': labels.get(vendor, vendor),
            'account': None if a is None else {
                'id': str(a.id), 'name': a.name, 'chrome_profile': a.chrome_profile, 'email': a.email,
            },
        })
    return out
