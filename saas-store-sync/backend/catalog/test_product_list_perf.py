"""Catalog product list should paginate without annotating the full store."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from catalog.models import ProductMapping
from marketplace.models import Marketplace
from products.models import Product
from stores.models import Store
from vendor.models import Vendor, VendorPrice


class CatalogProductListPerfTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username='catlist',
            email='catlist@example.com',
            password='pw',
        )
        mp, _ = Marketplace.objects.get_or_create(code='kogan', defaults={'name': 'Kogan'})
        self.store = Store.objects.create(
            user=self.user,
            name='KIPS Test',
            region='AU',
            api_token='tok',
            marketplace=mp,
            management_mode='inventory_only',
        )
        self.vendor, _ = Vendor.objects.get_or_create(
            code='amazonau',
            defaults={'name': 'Amazon AU'},
        )
        for i in range(1, 61):
            product = Product.objects.create(
                vendor=self.vendor,
                vendor_sku=f'SKU-{i:04d}',
                vendor_url=f'https://www.amazon.com.au/dp/{i}',
                owner=self.user,
            )
            ProductMapping.objects.create(
                store=self.store,
                product=product,
                title=f'Item {i}',
                marketplace_child_sku=f'SKU-{i:04d}',
                is_active=True,
                sync_status='pending',
            )
            VendorPrice.objects.create(product=product, price=10 + i, stock=5)

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_paginated_list_returns_page_and_count(self):
        resp = self.client.get(
            f'/api/v1/stores/{self.store.id}/products/',
            {'page': 1, 'page_size': 25},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['count'], 60)
        self.assertEqual(len(resp.data['results']), 25)
        first = resp.data['results'][0]
        self.assertIn('vendor_price', first)
        self.assertIn('vendor_url', first)
        self.assertTrue(str(first.get('vendor_url') or '').startswith('https://'))

    def test_second_page(self):
        resp = self.client.get(
            f'/api/v1/stores/{self.store.id}/products/',
            {'page': 2, 'page_size': 25},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 25)
        self.assertEqual(resp.data['count'], 60)
