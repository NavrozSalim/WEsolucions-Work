# Per-tenant Product rows: same vendor SKU can exist once per account owner.
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill_product_owner(apps, schema_editor):
    Product = apps.get_model('products', 'Product')
    ProductMapping = apps.get_model('catalog', 'ProductMapping')
    Store = apps.get_model('stores', 'Store')

    for product in Product.objects.filter(owner__isnull=True).iterator(chunk_size=500):
        pm = (
            ProductMapping.objects.filter(product_id=product.id)
            .order_by('id')
            .values_list('store_id', flat=True)
            .first()
        )
        if not pm:
            continue
        store = Store.objects.filter(id=pm).only('user_id').first()
        if store and store.user_id:
            Product.objects.filter(pk=product.pk).update(owner_id=store.user_id)


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0006_upload_user_store'),
        ('catalog', '0024_productmapping_lookup_indexes'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='owner',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='owned_products',
                to=settings.AUTH_USER_MODEL,
                db_index=True,
            ),
        ),
        migrations.RunPython(backfill_product_owner, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name='product',
            name='uq_product_vendor_sku_variation',
        ),
        migrations.AddConstraint(
            model_name='product',
            constraint=models.UniqueConstraint(
                fields=('vendor', 'vendor_sku', 'variation_id', 'owner'),
                name='uq_product_vendor_sku_variation_owner',
            ),
        ),
    ]
