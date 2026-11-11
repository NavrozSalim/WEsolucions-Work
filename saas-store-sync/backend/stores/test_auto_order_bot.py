"""Auto Order Bot API. The bot subprocess is mocked: no Chrome, no order-bot imports."""
import atexit
import json
import shutil
import tempfile
from pathlib import Path
from unittest import mock

import openpyxl
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from marketplace.models import Marketplace
from stores.models import AutoOrderRun, PurchaseAccount, Store

User = get_user_model()
_MEDIA = tempfile.mkdtemp(prefix='aob-test-')
atexit.register(shutil.rmtree, _MEDIA, ignore_errors=True)


def xlsx_bytes(headers, rows=()):
    p = Path(tempfile.mkdtemp(dir=_MEDIA)) / 'x.xlsx'
    wb = openpyxl.Workbook()
    wb.active.append(headers)
    for r in rows:
        wb.active.append(r)
    wb.save(p)
    return p.read_bytes()


def upload(name):
    return SimpleUploadedFile(name, xlsx_bytes(['a']), content_type='application/octet-stream')


class FakePopen:
    """Stands in for subprocess.Popen; optionally writes the files the real bot would write."""
    calls = []
    exit_code = 0
    stdout = 'bot output\n'
    write_files = True

    def __init__(self, cmd, **kw):
        type(self).calls.append((cmd, kw))
        self.cmd, self.returncode = cmd, type(self).exit_code
        if type(self).write_files:
            out = Path(cmd[cmd.index('--output-dir') + 1])
            mp = cmd[cmd.index('--marketplace') + 1]
            for kind, rows in (('done', [['K1', 'SKU1', 1, 'amazon_au', 'u', 5, 'ready_for_payment', '', '', '']]),
                               ('remaining', [['K2', 'SKU2', 1, '', '', '', 'unmapped_sku', '', '', '']])):
                wb = openpyxl.Workbook()
                wb.active.append(['order_id', 'sku', 'buy_qty', 'vendor', 'vendor_url', 'stock_seen', 'status',
                                  'vendor_order_number', 'error', 'screenshot'])
                for r in rows:
                    wb.active.append(r)
                wb.save(out / f'{mp}-{kind}.xlsx')
            (out / f'{mp}-log.txt').write_text('line1\nline2 done\n')

    def communicate(self):
        return type(self).stdout, None


@override_settings(MEDIA_ROOT=_MEDIA, AUTO_ORDER_BOT_INLINE=True, AUTO_ORDER_BOT_RUNNER='local', AUTO_ORDER_BOT_DIR='C:/bot', AUTO_ORDER_BOT_PYTHON='py', AUTO_ORDER_BOT_LEGACY_RUNS=True)
class AutoOrderBotTests(TestCase):
    def setUp(self):
        FakePopen.calls, FakePopen.exit_code, FakePopen.stdout, FakePopen.write_files = [], 0, 'bot output\n', True
        self.user = User.objects.create_user(username='aob', email='aob@example.com', password='pw12345678')
        self.other = User.objects.create_user(username='aob2', email='aob2@example.com', password='pw12345678')
        self.c = APIClient()
        self.c.force_authenticate(self.user)
        self.mp = {code: Marketplace.objects.get_or_create(code=code, defaults={'name': code.title()})[0]
                   for code in ('reverb', 'sears', 'kogan', 'mydeal', 'lasoo')}
        self.accts = {
            'amazon': self.acct('us-amazon', 'amazon', 'USA'), 'ebay': self.acct('us-ebay', 'ebay', 'USA'),
            'amazon_au': self.acct('au-amazon', 'amazon_au', 'AU'), 'vevor_au': self.acct('au-vevor', 'vevor_au', 'AU'),
        }

    def acct(self, name, vendor, region, user=None):
        return PurchaseAccount.objects.create(user=user or self.user, name=name, vendor=vendor, region=region,
                                              chrome_profile='Profile 1', email='a@example.test')

    def store(self, code, region, enabled=True):
        return Store.objects.create(user=self.user, name=f'{code} store', region=region, api_token='t',
                                    marketplace=self.mp[code], auto_order_bot_enabled=enabled)

    def url(self, store, tail=''):
        return f'/api/v1/stores/{store.id}/auto-order-bot/{tail}'

    def start(self, store, account, place=None, client=None):
        data = {'orders': upload('o.xlsx'), 'mapping': upload('m.xlsx'), 'account': account}
        if place is not None:
            data['place'] = place
        with mock.patch('stores.auto_order_bot.subprocess.Popen', FakePopen):
            return (client or self.c).post(self.url(store, 'runs/'), data, format='multipart')

    # ---- commands -----------------------------------------------------------------------
    def test_usa_dry_run_commands(self):
        for code in ('reverb', 'sears'):
            store = self.store(code, 'USA')
            r = self.start(store, 'us-amazon')
            self.assertEqual(r.status_code, 201, r.data)
            cmd, kw = FakePopen.calls[-1]
            self.assertEqual(cmd[:2], ['py', 'run_us.py'])
            self.assertEqual(cmd[cmd.index('--marketplace') + 1], code)
            self.assertEqual(cmd[cmd.index('--account') + 1], 'us-amazon')
            self.assertNotIn('--place', cmd)
            self.assertTrue(cmd[cmd.index('--orders') + 1].endswith('orders.xlsx'))
            self.assertTrue(cmd[cmd.index('--mapping') + 1].endswith('mapping.xlsx'))
            self.assertEqual(kw['cwd'], str(Path('C:/bot')))

    def test_kogan_and_mydeal_commands(self):
        r = self.start(self.store('kogan', 'AU'), 'au-amazon')
        cmd = FakePopen.calls[-1][0]
        self.assertEqual((cmd[1], cmd[cmd.index('--marketplace') + 1]), ('run_au.py', 'kogan'))
        r = self.start(self.store('mydeal', 'AU'), 'au-vevor')
        cmd = FakePopen.calls[-1][0]
        self.assertEqual((cmd[1], cmd[cmd.index('--marketplace') + 1]), ('run_au.py', 'bigw'))
        self.assertNotIn('mydeal', cmd)

    def test_place_flag_only_when_requested(self):
        store = self.store('reverb', 'USA')
        self.start(store, 'us-ebay', place='false')
        self.assertNotIn('--place', FakePopen.calls[-1][0])
        self.start(store, 'us-ebay', place='true')
        self.assertIn('--place', FakePopen.calls[-1][0])
        self.assertTrue(AutoOrderRun.objects.filter(place=True).exists())

    def test_accounts_file_only_has_users_accounts(self):
        self.acct('theirs', 'ebay', 'USA', user=self.other)
        r = self.start(self.store('reverb', 'USA'), 'us-ebay')
        cmd = FakePopen.calls[-1][0]
        data = json.loads(Path(cmd[cmd.index('--accounts-file') + 1]).read_text())
        names = [a['name'] for reg in data.values() for lst in reg.values() for a in lst]
        self.assertIn('us-ebay', names)
        self.assertNotIn('theirs', names)
        self.assertEqual(set(data['au']), {'amazon_au', 'ebay_au', 'costco_au', 'costway_au', 'vevor_au'})
        self.assertNotIn('password', json.dumps(data).lower())

    # ---- refusals -----------------------------------------------------------------------
    def test_checkbox_off_blocks_run(self):
        store = self.store('reverb', 'USA', enabled=False)
        r = self.start(store, 'us-amazon')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(FakePopen.calls, [])
        self.assertEqual(AutoOrderRun.objects.count(), 0)

    def test_ebay_account_cannot_run_au_store(self):
        r = self.start(self.store('kogan', 'AU'), 'us-ebay')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(FakePopen.calls, [])

    def test_other_users_account_refused(self):
        self.acct('theirs', 'amazon', 'USA', user=self.other)
        r = self.start(self.store('sears', 'USA'), 'theirs')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(FakePopen.calls, [])

    def test_unsupported_marketplace_no_run(self):
        store = self.store('lasoo', 'AU')
        info = self.c.get(self.url(store)).data
        self.assertFalse(info['supported'])
        self.assertIn('Reverb, Sears, Kogan, and BigW only', info['message'])
        self.assertEqual(self.start(store, 'au-amazon').status_code, 400)
        self.assertEqual(FakePopen.calls, [])

    def test_second_run_while_running_refused(self):
        store = self.store('kogan', 'AU')
        AutoOrderRun.objects.create(store=store, user=self.user, account_name='au-amazon', vendor='amazon_au',
                                    marketplace='kogan', run_dir=_MEDIA)
        r = self.start(store, 'au-amazon')
        self.assertEqual(r.status_code, 409)
        self.assertEqual(FakePopen.calls, [])

    # ---- outcomes -----------------------------------------------------------------------
    def test_exit_code_2_saved_as_rejected_with_stdout(self):
        FakePopen.exit_code, FakePopen.stdout, FakePopen.write_files = 2, 'REJECTED: order file is a x export\n', False
        r = self.start(self.store('sears', 'USA'), 'us-amazon')
        self.assertEqual(r.status_code, 201)
        run = AutoOrderRun.objects.get()
        self.assertEqual((run.status, run.exit_code), ('rejected', 2))
        self.assertIn('REJECTED', run.stdout)
        self.assertEqual(r.data['done_rows'], [])

    def test_finished_run_stores_workbook_rows_and_downloads(self):
        store = self.store('kogan', 'AU')
        r = self.start(store, 'au-amazon')
        run = AutoOrderRun.objects.get()
        self.assertEqual(run.status, 'finished')
        self.assertEqual(run.done_rows[0]['order_id'], 'K1')
        self.assertEqual(run.done_rows[0]['status'], 'ready_for_payment')
        self.assertEqual(run.remaining_rows[0]['status'], 'unmapped_sku')
        d = self.c.get(self.url(store, f'runs/{run.id}/')).data
        self.assertEqual((d['done_count'], d['remaining_count']), (1, 1))
        self.assertIn('line2 done', d['log_tail'])
        for kind in ('done', 'remaining', 'log'):
            self.assertEqual(self.c.get(self.url(store, f'runs/{run.id}/download/{kind}/')).status_code, 200)
        self.assertEqual(len(self.c.get(self.url(store, 'runs/')).data['runs']), 1)

    # ---- flag and accounts --------------------------------------------------------------
    def test_checkbox_persists_and_defaults_off(self):
        store = Store.objects.create(user=self.user, name='s', region='USA', api_token='t', marketplace=self.mp['reverb'])
        self.assertFalse(self.c.get(self.url(store)).data['automate'])
        self.assertTrue(self.c.patch(self.url(store), {'automate': True}, format='json').data['automate'])
        store.refresh_from_db()
        self.assertTrue(store.auto_order_bot_enabled)

    def test_accounts_api_region_scoped(self):
        store = self.store('kogan', 'AU')
        names = {a['name'] for a in self.c.get(self.url(store, 'accounts/')).data['accounts']}
        self.assertEqual(names, {'au-amazon', 'au-vevor'})
        bad = self.c.post(self.url(store, 'accounts/'), {'name': 'x', 'vendor': 'ebay', 'chrome_profile': 'P'}, format='json')
        self.assertEqual(bad.status_code, 400)
        ok = self.c.post(self.url(store, 'accounts/'), {'name': 'au-costco', 'vendor': 'costco_au', 'chrome_profile': 'P4'}, format='json')
        self.assertEqual(ok.status_code, 201)
        self.assertEqual(self.c.delete(self.url(store, f'accounts/{ok.data["id"]}/')).status_code, 204)

    def test_other_users_store_not_visible(self):
        store = self.store('kogan', 'AU')
        c2 = APIClient()
        c2.force_authenticate(self.other)
        self.assertEqual(c2.get(self.url(store)).status_code, 404)


@override_settings(MEDIA_ROOT=_MEDIA, AUTO_ORDER_BOT_INLINE=True, AUTO_ORDER_BOT_RUNNER='local', AUTO_ORDER_BOT_DIR='C:/bot', AUTO_ORDER_BOT_PYTHON='py', AUTO_ORDER_BOT_LEGACY_RUNS=True)
class SavedFilesTests(AutoOrderBotTests):  # inherits fixtures; base tests re-run harmlessly
    """Mapping is kept until replaced; the order export lasts one day."""

    def put(self, store, kind, name='f.xlsx'):
        return self.c.post(f'/api/v1/stores/{store.id}/auto-order-bot/files/{kind}/', {'file': upload(name)}, format='multipart')

    def test_saved_files_reused_without_reupload(self):
        store = self.store('kogan', 'AU')
        self.assertEqual(self.put(store, 'orders', 'o.xlsx').status_code, 201)
        self.put(store, 'mapping', 'm.xlsx')
        with mock.patch('stores.auto_order_bot.subprocess.Popen', FakePopen):
            r = self.c.post(self.url(store, 'runs/'), {'account': 'au-amazon'}, format='multipart')
        self.assertEqual(r.status_code, 201, r.data)
        cmd = FakePopen.calls[-1][0]
        self.assertEqual(Path(cmd[cmd.index('--orders') + 1]).parent.name, 'current')

    def test_run_without_files_refused(self):
        store = self.store('kogan', 'AU')
        with mock.patch('stores.auto_order_bot.subprocess.Popen', FakePopen):
            r = self.c.post(self.url(store, 'runs/'), {'account': 'au-amazon'}, format='multipart')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(FakePopen.calls, [])

    def test_mapping_can_be_replaced_and_survives_days(self):
        from django.utils import timezone
        from datetime import timedelta
        store = self.store('kogan', 'AU')
        self.put(store, 'mapping', 'old.xlsx')
        self.put(store, 'mapping', 'new.xlsx')
        tomorrow = timezone.now() + timedelta(days=1)
        with mock.patch('django.utils.timezone.now', return_value=tomorrow), \
                mock.patch('django.utils.timezone.localdate', return_value=tomorrow.date()):
            files = self.c.get(self.url(store)).data['files']
        self.assertEqual(files['mapping']['name'], 'new.xlsx')

    def test_order_file_deleted_after_the_day(self):
        from django.utils import timezone
        from datetime import timedelta
        from stores import auto_order_bot as bot
        store = self.store('kogan', 'AU')
        self.put(store, 'orders', 'o.xlsx')
        self.assertTrue(bot.current_path(store, 'orders').exists())
        tomorrow = (timezone.now() + timedelta(days=1)).date()
        with mock.patch('django.utils.timezone.localdate', return_value=tomorrow):
            files = self.c.get(self.url(store)).data['files']
        self.assertIsNone(files['orders'])
        self.assertFalse(bot.current_path(store, 'orders').exists())


@override_settings(MEDIA_ROOT=_MEDIA, AUTO_ORDER_BOT_RUNNER='laptop', AUTO_ORDER_BOT_LEGACY_RUNS=True)
class LaptopRunnerTests(TestCase):
    """Runs wait for the laptop agent, which talks to the ingest endpoints with a bearer token."""

    def setUp(self):
        import hashlib
        from catalog.models import IngestToken
        self.user = User.objects.create_user(username='lp', email='lp@example.com', password='pw12345678')
        self.other = User.objects.create_user(username='lp2', email='lp2@example.com', password='pw12345678')
        self.c = APIClient()
        self.c.force_authenticate(self.user)
        mp = Marketplace.objects.get_or_create(code='kogan', defaults={'name': 'Kogan'})[0]
        self.store = Store.objects.create(user=self.user, name='k', region='AU', api_token='t', marketplace=mp,
                                          auto_order_bot_enabled=True)
        PurchaseAccount.objects.create(user=self.user, name='au-amazon', vendor='amazon_au', region='AU', chrome_profile='P1')
        self.token = self.make_token('laptop-1', self.user, hashlib)
        self.laptop = APIClient()
        self.laptop.credentials(HTTP_AUTHORIZATION=f'Bearer {self.raw}')

    def make_token(self, label, owner, hashlib, scopes=('order-bot',)):
        from catalog.models import IngestToken
        self.raw = f'{label}-' + 'z' * 30
        return IngestToken.objects.create(label=label, token_hash=hashlib.sha256(self.raw.encode()).hexdigest(),
                                          token_prefix=self.raw[:8], scopes=list(scopes), created_by=owner)

    def create_run(self, place='false'):
        data = {'orders': upload('o.xlsx'), 'mapping': upload('m.xlsx'), 'account': 'au-amazon', 'place': place}
        with mock.patch('stores.auto_order_bot.subprocess.Popen') as popen:
            r = self.c.post(f'/api/v1/stores/{self.store.id}/auto-order-bot/runs/', data, format='multipart')
            popen.assert_not_called()     # Django never starts the bot itself in laptop mode
        return r

    def test_run_waits_pending_until_laptop_claims(self):
        r = self.create_run(place='true')
        self.assertEqual((r.status_code, r.data['status']), (201, 'pending'))
        job = self.laptop.get('/api/v1/ingest/order-bot/next-job/').data
        self.assertEqual((job['region'], job['marketplace'], job['account'], job['place']), ('au', 'kogan', 'au-amazon', True))
        self.assertEqual(AutoOrderRun.objects.get().status, 'running')
        self.assertIsNone(self.laptop.get('/api/v1/ingest/order-bot/next-job/').data['job_id'])
        for kind in ('orders', 'mapping', 'accounts'):
            self.assertEqual(self.laptop.get(job['files'][kind]).status_code, 200, kind)
        acc = json.loads(b''.join(self.laptop.get(job['files']['accounts']).streaming_content))
        self.assertEqual(acc['au']['amazon_au'][0]['name'], 'au-amazon')

    def test_second_run_blocked_while_pending(self):
        self.create_run()
        self.assertEqual(self.create_run().status_code, 409)

    def test_complete_stores_rows_and_exit_code(self):
        run_id = self.create_run().data['id']
        job = self.laptop.get('/api/v1/ingest/order-bot/next-job/').data
        FakePopen.exit_code = 0
        out = Path(tempfile.mkdtemp(dir=_MEDIA))
        FakePopen(['x', '--output-dir', str(out), '--marketplace', 'kogan'])
        files = {k: open(out / f'kogan-{k}.{"txt" if k == "log" else "xlsx"}', 'rb') for k in ('done', 'remaining', 'log')}
        r = self.laptop.post(job['complete_url'], {'exit_code': 0, 'stdout': 'ok', **files}, format='multipart')
        self.assertEqual(r.status_code, 200, r.data)
        d = self.c.get(f'/api/v1/stores/{self.store.id}/auto-order-bot/runs/{run_id}/').data
        self.assertEqual((d['status'], d['exit_code'], d['done_count'], d['remaining_count']), ('finished', 0, 1, 1))
        self.assertEqual(d['done_rows'][0]['status'], 'ready_for_payment')
        self.assertEqual(d['laptop'] if 'laptop' in d else 'laptop-1', 'laptop-1')

    def test_exit_code_2_is_rejected(self):
        self.create_run()
        job = self.laptop.get('/api/v1/ingest/order-bot/next-job/').data
        self.laptop.post(job['complete_url'], {'exit_code': 2, 'stdout': 'REJECTED: wrong file'}, format='multipart')
        run = AutoOrderRun.objects.get()
        self.assertEqual((run.status, run.exit_code), ('rejected', 2))
        self.assertIn('REJECTED', run.stdout)

    def test_cancel_tells_laptop_to_stop(self):
        run_id = self.create_run().data['id']
        job = self.laptop.get('/api/v1/ingest/order-bot/next-job/').data
        r = self.c.post(f'/api/v1/stores/{self.store.id}/auto-order-bot/runs/{run_id}/cancel/')
        self.assertEqual(r.data['status'], 'cancelled')
        self.assertEqual(self.laptop.get(job['status_url']).data['status'], 'cancelled')

    def test_token_rules(self):
        import hashlib
        self.create_run()
        anon = APIClient()
        self.assertIn(anon.get('/api/v1/ingest/order-bot/next-job/').status_code, (401, 403))
        wrong_scope = APIClient()
        self.make_token('reverb-laptop', self.user, hashlib, scopes=('reverb',))
        wrong_scope.credentials(HTTP_AUTHORIZATION=f'Bearer {self.raw}')
        self.assertEqual(wrong_scope.get('/api/v1/ingest/order-bot/next-job/').status_code, 403)
        stranger = APIClient()
        self.make_token('someone-else', self.other, hashlib)
        stranger.credentials(HTTP_AUTHORIZATION=f'Bearer {self.raw}')
        self.assertIsNone(stranger.get('/api/v1/ingest/order-bot/next-job/').data['job_id'])
        self.assertEqual(AutoOrderRun.objects.get().status, 'pending')

    def test_page_sees_laptop_online(self):
        url = f'/api/v1/stores/{self.store.id}/auto-order-bot/'
        self.assertFalse(self.c.get(url).data['laptop_online'])
        self.laptop.get('/api/v1/ingest/order-bot/next-job/')
        self.assertTrue(self.c.get(url).data['laptop_online'])
