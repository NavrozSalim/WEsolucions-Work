import io
from datetime import date, datetime, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from openpyxl import load_workbook
from rest_framework.test import APIClient

from discovery import listing_rules as policy
from discovery.listing_export import (
    ETSY_COLUMNS,
    REVERB_COLUMNS,
    etsy_description,
    refusal_for,
    run_export,
)
from discovery.models import DiscoveryJob, DiscoveryProduct, ListingExport

User = get_user_model()

BUNNY = dict(
    BUNNY_STORAGE_ZONE='zone',
    BUNNY_STORAGE_ACCESS_KEY='key',
    BUNNY_CDN_URL='https://zone.b-cdn.net',
    BUNNY_STORAGE_HOST='storage.bunnycdn.com',
    BUNNY_STORAGE_FOLDER='listings',
)


def amazon_row(asin, **extra):
    row = {
        'asin': asin,
        'url': f'https://www.amazon.com/dp/{asin}',
        'title': 'Acme Snare Drum Head 14 Inch Coated',
        'brand': 'Acme',
        'price': 24.99,
        'rating': 4.6,
        'review_count': 120,
        'inventory': 30,
        'delivery_date': 'Tuesday, November 10',
        'free_delivery': 'Yes',
        'description': 'A coated snare head. Contact us at help@acme.com for support.',
        'bullets': 'WARM TONE: Coated film for a warm tone | 1 year warranty and lifetime support | Fits 14 inch snares',
        'category': 'Musical Instruments > Drums & Percussion > Drum Set Parts > Heads',
        'image-01': 'https://m.media-amazon.com/images/I/a.jpg',
        'image-02': 'https://m.media-amazon.com/images/I/b.jpg',
    }
    row.update(extra)
    return row


class PolicyRuleTests(TestCase):
    def test_stock_to_quantity(self):
        self.assertIsNone(policy.quantity_for_stock(6))
        self.assertIsNone(policy.quantity_for_stock(None))
        self.assertEqual(policy.quantity_for_stock(7), 1)
        self.assertEqual(policy.quantity_for_stock(9), 1)
        self.assertEqual(policy.quantity_for_stock(10), 2)
        self.assertEqual(policy.quantity_for_stock(29), 2)
        self.assertEqual(policy.quantity_for_stock(30), 5)
        self.assertEqual(policy.quantity_for_stock('More than 50 available'), 5)

    def test_reverb_rating_table(self):
        self.assertFalse(policy.reverb_rating_ok(3.4, 500))
        self.assertFalse(policy.reverb_rating_ok(3.7, 19))
        self.assertTrue(policy.reverb_rating_ok(3.7, 20))
        self.assertFalse(policy.reverb_rating_ok(4.2, 9))
        self.assertTrue(policy.reverb_rating_ok(4.2, 10))
        self.assertTrue(policy.reverb_rating_ok(4.7, 5))
        self.assertFalse(policy.reverb_rating_ok(4.7, 4))
        self.assertTrue(policy.reverb_rating_ok(5.0, 1))
        self.assertFalse(policy.reverb_rating_ok(5.0, 0))

    def test_etsy_rating(self):
        self.assertTrue(policy.etsy_rating_ok(4.0, 20))
        self.assertFalse(policy.etsy_rating_ok(3.9, 500))
        self.assertFalse(policy.etsy_rating_ok(4.8, 19))

    def test_delivery_dates(self):
        day = date(2026, 11, 6)
        self.assertEqual(policy.latest_delivery_date('Tuesday, November 10', day), date(2026, 11, 10))
        self.assertEqual(policy.latest_delivery_date('Nov 10 - 14', day), date(2026, 11, 14))
        self.assertEqual(policy.latest_delivery_date('Tue, Oct 27 – Mon, Nov 2', date(2026, 10, 20)), date(2026, 11, 2))
        self.assertEqual(policy.latest_delivery_date('Tomorrow, 7 AM - 11 AM', day), date(2026, 11, 7))
        self.assertEqual(policy.latest_delivery_date('January 3', date(2026, 12, 30)), date(2027, 1, 3))
        self.assertIsNone(policy.latest_delivery_date('', day))
        ok, reason = policy.free_delivery_within_week({'free_delivery': 'Yes', 'delivery_date': 'November 20'}, day)
        self.assertFalse(ok)
        self.assertIn('later', reason)

    def test_restricted_brand(self):
        self.assertEqual(policy.restricted_brand({'brand': 'Fender', 'title': 'Strap'}), 'Fender')
        self.assertEqual(policy.restricted_brand({'brand': 'Visit', 'title': 'Yamaha PSR keyboard'}), 'Yamaha')
        self.assertEqual(policy.restricted_brand({'brand': "D'Addario", 'title': 'Strings'}), "D'Addario")
        self.assertEqual(policy.restricted_brand({'brand': 'Acme', 'title': 'Guitar strap for Fender'}), '')

    def test_brand_and_contact_removed(self):
        self.assertEqual(policy.remove_brand('Acme® Snare Head', 'Acme'), 'Snare Head')
        self.assertEqual(
            policy.strip_contact_info('Great head. Contact us for a refund. Loud.'),
            'Great head. Loud.',
        )

    def test_etsy_tags(self):
        tags = policy.etsy_tags('Acme Snare Drum Head Coated Warm Tone', 'Instruments > Drum Heads', ['Warm tone coating'])
        self.assertEqual(len(tags), 13)
        self.assertEqual(len(set(tags)), 13)
        self.assertTrue(all(len(tag) <= 20 for tag in tags))
        self.assertFalse(any(any(ch.isdigit() for ch in tag) for tag in tags))

    def test_reverb_category_match(self):
        category, sub = policy.reverb_category(
            'Musical Instruments > Drums & Percussion > Drum Set Parts > Heads', 'Snare head',
        )
        self.assertEqual(category, 'Drums and Percussion')
        self.assertEqual(sub, 'Heads')
        self.assertEqual(policy.reverb_category('Kitchen > Cookware', 'Frying pan'), ('', ''))

    def test_etsy_description_has_headings_not_bullets(self):
        text = etsy_description({
            'description': 'Intro.',
            'title': 'T',
            'bullets': ['WARM TONE: Coated film', 'Made of durable steel'],
        })
        self.assertIn('Warm Tone\nCoated film', text)
        self.assertIn('Quality Materials\nMade of durable steel', text)
        self.assertNotIn('*', text)


@override_settings(**BUNNY)
class ListingExportTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='lister', email='lister@example.com', password='pass12345')
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.job = DiscoveryJob.objects.create(
            owner=self.user,
            marketplace='amazon_us',
            mode='product',
            status=DiscoveryJob.Status.SUCCEEDED,
            zip_code='10001',
            stats={'kept': 4},
        )
        DiscoveryJob.objects.filter(pk=self.job.pk).update(
            finished_at=datetime(2026, 11, 6, 12, tzinfo=dt_timezone.utc),
        )
        self.job.refresh_from_db()
        rows = [
            amazon_row('B000000001'),
            amazon_row('B000000002', brand='Fender', title='Fender strap'),
            amazon_row('B000000003', inventory=5),
            amazon_row('B000000004', rating=3.6, review_count=12),
        ]
        for row in rows:
            DiscoveryProduct.objects.create(job=self.job, product_key=row['asin'], data=row)
        self.copy = patch(
            'discovery.listing_export.bunny.copy_image',
            side_effect=lambda session, cfg, url, path: cfg.cdn(path),
        )
        self.copy.start()

    def tearDown(self):
        self.copy.stop()

    def _export(self, target, store_name=''):
        export = ListingExport.objects.create(job=self.job, owner=self.user, target=target, store_name=store_name)
        run_export(str(export.id))
        export.refresh_from_db()
        return export

    def test_reverb_file(self):
        export = self._export('reverb', 'P&P')
        self.assertEqual(export.status, 'succeeded', export.error_message)
        wb = load_workbook(io.BytesIO(bytes(export.result_bytes)))
        ws = wb['create']
        self.assertEqual([c.value for c in ws[1]], list(REVERB_COLUMNS))
        rows = [dict(zip(REVERB_COLUMNS, [c.value for c in r])) for r in ws.iter_rows(min_row=2)]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['store_name'], 'P&P')
        self.assertIsNone(row['sku'])
        self.assertIsNone(row['upc'])
        self.assertEqual(row['quantity'], '5')
        self.assertEqual(row['price'], '24.99')
        self.assertEqual(row['status'], 'Draft')
        self.assertEqual(row['free_shipping'], 'True')
        self.assertEqual(row['remove_branding'], 'Acme')
        self.assertNotIn('Acme', row['title'])
        self.assertNotIn('warranty', row['description'].lower())
        self.assertTrue(row['description'].startswith('- '))
        self.assertEqual(
            row['photos'].split('\n'),
            ['https://zone.b-cdn.net/listings/B000000001_1.jpg', 'https://zone.b-cdn.net/listings/B000000001_2.jpg'],
        )
        self.assertEqual((row['Category'], row['Subcategory']), ('Drums and Percussion', 'Heads'))
        skipped = {r[0].value: r[3].value for r in wb['Skipped'].iter_rows(min_row=2)}
        self.assertIn('Restricted brand', skipped['B000000002'])
        self.assertIn('Stock under 7', skipped['B000000003'])
        self.assertIn('Rating', skipped['B000000004'])

    def test_etsy_file(self):
        export = self._export('etsy')
        self.assertEqual(export.status, 'succeeded', export.error_message)
        ws = load_workbook(io.BytesIO(bytes(export.result_bytes)))['Sheet1']
        self.assertEqual([c.value for c in ws[1]], list(ETSY_COLUMNS))
        row = dict(zip(ETSY_COLUMNS, [c.value for c in ws[2]]))
        self.assertIsNone(row['sku'])
        self.assertEqual(len(row['tags'].split(', ')), 13)
        self.assertEqual(row['images'].count('; '), 1)
        self.assertEqual(row['when_made'], '2020–2026')
        self.assertEqual(row['category'], 'Heads')
        self.assertNotIn('help@acme.com', row['description'])

    def test_wrong_zip_refused(self):
        self.job.zip_code = '20001'
        self.assertEqual(refusal_for(self.job, 'reverb'), '')
        self.assertIn('10001', refusal_for(self.job, 'etsy'))

    def test_api_start_and_download(self):
        with patch('discovery.tasks.build_listing_export.delay') as delay:
            delay.return_value.id = 'task-9'
            response = self.client.post(
                f'/api/v1/discovery/jobs/{self.job.id}/listings/', {'target': 'reverb', 'store_name': 'P&P'}, format='json',
            )
        self.assertEqual(response.status_code, 201, response.data)
        export_id = response.data['id']
        delay.assert_called_once_with(export_id)
        run_export(export_id)
        jobs = self.client.get('/api/v1/discovery/jobs/').data
        self.assertEqual(jobs[0]['listing_exports']['reverb']['status'], 'succeeded')
        self.assertEqual(jobs[0]['listing_exports']['reverb']['stats']['listings'], 1)
        download = self.client.get(f'/api/v1/discovery/listing-exports/{export_id}/download/?format=xlsx')
        self.assertEqual(download.status_code, 200)
        self.assertIn('reverb-listings-amazon_us', download['Content-Disposition'])

    def test_api_refuses_category_job_and_missing_bunny(self):
        category = DiscoveryJob.objects.create(
            owner=self.user, marketplace='amazon_us', mode='category', status='succeeded', zip_code='10001',
        )
        response = self.client.post(f'/api/v1/discovery/jobs/{category.id}/listings/', {'target': 'etsy'}, format='json')
        self.assertEqual(response.status_code, 400)
        with override_settings(BUNNY_STORAGE_ZONE=''):
            response = self.client.post(f'/api/v1/discovery/jobs/{self.job.id}/listings/', {'target': 'etsy'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('BUNNY_STORAGE_ZONE', response.data['detail'])
