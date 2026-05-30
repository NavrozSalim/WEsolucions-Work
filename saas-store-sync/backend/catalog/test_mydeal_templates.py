"""Mydeal template ingest, SKU join, and RRP export."""
from __future__ import annotations

import io
import zipfile
from decimal import Decimal

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from catalog.models import MydealTemplateRow, ProductMapping
from catalog.marketplace_rrp import compute_marketplace_rrp
from catalog.mydeal_templates import (
    MYDEAL_INVENTORY_HEADERS,
    MYDEAL_PRICE_HEADERS,
    _export_price_csv,
    ingest_mydeal_template,
    ingest_mydeal_templates_zip,
)
from marketplace.models import Marketplace
from products.models import Product
from stores.models import Store, StoreVendorPriceSettings
from vendor.models import Vendor

User = get_user_model()


def _csv_bytes(headers: list[str], rows: list[list[str]]) -> io.BytesIO:
    lines = [','.join(headers)]
    for row in rows:
        lines.append(','.join(row))
    return io.BytesIO('\n'.join(lines).encode('utf-8'))


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class MydealTemplateTests(TestCase):
    def setUp(self):
        self.mp, _ = Marketplace.objects.get_or_create(
            code='mydeal',
            defaults={'name': 'MyDeal'},
        )
        self.vendor = Vendor.objects.get(code='amazonau')
        self.user = User.objects.create_user(
            username='mydeal_u', email='mydeal_u@example.com', password='pass12345',
        )
        self.store = Store.objects.create(
            user=self.user,
            name='TFS',
            region='AU',
            api_token='tok',
            marketplace=self.mp,
            mydeal_setup_method='upload',
        )
        StoreVendorPriceSettings.objects.create(
            store=self.store,
            vendor=self.vendor,
            mydeal_rrp_margin_percentage=Decimal('50'),
        )
        self.product = Product.objects.create(
            owner=self.user,
            vendor=self.vendor,
            vendor_sku='VSKU-1',
        )
        ProductMapping.objects.create(
            store=self.store,
            product=self.product,
            marketplace_child_sku='MD-SKU-1',
            store_price=Decimal('50.00'),
            store_stock=12,
            is_active=True,
        )

    def test_compute_rrp_margin_formula(self):
        rrp = compute_marketplace_rrp(Decimal('50.00'), Decimal('50'))
        self.assertEqual(rrp, Decimal('100.00'))

    def test_compute_rrp_discount_off_rrp(self):
        rrp = compute_marketplace_rrp(Decimal('74.00'), Decimal('26'))
        self.assertEqual(rrp, Decimal('100.00'))

    def test_ingest_rejects_bad_headers(self):
        buf = _csv_bytes(['SKU', 'Price'], [['A', '1']])
        with self.assertRaises(ValueError) as ctx:
            ingest_mydeal_template(self.store, MydealTemplateRow.Kind.PRICE, buf)
        self.assertIn('Invalid template headers', str(ctx.exception))

    def test_ingest_price_and_inventory_templates(self):
        price_buf = _csv_bytes(
            MYDEAL_PRICE_HEADERS,
            [['D1', 'V1', 'E1', 'MD-SKU-1', '', 'Title', '', '']],
        )
        ingest_mydeal_template(self.store, MydealTemplateRow.Kind.PRICE, price_buf)
        self.assertEqual(
            MydealTemplateRow.objects.filter(
                store=self.store, kind=MydealTemplateRow.Kind.PRICE,
            ).count(),
            1,
        )

        inv_buf = _csv_bytes(
            MYDEAL_INVENTORY_HEADERS,
            [['D1', 'V1', 'E1', 'MD-SKU-1', '', 'Title', '', 'FALSE', 'Approved']],
        )
        ingest_mydeal_template(self.store, MydealTemplateRow.Kind.INVENTORY, inv_buf)
        self.assertEqual(
            MydealTemplateRow.objects.filter(
                store=self.store, kind=MydealTemplateRow.Kind.INVENTORY,
            ).count(),
            1,
        )

    def test_reupload_replaces_previous_rows(self):
        price_buf = _csv_bytes(
            MYDEAL_PRICE_HEADERS,
            [['D1', 'V1', 'E1', 'OLD-SKU', '', 'Title', '', '']],
        )
        first = ingest_mydeal_template(self.store, MydealTemplateRow.Kind.PRICE, price_buf)
        self.assertFalse(first['replaced'])

        price_buf2 = _csv_bytes(
            MYDEAL_PRICE_HEADERS,
            [
                ['D2', 'V2', 'E2', 'NEW-1', '', 'A', '', ''],
                ['D3', 'V3', 'E3', 'NEW-2', '', 'B', '', ''],
            ],
        )
        second = ingest_mydeal_template(self.store, MydealTemplateRow.Kind.PRICE, price_buf2)
        self.assertTrue(second['replaced'])
        self.assertEqual(second['previous_row_count'], 1)
        self.assertEqual(
            MydealTemplateRow.objects.filter(
                store=self.store, kind=MydealTemplateRow.Kind.PRICE,
            ).count(),
            2,
        )
        skus = set(
            MydealTemplateRow.objects.filter(
                store=self.store, kind=MydealTemplateRow.Kind.PRICE,
            ).values_list('sku', flat=True)
        )
        self.assertEqual(skus, {'NEW-1', 'NEW-2'})

    def test_zip_ingest_replaces_both_kinds(self):
        zbuf = io.BytesIO()
        with zipfile.ZipFile(zbuf, 'w') as zf:
            zf.writestr(
                'price.csv',
                '\n'.join([
                    ','.join(MYDEAL_PRICE_HEADERS),
                    'D1,V1,E1,ZIP-SKU,,T,,',
                ]),
            )
            zf.writestr(
                'inv.csv',
                '\n'.join([
                    ','.join(MYDEAL_INVENTORY_HEADERS),
                    'D1,V1,E1,ZIP-SKU,,T,,FALSE,Approved',
                ]),
            )
        zbuf.seek(0)
        out = ingest_mydeal_templates_zip(self.store, zbuf)
        self.assertEqual(set(out['kinds']), {'price', 'inventory'})
        self.assertTrue(out['status']['ready'])

    def test_export_price_fills_price_and_rrp(self):
        MydealTemplateRow.objects.create(
            store=self.store,
            kind=MydealTemplateRow.Kind.PRICE,
            row_number=1,
            deal_id='D1',
            variant_id='V1',
            external_id='E1',
            sku='MD-SKU-1',
            deal_title='Title',
        )
        content = _export_price_csv(self.store).decode('utf-8')
        lines = content.strip().splitlines()
        self.assertEqual(lines[0], ','.join(MYDEAL_PRICE_HEADERS))
        data = lines[1].split(',')
        self.assertEqual(data[3], 'MD-SKU-1')
        self.assertEqual(data[6], '50.00')
        self.assertEqual(data[7], '100.00')
