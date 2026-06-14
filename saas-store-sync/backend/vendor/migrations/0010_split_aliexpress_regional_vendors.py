"""Split single AliExpress vendor into UK / US / AU regional vendors."""
from django.db import migrations


ALIEXPRESS_REGIONAL = (
    ('aliexpressuk', 'AliExpress UK'),
    ('aliexpressus', 'AliExpress US'),
    ('aliexpressau', 'AliExpress AU'),
)

LEGACY_ALIEXPRESS_CODES = ('aliexpress', 'aliexpress_us', 'aliexpress_au')


def forward(apps, schema_editor):
    Vendor = apps.get_model('vendor', 'Vendor')
    Product = apps.get_model('products', 'Product')

    regional = {}
    for code, name in ALIEXPRESS_REGIONAL:
        vendor, _ = Vendor.objects.get_or_create(code=code, defaults={'name': name})
        if vendor.name != name:
            vendor.name = name
            vendor.save(update_fields=['name'])
        regional[code] = vendor

    uk = regional['aliexpressuk']
    legacy_qs = Vendor.objects.filter(code__in=LEGACY_ALIEXPRESS_CODES)
    for legacy in legacy_qs:
        code = (legacy.code or '').strip().lower()
        target = uk
        if code in ('aliexpress_us',):
            target = regional['aliexpressus']
        elif code in ('aliexpress_au',):
            target = regional['aliexpressau']

        Product.objects.filter(vendor=legacy).update(vendor=target)

        try:
            StoreVendorPriceSettings = apps.get_model('stores', 'StoreVendorPriceSettings')
            StoreVendorInventorySettings = apps.get_model('stores', 'StoreVendorInventorySettings')
        except LookupError:
            StoreVendorPriceSettings = None
            StoreVendorInventorySettings = None

        if StoreVendorPriceSettings is not None:
            for row in StoreVendorPriceSettings.objects.filter(vendor=legacy):
                exists = StoreVendorPriceSettings.objects.filter(
                    store_id=row.store_id,
                    vendor=target,
                ).exists()
                if exists:
                    row.delete()
                else:
                    row.vendor = target
                    row.save(update_fields=['vendor'])

        if StoreVendorInventorySettings is not None:
            for row in StoreVendorInventorySettings.objects.filter(vendor=legacy):
                exists = StoreVendorInventorySettings.objects.filter(
                    store_id=row.store_id,
                    vendor=target,
                ).exists()
                if exists:
                    row.delete()
                else:
                    row.vendor = target
                    row.save(update_fields=['vendor'])

        if legacy.id not in {v.id for v in regional.values()}:
            legacy.delete()


def backward(apps, schema_editor):
    Vendor = apps.get_model('vendor', 'Vendor')
    Product = apps.get_model('products', 'Product')
    legacy, _ = Vendor.objects.get_or_create(code='aliexpress', defaults={'name': 'AliExpress'})
    for code, _name in ALIEXPRESS_REGIONAL:
        vendor = Vendor.objects.filter(code=code).first()
        if vendor:
            Product.objects.filter(vendor=vendor).update(vendor=legacy)
            vendor.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('vendor', '0009_aliexpress_oauth_credentials'),
        ('products', '0006_upload_user_store'),
        ('stores', '0004_add_vendor_price_inventory_settings'),
    ]

    operations = [
        migrations.RunPython(forward, backward),
    ]
