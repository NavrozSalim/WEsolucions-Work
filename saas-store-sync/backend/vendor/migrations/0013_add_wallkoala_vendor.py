from django.db import migrations


def seed_wallkoala(apps, schema_editor):
    Vendor = apps.get_model("vendor", "Vendor")
    Vendor.objects.get_or_create(code="wallkoala", defaults={"name": "Wallkoala"})


def unseed_wallkoala(apps, schema_editor):
    Vendor = apps.get_model("vendor", "Vendor")
    Product = apps.get_model("products", "Product")
    vendor_qs = Vendor.objects.filter(code="wallkoala")
    for vendor in vendor_qs:
        attached = Product.objects.filter(vendor_id=vendor.id).count()
        if attached:
            raise RuntimeError(
                f"Cannot remove Vendor 'Wallkoala' ({vendor.id}): {attached} product(s) still attached."
            )
    vendor_qs.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("vendor", "0012_add_costwayau_vendor"),
        ("products", "0009_store_orphan_retention"),
    ]

    operations = [
        migrations.RunPython(seed_wallkoala, unseed_wallkoala),
    ]
