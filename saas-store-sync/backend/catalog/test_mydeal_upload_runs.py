"""MyDeal "Upload the file on the Marketplace" runs: web page endpoints and the inventory bot laptop flow."""
from __future__ import annotations

import hashlib
import io
import zipfile
from decimal import Decimal

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from catalog.models import CatalogActivityLog, IngestToken, MydealTemplateRow, MydealUploadRun, ProductMapping
from catalog.mydeal_upload_runs import UploadRunError, start_run
from marketplace.models import Marketplace
from products.models import Product
from stores.models import Store, StoreVendorPriceSettings
from vendor.models import Vendor

User = get_user_model()
API = '/api/v1'


def _token(label, scopes, owner=None, stores=None):
    raw = f'raw-{label}'
    IngestToken.objects.create(
        label=label, token_hash=hashlib.sha256(raw.encode()).hexdigest(), token_prefix=raw[:8],
        scopes=scopes, is_active=True, created_by=owner, stores=stores or [],
    )
    return raw


@override_settings(DEBUG=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
class MydealUploadRunTests(TestCase):
    def setUp(self):
        self.mp, _ = Marketplace.objects.get_or_create(code='mydeal', defaults={'name': 'MyDeal'})
        self.amazon = Vendor.objects.get(code='amazonau')
        self.vevor, _ = Vendor.objects.get_or_create(code='test-vevor-upl', defaults={'name': 'Vevor Upl'})
        self.user = User.objects.create_user(username='md_up', email='md_up@example.com', password='pass12345')
        self.other = User.objects.create_user(username='md_up2', email='md_up2@example.com', password='pass12345')
        self.store = Store.objects.create(
            user=self.user, name='BW-P&P', region='AU', api_token='tok', marketplace=self.mp,
            mydeal_setup_method='upload',
        )
        for v in (self.amazon, self.vevor):
            StoreVendorPriceSettings.objects.create(
                store=self.store, vendor=v, mydeal_rrp_margin_percentage=Decimal('20'))
        for vendor, sku in ((self.amazon, 'MD-A'), (self.vevor, 'MD-V')):
            p = Product.objects.create(owner=self.user, vendor=vendor, vendor_sku=f'{sku}-V')
            ProductMapping.objects.create(store=self.store, product=p, marketplace_child_sku=sku,
                                          store_price=Decimal('40.00'), store_stock=4, is_active=True)
        for i, sku in enumerate(['MD-A', 'MD-V', 'MD-NOMAP'], start=1):
            for kind in (MydealTemplateRow.Kind.PRICE, MydealTemplateRow.Kind.INVENTORY):
                MydealTemplateRow.objects.create(
                    store=self.store, kind=kind, row_number=i, deal_id=f'D{i}', sku=sku,
                    discontinued='FALSE', mydeal_approved='Approved')
        self.web = APIClient()
        self.web.force_authenticate(self.user)
        self.laptop_raw = _token('inv-laptop', ['inventory-bot'], owner=self.user)
        self.laptop = APIClient()
        self.laptop.credentials(HTTP_AUTHORIZATION=f'Bearer {self.laptop_raw}')

    def _start(self, vendors=None, kinds=('inventory',), dry_run=False):
        return self.web.post(
            f'{API}/stores/{self.store.id}/mydeal/upload-runs/',
            {'vendors': vendors or [str(self.vevor.id)], 'kinds': list(kinds), 'dry_run': dry_run},
            format='json',
        )

    # ----- starting -----
    def test_start_validates_choices(self):
        with self.assertRaisesMessage(UploadRunError, 'Choose Inventory, Price or both'):
            start_run(self.store, self.user, [str(self.vevor.id)], [])
        with self.assertRaisesMessage(UploadRunError, 'at least one vendor'):
            start_run(self.store, self.user, [], ['price'])
        with self.assertRaisesMessage(UploadRunError, 'no template rows'):
            start_run(self.store, self.user, ['00000000-0000-0000-0000-000000000000'], ['price'])

    def test_only_one_active_run_per_store(self):
        self.assertEqual(self._start().status_code, 201)
        resp = self._start()
        self.assertEqual(resp.status_code, 400)
        self.assertIn('already waiting or running', resp.json()['error'])

    def test_start_and_list(self):
        resp = self._start(vendors=[str(self.amazon.id), 'unmapped'], kinds=['price', 'inventory'], dry_run=True)
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        self.assertEqual(body['status'], 'pending')
        self.assertEqual(body['kinds'], ['inventory', 'price'])
        self.assertTrue(body['dry_run'])
        self.assertEqual([v['id'] for v in body['vendors']], [str(self.amazon.id), 'unmapped'])
        runs = self.web.get(f'{API}/stores/{self.store.id}/mydeal/upload-runs/').json()['runs']
        self.assertEqual(len(runs), 1)
        self.assertNotIn('log', runs[0])

    def test_other_user_cannot_see_store_runs(self):
        self._start()
        stranger = APIClient()
        stranger.force_authenticate(self.other)
        resp = stranger.get(f'{API}/stores/{self.store.id}/mydeal/upload-runs/')
        self.assertIn(resp.status_code, (403, 404))

    # ----- laptop -----
    def test_full_laptop_flow(self):
        run_id = self._start(vendors=[str(self.vevor.id)], kinds=['inventory', 'price']).json()['id']

        job = self.laptop.get(f'{API}/ingest/inventory-bot/next-job/').json()
        self.assertEqual(job['job_id'], run_id)
        self.assertEqual(job['kinds'], ['inventory', 'price'])
        self.assertFalse(job['include_unmapped'])
        self.assertIsNone(self.laptop.get(f'{API}/ingest/inventory-bot/next-job/').json()['job_id'])
        run = MydealUploadRun.objects.get(pk=run_id)
        self.assertEqual((run.status, run.laptop), ('running', 'inv-laptop'))

        resp = self.laptop.get(job['file_url'])
        self.assertEqual(resp.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            names = sorted(zf.namelist())
        self.assertEqual(names, [
            'Vevor Upl/Mydeal - BW-P&P - Vevor Upl - Inventory Template.csv',
            'Vevor Upl/Mydeal - BW-P&P - Vevor Upl - Price Template.csv',
        ])

        results = [{'vendor': 'Vevor Upl', 'kind': 'inventory', 'rows': 1, 'status': 'submitted',
                    'dash_state': 'completed', 'request_id': '6af1', 'requested_at': '19/11/2026 11:05',
                    'unexpected': 'dropped'}]
        self.laptop.post(job['progress_url'], {'log': 'line 1\nline 2', 'attention': 'Type the code',
                                               'results': results}, format='json')
        detail = self.web.get(f'{API}/stores/{self.store.id}/mydeal/upload-runs/{run_id}/').json()
        self.assertEqual(detail['attention'], 'Type the code')
        self.assertEqual(detail['log'], 'line 1\nline 2')
        self.assertEqual(detail['results'][0]['request_id'], '6af1')
        self.assertNotIn('unexpected', detail['results'][0])

        resp = self.laptop.post(job['complete_url'], {'exit_code': 0, 'log': 'done', 'results': results,
                                                      'message': 'Finished: 1 vendor(s), 1 file(s)'}, format='json')
        self.assertEqual(resp.json()['status'], 'finished')
        run.refresh_from_db()
        self.assertEqual((run.attention, run.message, run.exit_code), ('', 'Finished: 1 vendor(s), 1 file(s)', 0))
        self.assertIsNotNone(run.finished_at)
        self.assertEqual(self.laptop.post(job['complete_url'], {'exit_code': 0}, format='json').status_code, 409)

    def test_activity_log_entries(self):
        run_id = self._start(vendors=[str(self.vevor.id)], kinds=['inventory', 'price']).json()['id']
        job = self.laptop.get(f'{API}/ingest/inventory-bot/next-job/').json()
        results = [
            {'vendor': 'Vevor Upl', 'kind': 'inventory', 'part': 1, 'parts': 1, 'status': 'submitted',
             'dash_state': 'completed', 'request_id': '6afe1b6d', 'requested_at': '18/11/2026 13:41'},
            {'vendor': 'Vevor Upl', 'kind': 'price', 'part': 1, 'parts': 1, 'status': 'submitted',
             'dash_state': 'completed_with_errors', 'error_count': 2, 'request_id': '6afe1b83',
             'requested_at': '18/11/2026 13:41'},
        ]
        self.laptop.post(job['complete_url'], {'exit_code': 1, 'results': results,
                                               'message': 'Finished: 1 vendor(s), 2 file(s)'}, format='json')
        logs = list(CatalogActivityLog.objects.filter(store=self.store).order_by('created_at'))
        self.assertEqual([l.action_type for l in logs], [
            'mydeal_upload_queued', 'mydeal_upload_started', 'mydeal_upload_file', 'mydeal_upload_file',
            'mydeal_upload_finished'])
        self.assertIn('Inventory + Price for Vevor Upl', logs[0].message)
        self.assertIn('laptop inv-laptop started', logs[1].message)
        self.assertEqual(logs[2].message,
                         'Inventory updated: Vevor Upl at 18/11/2026 13:41, COMPLETED, request 6afe1b6d')
        self.assertTrue(logs[3].message.startswith('Price updated with 2 error(s): Vevor Upl'))
        self.assertIn('Finished: 1 vendor(s), 2 file(s)', logs[4].message)
        self.assertEqual(logs[4].metadata['mydeal_upload_run'], run_id)
        self.assertEqual(logs[0].user_id, self.user.id)

    def test_cancel_and_dry_run_logs(self):
        run_id = self._start().json()['id']
        self.web.post(f'{API}/stores/{self.store.id}/mydeal/upload-runs/{run_id}/cancel/')
        self.assertTrue(CatalogActivityLog.objects.filter(
            store=self.store, action_type='mydeal_upload_cancelled').exists())
        self._start(dry_run=True)
        job = self.laptop.get(f'{API}/ingest/inventory-bot/next-job/').json()
        self.laptop.post(job['complete_url'], {'exit_code': 0, 'message': 'Dry run finished',
                                               'results': [{'vendor': 'Vevor Upl', 'kind': 'inventory',
                                                            'status': 'dry_run'}]}, format='json')
        self.assertFalse(CatalogActivityLog.objects.filter(store=self.store, action_type='mydeal_upload_file').exists())
        last = CatalogActivityLog.objects.filter(store=self.store).order_by('-created_at').first()
        self.assertIn('(dry run)', last.message)

    def test_zip_only_has_chosen_kind(self):
        self._start(vendors=[str(self.amazon.id)], kinds=['price'])
        job = self.laptop.get(f'{API}/ingest/inventory-bot/next-job/').json()
        with zipfile.ZipFile(io.BytesIO(self.laptop.get(job['file_url']).content)) as zf:
            names = zf.namelist()
        self.assertEqual(len(names), 1)
        self.assertTrue(names[0].endswith('Price Template.csv'))

    def test_failed_exit_code_and_cancel_while_running(self):
        run_id = self._start().json()['id']
        job = self.laptop.get(f'{API}/ingest/inventory-bot/next-job/').json()
        cancel = self.web.post(f'{API}/stores/{self.store.id}/mydeal/upload-runs/{run_id}/cancel/')
        self.assertEqual(cancel.json()['status'], 'cancelled')
        self.assertEqual(self.laptop.get(job['status_url']).json()['status'], 'cancelled')
        self.laptop.post(job['complete_url'], {'exit_code': 2, 'log': 'stopped'}, format='json')
        run = MydealUploadRun.objects.get(pk=run_id)
        self.assertEqual(run.status, 'cancelled')
        self.assertIn('exit code 2', run.message)

    def test_exit_code_two_is_failed(self):
        self._start()
        job = self.laptop.get(f'{API}/ingest/inventory-bot/next-job/').json()
        self.laptop.post(job['complete_url'], {'exit_code': 2, 'message': 'STOPPED: Wrong IP'}, format='json')
        run = MydealUploadRun.objects.get(pk=job['job_id'])
        self.assertEqual((run.status, run.message), ('failed', 'STOPPED: Wrong IP'))

    def test_cancel_pending_ends_it(self):
        run_id = self._start().json()['id']
        self.web.post(f'{API}/stores/{self.store.id}/mydeal/upload-runs/{run_id}/cancel/')
        run = MydealUploadRun.objects.get(pk=run_id)
        self.assertEqual(run.status, 'cancelled')
        self.assertIsNotNone(run.finished_at)
        self.assertIsNone(self.laptop.get(f'{API}/ingest/inventory-bot/next-job/').json()['job_id'])
        again = self.web.post(f'{API}/stores/{self.store.id}/mydeal/upload-runs/{run_id}/cancel/')
        self.assertEqual(again.status_code, 400)

    def test_token_scope_and_owner(self):
        self._start()
        wrong_scope = APIClient()
        wrong_scope.credentials(HTTP_AUTHORIZATION=f"Bearer {_token('heb-pc', ['heb'])}")
        self.assertEqual(wrong_scope.get(f'{API}/ingest/inventory-bot/next-job/').status_code, 403)
        no_auth = APIClient()
        self.assertIn(no_auth.get(f'{API}/ingest/inventory-bot/next-job/').status_code, (401, 403))
        other_owner = APIClient()
        other_owner.credentials(HTTP_AUTHORIZATION=f"Bearer {_token('other', ['inventory-bot'], self.other)}")
        self.assertIsNone(other_owner.get(f'{API}/ingest/inventory-bot/next-job/').json()['job_id'])
        job = self.laptop.get(f'{API}/ingest/inventory-bot/next-job/').json()
        self.assertEqual(other_owner.get(job['status_url']).status_code, 404)
        self.assertEqual(other_owner.get(job['file_url']).status_code, 404)

    def test_token_store_list_limits_jobs(self):
        shemaya = Store.objects.create(
            user=self.user, name='Shemaya', region='AU', api_token='tok2', marketplace=self.mp,
            mydeal_setup_method='upload',
        )
        MydealUploadRun.objects.create(store=shemaya, vendors=[{'id': 'x', 'name': 'X'}], kinds=['price'])
        pp_laptop = APIClient()
        pp_laptop.credentials(
            HTTP_AUTHORIZATION=f"Bearer {_token('pp-laptop', ['inventory-bot'], self.user, ['BW-P&P'])}")
        self.assertIsNone(pp_laptop.get(f'{API}/ingest/inventory-bot/next-job/').json()['job_id'])
        shemaya_run = MydealUploadRun.objects.get(store=shemaya)
        self.assertEqual(pp_laptop.get(f'{API}/ingest/inventory-bot/jobs/{shemaya_run.id}/').status_code, 404)
        run_id = self._start().json()['id']
        self.assertEqual(pp_laptop.get(f'{API}/ingest/inventory-bot/next-job/').json()['job_id'], run_id)
        shemaya_run.refresh_from_db()
        self.assertEqual(shemaya_run.status, 'pending')
