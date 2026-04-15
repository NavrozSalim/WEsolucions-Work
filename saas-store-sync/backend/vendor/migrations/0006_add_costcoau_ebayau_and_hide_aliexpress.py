# Update vendor list for store settings dropdown:
# - add CostcoAU
# - add eBayAU
# - keep existing AliExpress rows untouched (hidden in API layer)
from django.db import migrations


def forward(apps, schema_editor):
    Vendor = apps.get_model("vendor", "Vendor")
    Vendor.objects.get_or_create(code="costcoau", defaults={"name": "CostcoAU"})
    Vendor.objects.get_or_create(code="ebayau", defaults={"name": "eBayAU"})


def backward(apps, schema_editor):
    Vendor = apps.get_model("vendor", "Vendor")
    Vendor.objects.filter(code__in=["costcoau", "ebayau"]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("vendor", "0005_add_heb_vendor"),
    ]

    operations = [
        migrations.RunPython(forward, backward),
    ]

