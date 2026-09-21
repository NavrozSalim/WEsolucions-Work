"""Tests for Costco AU URL resolution — upload Vendor URL must win over built short URLs."""
from __future__ import annotations

from unittest.mock import MagicMock

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from catalog.models import CatalogUpload, CatalogUploadRow, ProductMapping
from catalog.serializers import ProductMappingSerializer
from catalog.tasks import _get_or_create_product, _update_product_mapping
from catalog.vendor_url_resolve import (
    canonicalize_costco_pdp_url,
    costco_product_id_from_value,
    normalize_costco_url,
    resolve_costco_product_url,
    resolve_vendor_url_for_row,
    sync_product_vendor_url_from_row,
)
from marketplace.models import Marketplace
from products.models import Product
from stores.models import Store
from sync.tasks import resolve_vendor_scrape_url
from vendor.models import Vendor

User = get_user_model()

FULL_COSTCO_URL = (
    'https://www.costco.com.au/p/TFCO-173734-New/some-product-slug'
)
SHORT_COSTCO_URL = 'https://www.costco.com.au/p/173734'


class CostcoUrlResolveTests(SimpleTestCase):
    def test_costco_product_id_from_composite_sku(self):
        self.assertEqual(costco_product_id_from_value('TFCO-173734-New'), '173734')

    def test_costco_product_id_keeps_option_code(self):
        self.assertEqual(costco_product_id_from_value('1851433-FER'), '1851433-FER')
        self.assertEqual(costco_product_id_from_value('COST-1851433-FER'), '1851433-FER')
        self.assertEqual(
            costco_product_id_from_value(
                'https://www.costco.com.au/c/Burago-Paddock-Ferrari/p/1851433-FER'
            ),
            '1851433-FER',
        )
        self.assertEqual(
            costco_product_id_from_value('https://www.costco.com.au/p/1851433-FER'),
            '1851433-FER',
        )
        # Catalog junk suffix is not an option code.
        self.assertEqual(costco_product_id_from_value('TFCO-173734-NEW'), '173734')

    def test_canonicalize_keeps_option_code_and_merges_sku_hint(self):
        variant = 'https://www.costco.com.au/p/1851433-FER'
        self.assertEqual(canonicalize_costco_pdp_url(variant), variant)
        self.assertEqual(
            canonicalize_costco_pdp_url(
                'https://www.costco.com.au/c/Burago/p/1851433-FER'
            ),
            'https://www.costco.com.au/c/Burago/p/1851433-FER',
        )
        self.assertEqual(
            canonicalize_costco_pdp_url(
                'https://www.costco.com.au/p/1851433',
                'COST-1851433-FER',
            ),
            variant,
        )

    def test_costco_product_id_from_full_url_path(self):
        self.assertEqual(
            costco_product_id_from_value('https://www.costco.com.au/p/TFCO-173734-New/slug'),
            '173734',
        )

    def test_costco_product_id_strips_c0_prefix_and_leading_zeros(self):
        self.assertEqual(costco_product_id_from_value('AU-C0141624'), '141624')
        self.assertEqual(costco_product_id_from_value('C0141624'), '141624')
        self.assertEqual(costco_product_id_from_value('0141624'), '141624')
        self.assertEqual(costco_product_id_from_value('UR-C01484024'), '1484024')
        self.assertEqual(
            costco_product_id_from_value('https://www.costco.com.au/p/0141624'),
            '141624',
        )
        self.assertEqual(
            costco_product_id_from_value(
                'https://www.costco.com.au/c/Gillette-Fusion-5/p/141624'
            ),
            '141624',
        )

    def test_canonicalize_rewrites_legacy_and_padded_urls(self):
        self.assertEqual(
            canonicalize_costco_pdp_url(
                'https://www.costco.com.au/p/TFS-CO-138511-New/everblue'
            ),
            'https://www.costco.com.au/p/138511',
        )
        self.assertEqual(
            canonicalize_costco_pdp_url('https://www.costco.com.au/p/0141624'),
            'https://www.costco.com.au/p/141624',
        )
        keep = 'https://www.costco.com.au/c/Gillette-Fusion-5/p/141624'
        self.assertEqual(canonicalize_costco_pdp_url(keep), keep)
        self.assertEqual(
            canonicalize_costco_pdp_url(SHORT_COSTCO_URL),
            SHORT_COSTCO_URL,
        )

    def test_normalize_costco_url_preserves_slug(self):
        self.assertEqual(normalize_costco_url(FULL_COSTCO_URL), FULL_COSTCO_URL)

    def test_resolve_prefers_upload_url_over_vendor_id(self):
        vendor = MagicMock()
        vendor.code = 'costcoau'
        product = MagicMock()
        product.vendor = vendor
        product.vendor_url = SHORT_COSTCO_URL
        product.vendor_sku = 'TFCO-173734-New'
        url = resolve_costco_product_url(
            product,
            vendor_url_raw=FULL_COSTCO_URL,
            vendor_id_raw='173734',
        )
        self.assertEqual(url, FULL_COSTCO_URL)

    def test_resolve_uses_stored_product_url_without_shortening(self):
        vendor = MagicMock()
        vendor.code = 'costcoau'
        product = MagicMock()
        product.vendor = vendor
        product.vendor_url = FULL_COSTCO_URL
        product.vendor_sku = 'TFCO-173734-New'
        url = resolve_costco_product_url(product)
        self.assertEqual(url, FULL_COSTCO_URL)

    def test_resolve_builds_from_vendor_id_when_no_url(self):
        vendor = MagicMock()
        vendor.code = 'costcoau'
        product = MagicMock()
        product.vendor = vendor
        product.vendor_url = ''
        product.vendor_sku = 'TFCO-173734-New'
        url = resolve_costco_product_url(product, vendor_id_raw='TFCO-173734-New')
        self.assertEqual(url, SHORT_COSTCO_URL)

    def test_resolve_vendor_url_for_row_upload_wins(self):
        vendor = MagicMock()
        vendor.code = 'costcoau'
        row = MagicMock()
        row.vendor_url_raw = FULL_COSTCO_URL
        row.vendor_id_raw = '173734'
        self.assertEqual(resolve_vendor_url_for_row(vendor, row), FULL_COSTCO_URL)

    def test_scrape_url_prefers_upload_over_vendor_id(self):
        row = MagicMock()
        row.vendor_url_raw = FULL_COSTCO_URL
        row.vendor_id_raw = '173734'
        vendor = MagicMock()
        vendor.code = 'costcoau'
        product = MagicMock()
        product.vendor = vendor
        product.vendor_url = ''
        product.vendor_sku = 'TFCO-173734-New'
        store = MagicMock()
        store.region = 'AU'
        url = resolve_vendor_scrape_url(product, store, row)
        self.assertEqual(url, SHORT_COSTCO_URL)

    def test_scrape_url_canonicalizes_stored_legacy_product_url(self):
        vendor = MagicMock()
        vendor.code = 'costcoau'
        product = MagicMock()
        product.vendor = vendor
        product.vendor_url = FULL_COSTCO_URL
        product.vendor_sku = 'TFCO-173734-New'
        store = MagicMock()
        store.region = 'AU'
        url = resolve_vendor_scrape_url(product, store, None)
        self.assertEqual(url, SHORT_COSTCO_URL)


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class CostcoIngestAndSerializerTests(TestCase):
    def setUp(self):
        self.mp, _ = Marketplace.objects.get_or_create(
            code='kogan_costco_urls',
            defaults={'name': 'Kogan Costco URLs'},
        )
        self.vendor, _ = Vendor.objects.get_or_create(
            code='costcoau',
            defaults={'name': 'CostcoAU'},
        )
        self.user = User.objects.create_user(
            username='costco_url_u',
            email='costco_url_u@example.com',
            password='pass12345',
        )
        self.store = Store.objects.create(
            user=self.user,
            name='Costco Test Store',
            region='AU',
            api_token='tok-costco-url',
            marketplace=self.mp,
        )
        self.upload = CatalogUpload.objects.create(
            user=self.user,
            store=self.store,
            original_filename='costco.csv',
        )

    def _row(self, **kwargs):
        defaults = {
            'catalog_upload': self.upload,
            'row_number': 1,
            'action_raw': 'add',
            'vendor_name_raw': 'CostcoAU',
            'store_name_raw': self.store.name,
            'vendor_id_raw': 'TFCO-173734-New',
            'vendor_sku_raw': 'TFCO-173734-New',
            'vendor_url_raw': FULL_COSTCO_URL,
            'marketplace_parent_sku_raw': 'MP-173734',
            'vendor': self.vendor,
            'store': self.store,
        }
        defaults.update(kwargs)
        return CatalogUploadRow.objects.create(**defaults)

    def test_ingest_add_persists_full_upload_url_on_new_mapping(self):
        row = self._row()
        product = _get_or_create_product(self.vendor, row, store=self.store)
        pm = ProductMapping.objects.create(
            store=self.store,
            product=product,
            marketplace_parent_sku='MP-173734',
            is_active=True,
        )
        _update_product_mapping(pm, row)

        product.refresh_from_db()
        self.assertEqual(product.vendor_url, FULL_COSTCO_URL)

    def test_sync_product_overwrites_short_url_with_upload(self):
        product = Product.objects.create(
            vendor=self.vendor,
            owner=self.user,
            vendor_sku='TFCO-173734-New',
            vendor_url=SHORT_COSTCO_URL,
        )
        row = self._row()
        changed = sync_product_vendor_url_from_row(product, self.vendor, row)
        self.assertTrue(changed)
        product.refresh_from_db()
        self.assertEqual(product.vendor_url, FULL_COSTCO_URL)

    def test_serializer_returns_latest_upload_url(self):
        product = Product.objects.create(
            vendor=self.vendor,
            owner=self.user,
            vendor_sku='TFCO-173734-New',
            vendor_url=SHORT_COSTCO_URL,
        )
        pm = ProductMapping.objects.create(
            store=self.store,
            product=product,
            marketplace_parent_sku='MP-173734',
            is_active=True,
        )
        row = self._row(product=product, product_mapping=pm, row_number=2)
        row.save()

        data = ProductMappingSerializer(pm).data
        self.assertEqual(data['vendor_url'], SHORT_COSTCO_URL)

    def test_serializer_link_uses_variant_option_code_from_sku(self):
        product = Product.objects.create(
            vendor=self.vendor,
            owner=self.user,
            vendor_sku='COST-1851433-FER',
            vendor_url='https://www.costco.com.au/p/1851433',
        )
        pm = ProductMapping.objects.create(
            store=self.store,
            product=product,
            marketplace_parent_sku='COST-1851433',
            marketplace_child_sku='COST-1851433-FER',
            is_active=True,
        )
        data = ProductMappingSerializer(pm).data
        self.assertEqual(data['vendor_url'], 'https://www.costco.com.au/p/1851433-FER')
