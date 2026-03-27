"""Seed Marketplace and Vendor lookup data."""
from django.core.management.base import BaseCommand

from marketplace.models import Marketplace
from vendor.models import Vendor


MARKETPLACES = [
    ('reverb', 'Reverb'),
    ('etsy', 'Etsy'),
    ('walmart', 'Walmart'),
    ('sears', 'Sears'),
    ('mydeal', 'MyDeal'),
    ('kogan', 'Kogan'),
]

VENDORS = [
    ('amazon', 'Amazon'),
    ('vevor', 'Vevor'),
    ('aliexpress', 'AliExpress'),
    ('ebay', 'eBay'),
]


class Command(BaseCommand):
    help = 'Seed Marketplace and Vendor lookup tables'

    def handle(self, *args, **options):
        for code, name in MARKETPLACES:
            _, created = Marketplace.objects.get_or_create(code=code, defaults={'name': name})
            if created:
                self.stdout.write(f'Created Marketplace: {name}')
        for code, name in VENDORS:
            _, created = Vendor.objects.get_or_create(code=code, defaults={'name': name})
            if created:
                self.stdout.write(f'Created Vendor: {name}')
        self.stdout.write(self.style.SUCCESS('Seeding complete.'))
