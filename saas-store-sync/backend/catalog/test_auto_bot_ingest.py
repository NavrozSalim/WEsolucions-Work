"""Auto Bot Excel jobs: claim one job, result upload, cancel and delete."""
from __future__ import annotations

import hashlib
import io
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from openpyxl import Workbook, load_workbook
from rest_framework.test import APIClient

from catalog.ingest_views import SUPPORTED_VENDORS
from catalog.models import HebScrapeJob, IngestToken

User = get_user_model()

_TEMP_MEDIA = tempfile.mkdtemp(prefix='auto_bot_media_')
_STOCK_HEADER = ['store_name', 'sku', 'price', 'quantity']


def xlsx(name: str, rows: list[list], header=_STOCK_HEADER) -> SimpleUploadedFile:
    wb = Workbook()
    ws = wb.active
    ws.append(header)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return SimpleUploadedFile(
        name,
        buf.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


def make_token(label: str, scopes: list[str], user, stores=None) -> tuple[IngestToken, str]:
    raw = f'auto-bot-{label}-' + ('z' * 24)
    tok = IngestToken.objects.create(
        label=label,
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        token_prefix=raw[:8],
        scopes=scopes,
        created_by=user,
        stores=stores or [],
    )
    return tok, raw


@override_settings(MEDIA_ROOT=_TEMP_MEDIA)
class AutoBotIngestTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_TEMP_MEDIA, ignore_errors=True)

    def setUp(self):
        self.user_a = User.objects.create_user(username='bot_a', email='bot_a@example.com', password='pass12345')
        self.user_b = User.objects.create_user(username='bot_b', email='bot_b@example.com', password='pass12345')
        self.token_a, self.raw_a = make_token('laptop-1', ['reverb', 'etsy'], self.user_a)
        self.token_a2, self.raw_a2 = make_token('laptop-2', ['reverb', 'etsy'], self.user_a)
        self.token_b, self.raw_b = make_token('laptop-b', ['reverb', 'etsy'], self.user_b)
        self.token_reverb_only, self.raw_reverb_only = make_token('reverb-only', ['reverb'], self.user_a)
        self.page = APIClient()
        self.page.force_authenticate(self.user_a)
        self.page_b = APIClient()
        self.page_b.force_authenticate(self.user_b)

    # --- helpers

    def laptop(self, raw: str) -> APIClient:
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {raw}')
        return client

    def queue(self, page: APIClient, job_type='etsy-stock', rows=None, store_name='IQ') -> str:
        rows = rows or [['IQ', 'SKU-1', '19.96', '5'], ['IQ', 'SKU-2', '', '3'], ['IQ', 'SKU-3', '9.99', '']]
        resp = page.post(
            '/api/v1/auto-bot/jobs/',
            {'job_type': job_type, 'store_name': store_name, 'file': xlsx('PriceStock.xlsx', rows)},
            format='multipart',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        jobs = resp.json()['jobs']
        self.assertEqual(len(jobs), 1)
        return jobs[0]['id']

    def claim(self, raw: str, vendor='etsy'):
        return self.laptop(raw).get(f'/api/v1/ingest/{vendor}/next-job/')

    # --- claim one job

    def test_claim_one_job_per_poll_scoped_to_token_owner(self):
        first = self.queue(self.page)
        second = self.queue(self.page)
        other_user = self.queue(self.page_b)

        resp = self.claim(self.raw_a)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['job_id'], first)
        self.assertEqual(data['job_type'], 'etsy-stock')
        self.assertEqual(data['store_name'], 'IQ')
        self.assertEqual(data['file_name'], 'PriceStock.xlsx')
        self.assertEqual(data['file_url'], f'/api/v1/ingest/etsy/jobs/{first}/file/')
        job = HebScrapeJob.objects.get(id=first)
        self.assertEqual(job.status, HebScrapeJob.Status.CLAIMED)
        self.assertEqual(job.claimed_by_token_id, self.token_a.id)

        # A second laptop of the same user gets the next job, not the same one.
        self.assertEqual(self.claim(self.raw_a2).json()['job_id'], second)
        self.assertIsNone(self.claim(self.raw_a).json()['job_id'])
        self.assertEqual(HebScrapeJob.objects.get(id=other_user).status, HebScrapeJob.Status.PENDING)

    def test_vendor_and_scope_must_match(self):
        self.queue(self.page)
        self.assertIsNone(self.claim(self.raw_a, vendor='reverb').json()['job_id'])
        self.assertEqual(self.claim(self.raw_reverb_only, vendor='etsy').status_code, 403)

    def test_file_only_for_claiming_laptop(self):
        job_id = self.queue(self.page)
        self.claim(self.raw_a)
        url = f'/api/v1/ingest/etsy/jobs/{job_id}/file/'
        resp = self.laptop(self.raw_a).get(url)
        self.assertEqual(resp.status_code, 200)
        wb = load_workbook(io.BytesIO(b''.join(resp.streaming_content)))
        self.assertEqual([c.value for c in wb.active[1]], _STOCK_HEADER)
        self.assertEqual(self.laptop(self.raw_a2).get(url).status_code, 404)
        self.assertEqual(self.laptop(self.raw_b).get(url).status_code, 404)

    # --- result upload

    def post_results(self, raw: str, job_id: str, done_rows, remaining_rows, exit_code=0):
        files = {'job_id': job_id, 'exit_code': str(exit_code)}
        if done_rows is not None:
            files['done_file'] = xlsx('Updated(2026-11-04).xlsx', done_rows)
        if remaining_rows is not None:
            files['remaining_file'] = xlsx('remain-to-update(2026-11-04).xlsx', remaining_rows)
        return self.laptop(raw).post('/api/v1/ingest/etsy/', files, format='multipart')

    def test_result_upload_counts_and_complete(self):
        job_id = self.queue(self.page)
        self.claim(self.raw_a)
        resp = self.post_results(
            self.raw_a, job_id,
            [['IQ', 'SKU-1', '19.96', '5'], ['IQ', 'SKU-2', None, '3']],
            [['IQ', 'SKU-3', '9.99', None]],
            exit_code=2,
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['stats'], {'done': 2, 'remaining': 1, 'total': 3, 'exit_code': 2})

        done = self.laptop(self.raw_a).post(
            f'/api/v1/ingest/etsy/jobs/{job_id}/complete/', {'status': 'done'}, format='json'
        )
        self.assertEqual(done.status_code, 200)
        self.assertEqual(done.json()['status'], 'done')

        row = next(j for j in self.page.get('/api/v1/auto-bot/jobs/').json()['jobs'] if j['id'] == job_id)
        self.assertEqual((row['done_count'], row['remaining_count']), (2, 1))
        self.assertEqual(row['laptop'], 'laptop-1')
        self.assertTrue(row['has_remaining_file'])

        download = self.page.get(f'/api/v1/auto-bot/jobs/{job_id}/download/remaining/')
        self.assertEqual(download.status_code, 200)
        wb = load_workbook(io.BytesIO(b''.join(download.streaming_content)))
        self.assertEqual([c.value for c in wb.active[1]], _STOCK_HEADER)
        self.assertEqual(wb.active.cell(row=2, column=2).value, 'SKU-3')

    def test_no_output_keeps_input_as_remaining(self):
        job_id = self.queue(self.page)
        self.claim(self.raw_a)
        resp = self.post_results(self.raw_a, job_id, None, None, exit_code=1)
        self.assertEqual(resp.json()['stats']['remaining'], 3)
        self.assertEqual(resp.json()['stats']['done'], 0)

    def test_results_rejected_for_other_laptop_or_user(self):
        job_id = self.queue(self.page)
        self.claim(self.raw_a)
        self.assertEqual(self.post_results(self.raw_a2, job_id, [], []).status_code, 404)
        self.assertEqual(self.post_results(self.raw_b, job_id, [], []).status_code, 404)
        self.assertEqual(self.page_b.get(f'/api/v1/auto-bot/jobs/{job_id}/download/input/').status_code, 404)

    # --- cancel and delete

    def test_cancel_tells_laptop_to_stop(self):
        job_id = self.queue(self.page)
        self.claim(self.raw_a)
        status_url = f'/api/v1/ingest/etsy/jobs/{job_id}/'
        self.assertFalse(self.laptop(self.raw_a).get(status_url).json()['cancelled'])

        self.assertEqual(self.page.post(f'/api/v1/auto-bot/jobs/{job_id}/cancel/').status_code, 200)
        self.assertTrue(self.laptop(self.raw_a).get(status_url).json()['cancelled'])
        complete = self.laptop(self.raw_a).post(f'/api/v1/ingest/etsy/jobs/{job_id}/complete/', {}, format='json')
        self.assertEqual(complete.status_code, 409)
        # Partial results may still be kept after a cancel.
        self.assertEqual(self.post_results(self.raw_a, job_id, [['IQ', 'SKU-1', '1', '1']], []).status_code, 200)

    def test_cancel_finished_job_conflicts(self):
        job_id = self.queue(self.page)
        self.claim(self.raw_a)
        self.laptop(self.raw_a).post(f'/api/v1/ingest/etsy/jobs/{job_id}/complete/', {}, format='json')
        self.assertEqual(self.page.post(f'/api/v1/auto-bot/jobs/{job_id}/cancel/').status_code, 409)

    def test_delete_returns_404_to_laptop_and_removes_files(self):
        job_id = self.queue(self.page)
        self.claim(self.raw_a)
        storage_name = HebScrapeJob.objects.get(id=job_id).input_file
        storage, name = storage_name.storage, storage_name.name
        self.assertTrue(storage.exists(name))

        self.assertEqual(self.page_b.delete(f'/api/v1/auto-bot/jobs/{job_id}/').status_code, 404)
        self.assertEqual(self.page.delete(f'/api/v1/auto-bot/jobs/{job_id}/').status_code, 204)
        self.assertEqual(self.laptop(self.raw_a).get(f'/api/v1/ingest/etsy/jobs/{job_id}/').status_code, 404)
        self.assertFalse(storage.exists(name))

    # --- shared laptops (token with no owner)

    def test_shared_laptop_serves_every_user_oldest_first(self):
        _, raw_shared = make_token('laptop-shared', ['reverb', 'etsy'], None)
        first_b = self.queue(self.page_b)
        second_a = self.queue(self.page)
        third_b = self.queue(self.page_b)

        # Waiting jobs show their place in the shared queue.
        rows = {j['id']: j for j in self.page.get('/api/v1/auto-bot/jobs/').json()['jobs']}
        self.assertEqual(rows[second_a]['queue_position'], 2)
        self.assertEqual(rows[second_a]['status'], 'pending')

        shared = self.laptop(raw_shared)
        self.assertEqual(shared.get('/api/v1/ingest/etsy/next-job/').json()['job_id'], first_b)
        self.assertEqual(shared.get('/api/v1/ingest/etsy/next-job/').json()['job_id'], second_a)
        self.assertEqual(shared.get(f'/api/v1/ingest/etsy/jobs/{second_a}/file/').status_code, 200)
        self.assertEqual(self.post_results(raw_shared, second_a, [['IQ', 'SKU-1', '1', '1']], []).status_code, 200)
        self.assertEqual(shared.get('/api/v1/ingest/etsy/next-job/').json()['job_id'], third_b)

    def test_page_lists_shared_laptops_as_free_or_busy(self):
        _, raw_shared = make_token('laptop-shared', ['reverb', 'etsy'], None)
        make_token('heb-only', ['heb'], None)
        job_id = self.queue(self.page)
        self.claim(raw_shared)

        mine = {l['label']: l for l in self.page.get('/api/v1/auto-bot/laptops/').json()['laptops']}
        self.assertTrue(mine['laptop-shared']['shared'])
        self.assertTrue(mine['laptop-shared']['busy'])
        self.assertEqual(mine['laptop-shared']['job_label'], 'Etsy - price and stock')
        self.assertFalse(mine['laptop-1']['busy'])
        self.assertNotIn('heb-only', mine)
        self.assertNotIn('laptop-b', mine)

        # Another user sees the shared laptop as busy, without the job name.
        theirs = {l['label']: l for l in self.page_b.get('/api/v1/auto-bot/laptops/').json()['laptops']}
        self.assertTrue(theirs['laptop-shared']['busy'])
        self.assertEqual(theirs['laptop-shared']['job_label'], '')
        self.assertEqual(self.page_b.get(f'/api/v1/auto-bot/jobs/{job_id}/download/input/').status_code, 404)

    # --- one laptop per store

    def test_upload_splits_into_one_job_per_store(self):
        rows = [['IQ', 'SKU-1', '1', '1'], ['AMH', 'SKU-2', '2', '2'], ['iq', 'SKU-3', '3', '3']]
        resp = self.page.post(
            '/api/v1/auto-bot/jobs/',
            {'job_type': 'etsy-stock', 'file': xlsx('Mixed.xlsx', rows)},
            format='multipart',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        jobs = {j['store_name']: j for j in resp.json()['jobs']}
        self.assertEqual(set(jobs), {'AMH', 'IQ'})
        self.assertEqual(jobs['IQ']['file_name'], 'Mixed - IQ.xlsx')

        iq = HebScrapeJob.objects.get(id=jobs['IQ']['id'])
        with iq.input_file.open('rb') as fh:
            sheet = load_workbook(io.BytesIO(fh.read())).active
        self.assertEqual([c.value for c in sheet[1]], _STOCK_HEADER)
        self.assertEqual([sheet.cell(row=r, column=2).value for r in (2, 3)], ['SKU-1', 'SKU-3'])
        self.assertEqual(sheet.max_row, 3)

    def test_upload_rejects_rows_without_store_or_missing_chosen_store(self):
        blank_store = self.page.post(
            '/api/v1/auto-bot/jobs/',
            {'job_type': 'etsy-stock', 'file': xlsx('a.xlsx', [['IQ', 'S1', '1', '1'], [None, 'S2', '1', '1']])},
            format='multipart',
        )
        self.assertEqual(blank_store.status_code, 400)
        self.assertIn('Row 3', blank_store.json()['error'])
        wrong_store = self.page.post(
            '/api/v1/auto-bot/jobs/',
            {'job_type': 'etsy-stock', 'store_name': 'MMS', 'file': xlsx('b.xlsx', [['IQ', 'S1', '1', '1']])},
            format='multipart',
        )
        self.assertEqual(wrong_store.status_code, 400)

    def test_laptop_only_takes_its_own_stores_for_etsy_and_reverb(self):
        user_c = User.objects.create_user(username='bot_c', email='bot_c@example.com', password='pass12345')
        page_c = APIClient()
        page_c.force_authenticate(user_c)
        _, raw_iq = make_token('worker-1', ['reverb', 'etsy'], None, stores=['IQ', 'IQR'])
        _, raw_amh = make_token('worker-3', ['reverb', 'etsy'], None, stores=['AMH'])

        mms = self.queue(page_c, rows=[['MMS', 'S1', '1', '1']], store_name='')
        iq_etsy = self.queue(page_c, rows=[['IQ', 'S1', '1', '1']], store_name='')
        amh = self.queue(page_c, rows=[['AMH', 'S1', '1', '1']], store_name='')
        iq_reverb = self.queue(page_c, job_type='reverb-stock', rows=[['IQ', 'S2', '1', '1']], store_name='')

        iq_laptop, amh_laptop = self.laptop(raw_iq), self.laptop(raw_amh)
        self.assertEqual(iq_laptop.get('/api/v1/ingest/etsy/next-job/').json()['job_id'], iq_etsy)
        self.assertEqual(iq_laptop.get('/api/v1/ingest/reverb/next-job/').json()['job_id'], iq_reverb)
        self.assertIsNone(iq_laptop.get('/api/v1/ingest/etsy/next-job/').json()['job_id'])
        self.assertEqual(amh_laptop.get('/api/v1/ingest/etsy/next-job/').json()['job_id'], amh)
        self.assertIsNone(amh_laptop.get('/api/v1/ingest/etsy/next-job/').json()['job_id'])

        rows = {j['id']: j for j in page_c.get('/api/v1/auto-bot/jobs/').json()['jobs']}
        self.assertEqual(rows[mms]['status'], 'pending')
        self.assertFalse(rows[mms]['has_laptop'])
        self.assertTrue(rows[amh]['has_laptop'])

        laptops = {l['label']: l for l in page_c.get('/api/v1/auto-bot/laptops/').json()['laptops']}
        self.assertEqual(laptops['worker-1']['stores'], ['IQ', 'IQR'])

    def test_queue_position_counts_only_the_same_store(self):
        first_iq = self.queue(self.page_b, rows=[['IQ', 'S1', '1', '1']], store_name='')
        amh = self.queue(self.page, rows=[['AMH', 'S1', '1', '1']], store_name='')
        second_iq = self.queue(self.page, rows=[['IQ', 'S2', '1', '1']], store_name='')
        rows = {j['id']: j for j in self.page.get('/api/v1/auto-bot/jobs/').json()['jobs']}
        self.assertEqual(rows[amh]['queue_position'], 1)
        self.assertEqual(rows[second_iq]['queue_position'], 2)
        self.assertTrue(first_iq)

    # --- validation and regression

    def test_upload_validation(self):
        missing_sku = self.page.post(
            '/api/v1/auto-bot/jobs/',
            {'job_type': 'etsy-stock', 'file': xlsx('a.xlsx', [['IQ', '1']], header=['store_name', 'price'])},
            format='multipart',
        )
        self.assertEqual(missing_sku.status_code, 400)
        self.assertIn('sku', missing_sku.json()['error'])
        no_price_or_qty = self.page.post(
            '/api/v1/auto-bot/jobs/',
            {'job_type': 'reverb-stock', 'file': xlsx('b.xlsx', [['GG', 'S']], header=['store_name', 'sku'])},
            format='multipart',
        )
        self.assertEqual(no_price_or_qty.status_code, 400)
        sku_job = self.page.post(
            '/api/v1/auto-bot/jobs/',
            {'job_type': 'reverb-sku', 'file': xlsx('c.xlsx', [['GG', 'OLD', 'NEW', '']],
                                                    header=['store_name', 'sku', 'new_sku', 'upc'])},
            format='multipart',
        )
        self.assertEqual(sku_job.status_code, 201)
        self.assertEqual(HebScrapeJob.objects.get(id=sku_job.json()['jobs'][0]['id']).vendor_code, 'reverb')
        bad_type = self.page.post(
            '/api/v1/auto-bot/jobs/', {'job_type': 'amazon-stock', 'file': xlsx('d.xlsx', [['a', 'b', 1, 1]])},
            format='multipart',
        )
        self.assertEqual(bad_type.status_code, 400)

    def test_heb_registry_and_routes_unchanged(self):
        self.assertEqual(set(SUPPORTED_VENDORS), {'heb', 'costco', 'vevor', 'costway'})
        _, raw_heb = make_token('heb-pc', ['heb'], self.user_a)
        resp = self.laptop(raw_heb).get('/api/v1/ingest/heb/next-job/')
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()['job_id'])
