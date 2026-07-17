"""Upload history list must stay fast for large catalog files."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from catalog.models import CatalogUpload, CatalogUploadRow
from marketplace.models import Marketplace
from stores.models import Store

from catalog.views import (
    _catalog_upload_history_extras,
    _upload_action_reason_from_counts,
    _upload_action_reason_from_rows,
)


class CatalogUploadHistoryExtrasTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username='uphist',
            email='uphist@example.com',
            password='pw',
        )
        mp, _ = Marketplace.objects.get_or_create(code='kogan', defaults={'name': 'Kogan'})
        self.store = Store.objects.create(
            user=self.user,
            name='KTFS Test',
            region='AU',
            api_token='tok',
            marketplace=mp,
            management_mode='inventory_only',
        )
        self.upload = CatalogUpload.objects.create(
            user=self.user,
            store=self.store,
            original_filename='vevor.xlsx',
            total_rows=0,
            processed_rows=0,
            status=CatalogUpload.Status.VALIDATED,
        )

    def test_reason_helper_matches_row_scan(self):
        rows = [
            CatalogUploadRow(
                catalog_upload=self.upload,
                row_number=i,
                vendor_name_raw='Vevor',
                action_raw=action,
            )
            for i, action in enumerate(['Add', 'Add', 'Update', 'Delete'], start=1)
        ]
        CatalogUploadRow.objects.bulk_create(rows)
        from_rows = _upload_action_reason_from_rows(
            CatalogUploadRow.objects.filter(catalog_upload=self.upload)
        )
        _, _, reasons = _catalog_upload_history_extras([self.upload.id])
        self.assertEqual(from_rows, reasons[self.upload.id])
        self.assertIn('Add', from_rows)
        self.assertIn('Update', from_rows)

    def test_extras_do_not_require_loading_all_row_objects(self):
        # Simulate a large Vevor-style upload with many identical actions.
        CatalogUploadRow.objects.bulk_create([
            CatalogUploadRow(
                catalog_upload=self.upload,
                row_number=i,
                vendor_name_raw='Vevor AU',
                action_raw='Add',
                sync_status=(
                    CatalogUploadRow.SyncStatus.ERROR
                    if i <= 3
                    else CatalogUploadRow.SyncStatus.PENDING
                ),
            )
            for i in range(1, 501)
        ])
        vendors, errors, reasons = _catalog_upload_history_extras([self.upload.id])
        self.assertEqual(vendors[self.upload.id], 'Vevor AU')
        self.assertEqual(errors[self.upload.id], 3)
        self.assertEqual(reasons[self.upload.id], 'Add')

    def test_upload_list_api_response_shape(self):
        CatalogUploadRow.objects.create(
            catalog_upload=self.upload,
            row_number=1,
            vendor_name_raw='Vevor',
            action_raw='Add',
        )
        client = APIClient()
        client.force_authenticate(user=self.user)
        resp = client.get(f'/api/v1/stores/{self.store.id}/catalog/uploads/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 1)
        row = resp.data[0]
        self.assertEqual(row['vendor_source'], 'Vevor')
        self.assertEqual(row['reason'], 'Add')
        self.assertEqual(row['total_rows'], 0)
        self.assertIn('has_errors', row)
        self.assertIn('error_row_count', row)

    def test_single_action_reason_label(self):
        self.assertEqual(_upload_action_reason_from_counts({'Add': 10}), 'Add')
        self.assertEqual(_upload_action_reason_from_counts({}), '—')
