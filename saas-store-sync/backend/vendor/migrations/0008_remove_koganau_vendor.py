"""Remove the ``KoganAU`` Vendor row.

Kogan is a **marketplace** (the Kogan Google Sheets integration in
``store_adapters/kogan_adapter.py`` and the ``kogan`` row in the
``Marketplace`` table), not a product vendor. Migration
``0007_normalize_vendor_codes`` accidentally seeded it into
``Vendor`` as well, which made it appear in the "Select vendor to add"
dropdown and the catalog upload vendor whitelist.

This migration deletes the stray Vendor row. If any Products are still
attached to it, we refuse rather than orphan / cascade-delete them —
the operator should remap those products to the correct vendor
(probably ``AmazonAU`` or ``EbayAU``) first, either via the admin or
via a data fix in the shell.
"""
from django.db import migrations


def forward(apps, schema_editor):
    Vendor = apps.get_model("vendor", "Vendor")
    Product = apps.get_model("products", "Product")

    vendor_qs = Vendor.objects.filter(code__iexact="koganau")
    if not vendor_qs.exists():
        return

    for v in vendor_qs:
        attached = Product.objects.filter(vendor=v).count()
        if attached:
            raise RuntimeError(
                f"Cannot remove Vendor 'KoganAU' ({v.id}): {attached} product(s) "
                "still reference it. Re-home them to the correct vendor "
                "(AmazonAU / EbayAU / etc.) first, then re-run the migration."
            )
        v.delete()


def backward(apps, schema_editor):
    # Deliberately non-reversible: we do not want to re-introduce the
    # bogus KoganAU vendor on a rollback. Recreate it manually via
    # fixture or admin if you ever need it.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("vendor", "0007_normalize_vendor_codes"),
        ("products", "0006_upload_user_store"),
    ]

    operations = [
        migrations.RunPython(forward, backward),
    ]
