"""MyDeal Price/Inventory templates split into one file per vendor."""
from __future__ import annotations

import csv
import io
import zipfile
from decimal import Decimal

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from catalog.models import MydealTemplateRow, ProductMapping
from catalog.mydeal_templates import (
    MYDEAL_INVENTORY_HEADERS,
    MYDEAL_PRICE_HEADERS,
    _export_inventory_csv,
    _export_price_csv,
    build_export_response,
    vendor_template_summary,
)
from marketplace.models import Marketplace
from products.models import Product
from stores.models import Store, StoreVendorPriceSettings
from vendor.models import Vendor

User = get_user_model()


def _rows(content: bytes) -> list[list[str]]:
    return list(csv.reader(content.decode('utf-8').splitlines()))


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class MydealVendorTemplateTests(TestCase):
    """Store with an Amazon SKU, a Vevor SKU, a Vevor SKU removed from the
    catalog, and a MyDeal SKU with no mapping."""

    def setUp(self):
        self.mp, _ = Marketplace.objects.get_or_create(
            code='mydeal', defaults={'name': 'MyDeal'},
        )
        self.amazon = Vendor.objects.get(code='amazonau')
        self.vevor, _ = Vendor.objects.get_or_create(
            code='test-vevor-split', defaults={'name': 'Vevor Test'},
        )
        self.user = User.objects.create_user(
            username='md_split', email='md_split@example.com', password='pass12345',
        )
        self.store = Store.objects.create(
            user=self.user,
            name='BW-P&P',
            region='AU',
            api_token='tok',
            marketplace=self.mp,
            mydeal_setup_method='upload',
        )
        StoreVendorPriceSettings.objects.create(
            store=self.store, vendor=self.amazon, mydeal_rrp_margin_percentage=Decimal('50'),
        )
        StoreVendorPriceSettings.objects.create(
            store=self.store, vendor=self.vevor, mydeal_rrp_margin_percentage=Decimal('20'),
        )
        self._map(self.amazon, 'AMZ-V1', 'MD-AMZ', Decimal('50.00'), 7)
        self._map(self.vevor, 'VEV-V1', 'MD-VEV', Decimal('80.00'), 3)
        self._map(self.vevor, 'VEV-V2', 'MD-VEV-GONE', Decimal('10.00'), 9, active=False)

        skus = ['MD-AMZ', 'MD-VEV', 'MD-VEV-GONE', 'MD-NOMAP']
        for i, sku in enumerate(skus, start=1):
            for kind in (MydealTemplateRow.Kind.PRICE, MydealTemplateRow.Kind.INVENTORY):
                MydealTemplateRow.objects.create(
                    store=self.store,
                    kind=kind,
                    row_number=i,
                    deal_id=f'D{i}',
                    variant_id=f'V{i}',
                    external_id=f'E{i}',
                    sku=sku,
                    deal_title=f'Title {i}',
                    discontinued='FALSE',
                    mydeal_approved='Approved',
                )

    def _map(self, vendor, vendor_sku, md_sku, price, stock, active=True):
        product = Product.objects.create(owner=self.user, vendor=vendor, vendor_sku=vendor_sku)
        ProductMapping.objects.create(
            store=self.store,
            product=product,
            marketplace_child_sku=md_sku,
            store_price=price,
            store_stock=stock,
            is_active=active,
        )

    def _zip(self, resp) -> dict[str, list[list[str]]]:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            return {name: _rows(zf.read(name)) for name in zf.namelist()}

    def test_by_vendor_zip_has_folder_per_vendor(self):
        resp = build_export_response(self.store, 'by_vendor')
        self.assertEqual(resp['Content-Type'], 'application/zip')
        self.assertIn('Templates by Vendor.zip', resp['Content-Disposition'])
        files = self._zip(resp)
        self.assertEqual(
            set(files),
            {
                f'{self.amazon.name}/Mydeal - BW-P&P - {self.amazon.name} - Price Template.csv',
                f'{self.amazon.name}/Mydeal - BW-P&P - {self.amazon.name} - Inventory Template.csv',
                'Vevor Test/Mydeal - BW-P&P - Vevor Test - Price Template.csv',
                'Vevor Test/Mydeal - BW-P&P - Vevor Test - Inventory Template.csv',
                'Unmapped/Mydeal - BW-P&P - Unmapped - Price Template.csv',
                'Unmapped/Mydeal - BW-P&P - Unmapped - Inventory Template.csv',
            },
        )
        vevor_inv = files['Vevor Test/Mydeal - BW-P&P - Vevor Test - Inventory Template.csv']
        self.assertEqual(vevor_inv[0], MYDEAL_INVENTORY_HEADERS)
        by_sku = {r[3]: r for r in vevor_inv[1:]}
        self.assertEqual(set(by_sku), {'MD-VEV', 'MD-VEV-GONE'})
        self.assertEqual(by_sku['MD-VEV'][6], '3')
        # Removed from the catalog: stock 0, not discontinued, stays with its vendor.
        self.assertEqual(by_sku['MD-VEV-GONE'][6:8], ['0', 'FALSE'])

        unmapped_inv = files['Unmapped/Mydeal - BW-P&P - Unmapped - Inventory Template.csv']
        self.assertEqual([r[3] for r in unmapped_inv[1:]], ['MD-NOMAP'])
        self.assertEqual(unmapped_inv[1][6], '')

    def test_vendor_files_add_up_to_all_vendor_file(self):
        files = self._zip(build_export_response(self.store, 'by_vendor'))
        for kind, export, headers in (
            ('Price', _export_price_csv, MYDEAL_PRICE_HEADERS),
            ('Inventory', _export_inventory_csv, MYDEAL_INVENTORY_HEADERS),
        ):
            combined = _rows(export(self.store))
            self.assertEqual(combined[0], headers)
            split: list[list[str]] = []
            for name, rows in files.items():
                if name.endswith(f'- {kind} Template.csv'):
                    self.assertEqual(rows[0], headers)
                    split.extend(rows[1:])
            self.assertEqual(sorted(split), sorted(combined[1:]))
            self.assertEqual(len(split), 4)

    def test_single_vendor_price_csv_uses_that_vendors_margin(self):
        resp = build_export_response(self.store, 'price', str(self.vevor.id))
        self.assertIn('Vevor Test - Price Template.csv', resp['Content-Disposition'])
        rows = _rows(resp.content)
        self.assertEqual(rows[0], MYDEAL_PRICE_HEADERS)
        by_sku = {r[3]: r for r in rows[1:]}
        self.assertEqual(set(by_sku), {'MD-VEV', 'MD-VEV-GONE'})
        self.assertEqual(by_sku['MD-VEV'][6:8], ['80.00', '100.00'])

    def test_single_vendor_both_is_zip_of_two_files(self):
        resp = build_export_response(self.store, 'both', str(self.amazon.id))
        files = self._zip(resp)
        self.assertEqual(len(files), 2)
        for rows in files.values():
            self.assertEqual([r[3] for r in rows[1:]], ['MD-AMZ'])

    def test_unmapped_inventory_only(self):
        resp = build_export_response(self.store, 'inventory', 'unmapped')
        rows = _rows(resp.content)
        self.assertEqual([r[3] for r in rows[1:]], ['MD-NOMAP'])

    def test_vendor_with_no_rows_raises(self):
        other, _ = Vendor.objects.get_or_create(code='test-empty-split', defaults={'name': 'Empty'})
        with self.assertRaises(ValueError):
            build_export_response(self.store, 'price', str(other.id))

    def test_bad_vendor_param_raises(self):
        with self.assertRaises(ValueError):
            build_export_response(self.store, 'price', 'not-a-uuid')

    def test_summary_counts_per_vendor_with_unmapped_last(self):
        summary = vendor_template_summary(self.store)
        self.assertEqual(summary[-1]['id'], 'unmapped')
        counts = {s['id']: (s['price_rows'], s['inventory_rows']) for s in summary}
        self.assertEqual(counts[str(self.amazon.id)], (1, 1))
        self.assertEqual(counts[str(self.vevor.id)], (2, 2))
        self.assertEqual(counts['unmapped'], (1, 1))

    def test_by_vendor_with_only_inventory_uploaded(self):
        MydealTemplateRow.objects.filter(
            store=self.store, kind=MydealTemplateRow.Kind.PRICE,
        ).delete()
        files = self._zip(build_export_response(self.store, 'by_vendor'))
        self.assertTrue(files)
        self.assertTrue(all(n.endswith('Inventory Template.csv') for n in files))

    def test_by_vendor_with_nothing_uploaded_raises(self):
        MydealTemplateRow.objects.filter(store=self.store).delete()
        with self.assertRaises(ValueError):
            build_export_response(self.store, 'by_vendor')

    def test_api_status_and_vendor_export(self):
        client = APIClient()
        client.force_authenticate(self.user)
        base = f'/api/v1/stores/{self.store.id}/mydeal/templates'
        plain = client.get(f'{base}/status/')
        self.assertEqual(plain.status_code, 200, plain.content)
        self.assertNotIn('vendors', plain.json())
        with_vendors = client.get(f'{base}/status/', {'vendors': 1})
        self.assertEqual(len(with_vendors.json()['vendors']), 3)

        resp = client.get(f'{base}/export/', {'type': 'inventory', 'vendor': str(self.vevor.id)})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(_rows(resp.content)), 3)

        bad = client.get(f'{base}/export/', {'type': 'inventory', 'vendor': 'nope'})
        self.assertEqual(bad.status_code, 400)
