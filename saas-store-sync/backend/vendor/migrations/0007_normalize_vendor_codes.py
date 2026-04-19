"""Normalize vendor codes + display names to the canonical set.

Canonical list (code -> display name):
    amazonus   -> AmazonUS
    amazonau   -> AmazonAU
    ebayus     -> EbayUS
    ebayau     -> EbayAU
    vevorau    -> VevorAU
    costcoau   -> CostcoAU
    hebus      -> HebUS
    koganau    -> KoganAU

Strategy (no data loss):
    1. Create all canonical vendors (get_or_create).
    2. For every legacy/alias vendor (e.g. ``amazon``, ``amazonusa``, ``ebay``,
       ``vevor``, ``heb``) re-home its Products to the canonical vendor, then
       delete the legacy Vendor row.
    3. Ensure AliExpress stays (still hidden in the API layer), but don't
       elevate it to a canonical code.
"""
from django.db import migrations


CANONICAL = [
    ("amazonus", "AmazonUS"),
    ("amazonau", "AmazonAU"),
    ("ebayus", "EbayUS"),
    ("ebayau", "EbayAU"),
    ("vevorau", "VevorAU"),
    ("costcoau", "CostcoAU"),
    ("hebus", "HebUS"),
    ("koganau", "KoganAU"),
]

# legacy_code -> canonical_code
ALIASES = {
    "amazon": "amazonus",
    "amazonusa": "amazonus",
    "amazon_us": "amazonus",
    "amazon-us": "amazonus",
    "amazon_au": "amazonau",
    "amazon-au": "amazonau",
    "ebay": "ebayus",
    "ebay_us": "ebayus",
    "ebay-us": "ebayus",
    "ebay_au": "ebayau",
    "ebay-au": "ebayau",
    "vevor": "vevorau",
    "vevor_au": "vevorau",
    "vevor-au": "vevorau",
    "costco": "costcoau",
    "costco_au": "costcoau",
    "costco-au": "costcoau",
    "heb": "hebus",
    "heb_us": "hebus",
    "heb-us": "hebus",
    "kogan": "koganau",
    "kogan_au": "koganau",
    "kogan-au": "koganau",
}


def forward(apps, schema_editor):
    Vendor = apps.get_model("vendor", "Vendor")
    Product = apps.get_model("products", "Product")

    canonical = {}
    for code, name in CANONICAL:
        v, _ = Vendor.objects.get_or_create(code=code, defaults={"name": name})
        if v.name != name:
            v.name = name
            v.save(update_fields=["name"])
        canonical[code] = v

    for legacy_code, target_code in ALIASES.items():
        target = canonical.get(target_code)
        if not target:
            continue
        legacy_qs = Vendor.objects.filter(code__iexact=legacy_code).exclude(id=target.id)
        for legacy in legacy_qs:
            Product.objects.filter(vendor=legacy).update(vendor=target)
            try:
                legacy.delete()
            except Exception:
                pass


def backward(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("vendor", "0006_add_costcoau_ebayau_and_hide_aliexpress"),
        ("products", "0006_upload_user_store"),
    ]

    operations = [
        migrations.RunPython(forward, backward),
    ]
