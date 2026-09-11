"""API coverage for marketplace-specific catalog / delete sample CSVs."""
from __future__ import annotations

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from marketplace.models import Marketplace
from stores.models import Store

User = get_user_model()


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class CatalogSampleTemplateApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='tpl_u', email='tpl_u@example.com', password='pass12345',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.mydeal_mp, _ = Marketplace.objects.get_or_create(
            code='mydeal', defaults={'name': 'MyDeal'},
        )
        self.kogan_mp, _ = Marketplace.objects.get_or_create(
            code='kogan', defaults={'name': 'Kogan'},
        )
        self.mydeal_store = Store.objects.create(
            user=self.user,
            name='MDP&P',
            region='AU',
            api_token='tok-md',
            marketplace=self.mydeal_mp,
        )
        self.kogan_store = Store.objects.create(
            user=self.user,
            name='KTFS',
            region='AU',
            api_token='tok-kg',
            marketplace=self.kogan_mp,
        )

    def _get(self, **params):
        return self.client.get('/api/v1/catalog/sample-template/', params)

    def test_mydeal_store_downloads_mydeal_catalog_not_generic(self):
        res = self._get(store_id=str(self.mydeal_store.id))
        self.assertEqual(res.status_code, 200)
        self.assertIn('attachment', res['Content-Disposition'])
        self.assertIn('catalog_upload_template_mydeal.csv', res['Content-Disposition'])
        text = res.content.decode('utf-8')
        header = text.splitlines()[0]
        self.assertIn('SKU', header)
        self.assertIn('Action', header)
        self.assertIn('MyDeal', text)
        self.assertNotIn('Reverb', text)
        self.assertNotIn('Fulfillment Center ID', header)

    def test_mydeal_delete_template(self):
        res = self._get(store_id=str(self.mydeal_store.id), action='delete')
        self.assertEqual(res.status_code, 200)
        self.assertIn('catalog_delete_template_mydeal.csv', res['Content-Disposition'])
        lines = res.content.decode('utf-8').splitlines()
        self.assertIn('Action', lines[0])
        self.assertIn('Delete', lines[1])
        self.assertNotIn(',Add,', lines[1])

    def test_kogan_catalog_and_delete_are_kogan_labeled(self):
        catalog = self._get(store_id=str(self.kogan_store.id))
        self.assertEqual(catalog.status_code, 200)
        self.assertIn('catalog_upload_template_kogan.csv', catalog['Content-Disposition'])
        self.assertIn('Kogan', catalog.content.decode('utf-8'))

        delete = self._get(store_id=str(self.kogan_store.id), action='delete')
        self.assertEqual(delete.status_code, 200)
        self.assertIn('catalog_delete_template_kogan.csv', delete['Content-Disposition'])
        self.assertIn('Delete', delete.content.decode('utf-8').splitlines()[1])

    def test_marketplace_query_overrides_store(self):
        res = self._get(store_id=str(self.mydeal_store.id), marketplace='walmart')
        self.assertEqual(res.status_code, 200)
        self.assertIn('catalog_upload_template_walmart.csv', res['Content-Disposition'])
        self.assertIn('Fulfillment Center ID', res.content.decode('utf-8'))
