"""Nora stock loads from the database when the AU worker has no media file."""
import io

from django.contrib.auth import get_user_model
from django.test import TestCase
from openpyxl import Workbook

from marketplace.models import Marketplace
from stores.models import Store, StoreVendorInventorySettings
from stores.nora import load_store_nora_stock_map
from vendor.models import Vendor

User = get_user_model()


def _nora_xlsx() -> bytes:
    wb = Workbook()
    wb.active.title = 'Sheet1'
    sheet = wb.create_sheet('Export inventory')
    header = [''] * 13
    header[0] = 'BarCode'
    header[10] = 'Available inventory'
    sheet.append(header)
    sheet.append(['XMR-C28-366-AU', 't', 0, 0, 0, 0, 0, 0, 0, 0, 12])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class NoraInventoryBytesTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username='nora', email='nora@example.com', password='x')
        market, _ = Marketplace.objects.get_or_create(code='lasoo', defaults={'name': 'Lasoo'})
        self.store = Store.objects.create(
            user=user,
            name='Lasoo Nora',
            region='AU',
            management_mode='full_store',
            marketplace=market,
            api_token='',
        )
        self.vendor, _ = Vendor.objects.get_or_create(code='noraau', defaults={'name': 'Nora AU'})

    def test_stock_map_reads_database_bytes_when_the_disk_file_is_missing(self):
        inv = StoreVendorInventorySettings.objects.create(
            store=self.store,
            vendor=self.vendor,
            nora_inventory_original_name='Australian_inventory.xlsx',
            inventory_file_bytes=_nora_xlsx(),
        )
        inv.nora_inventory_file.name = 'nora_inventory/2026/10/Australian_inventory.xlsx'
        inv.save(update_fields=['nora_inventory_file'])

        stock = load_store_nora_stock_map(self.store)
        self.assertEqual(stock['XMR-C28-366-AU'], 12)
