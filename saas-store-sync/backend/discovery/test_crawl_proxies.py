"""Discovery's category and search pages go through the catalog proxy pools."""
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase

from discovery import engine
from discovery.crawl_proxies import CrawlProxy, use_crawl_proxy

OK_PAGE = '<html><body>results</body></html>'
BLOCKED_PAGE = '<html><body>Robot Check: enter the characters you see below</body></html>'


def _assignment(label):
    return SimpleNamespace(label=label, url=f'http://{label}', as_requests_proxy=lambda: {'http': f'http://{label}', 'https': f'http://{label}'})


class _FakeProxy:
    """Stands in for CrawlProxy: hands out proxies A, B, C... and records what happened."""

    def __init__(self, labels=('A', 'B', 'C', 'D')):
        self.marketplace = 'amazon_au'
        self.labels = list(labels)
        self.current = None
        self.rested = []
        self.worked_on = []

    def attach(self, session, rotate=False):
        if not self.labels:
            return False
        self.current = self.labels.pop(0)
        session.proxies = {'https': self.current}
        return True

    def blocked(self):
        self.rested.append(self.current)

    def worked(self):
        self.worked_on.append(self.current)


def _session_on_proxy():
    session = SimpleNamespace(proxies={}, headers={})
    proxy = _FakeProxy()
    proxy.attach(session)
    session._crawl_proxy = proxy
    return session, proxy


class CrawlSessionProxyTests(SimpleTestCase):
    @patch('scrapers.amazon_au_proxies.acquire_proxy', return_value=_assignment('1.2.3.4:80'))
    @patch('scrapers.amazon_au_proxies.proxies_configured', return_value=True)
    def test_new_crawl_session_takes_a_proxy_from_the_marketplace_pool(self, _configured, _acquire):
        session = SimpleNamespace(proxies={})
        use_crawl_proxy(session, 'amazon_au')
        self.assertEqual(session.proxies['https'], 'http://1.2.3.4:80')
        self.assertIsInstance(session._crawl_proxy, CrawlProxy)

    @patch('scrapers.ebay_us_proxies.proxies_configured', return_value=False)
    def test_without_a_pool_the_session_stays_on_the_server_ip(self, _configured):
        session = SimpleNamespace(proxies={})
        use_crawl_proxy(session, 'ebay_us')
        self.assertEqual(session.proxies, {})
        self.assertFalse(hasattr(session, '_crawl_proxy'))

    @patch('scrapers.amazon_au_proxies.proxies_configured', return_value=True)
    def test_switch_off(self, _configured):
        with patch.dict(os.environ, {'DISCOVERY_CRAWL_PROXIES': '0'}):
            self.assertFalse(CrawlProxy('amazon_au').configured())

    @patch('scrapers.ebay_au_proxies.acquire_proxy', return_value=_assignment('5.6.7.8:80'))
    @patch('scrapers.ebay_au_proxies.proxies_configured', return_value=True)
    def test_browser_session_for_ebay_au_uses_the_ebay_au_pool(self, _configured, acquire):
        session = engine._browser_session('ebay_au')
        self.assertEqual(session.proxies['https'], 'http://5.6.7.8:80')
        acquire.assert_called_once()


class CrawlFetchRotationTests(SimpleTestCase):
    def test_blocked_page_rests_the_proxy_and_uses_the_next(self):
        session, proxy = _session_on_proxy()
        with patch.object(engine, '_fetch_once', side_effect=[BLOCKED_PAGE, OK_PAGE]):
            self.assertEqual(engine._fetch('https://www.amazon.com.au/s?k=x', session), OK_PAGE)
        self.assertEqual(proxy.rested, ['A'])
        self.assertEqual(proxy.worked_on, ['B'])

    def test_refused_request_moves_to_the_next_proxy(self):
        session, proxy = _session_on_proxy()
        answers = [requests.RequestException('HTTP Error 403'), OK_PAGE]
        with patch.object(engine, '_fetch_once', side_effect=answers):
            self.assertEqual(engine._fetch('https://www.ebay.com.au/b/x', session), OK_PAGE)
        self.assertEqual(proxy.rested, ['A'])

    def test_missing_page_is_not_a_proxy_problem(self):
        session, proxy = _session_on_proxy()
        with patch.object(engine, '_fetch_once', side_effect=requests.RequestException('HTTP Error 404')):
            with self.assertRaises(requests.RequestException):
                engine._fetch('https://www.amazon.com.au/s?k=gone', session)
        self.assertEqual(proxy.rested, [])

    def test_gives_up_after_the_retries_and_returns_the_blocked_page(self):
        session, proxy = _session_on_proxy()
        with patch.dict(os.environ, {'DISCOVERY_CRAWL_PROXY_RETRIES': '2'}), \
                patch.object(engine, '_fetch_once', return_value=BLOCKED_PAGE) as fetch:
            self.assertEqual(engine._fetch('https://www.amazon.com.au/s?k=x', session), BLOCKED_PAGE)
        self.assertEqual(fetch.call_count, 3)
        self.assertEqual(proxy.rested, ['A', 'B'])

    def test_session_without_proxy_fetches_once_as_before(self):
        session = SimpleNamespace(proxies={}, headers={})
        with patch.object(engine, '_fetch_once', return_value=BLOCKED_PAGE) as fetch:
            self.assertEqual(engine._fetch('https://www.amazon.com.au/s?k=x', session), BLOCKED_PAGE)
        fetch.assert_called_once()

    def test_no_free_proxy_returns_what_it_has(self):
        session, proxy = _session_on_proxy()
        proxy.labels = []
        with patch.object(engine, '_fetch_once', return_value=BLOCKED_PAGE) as fetch:
            self.assertEqual(engine._fetch('https://www.amazon.com.au/s?k=x', session), BLOCKED_PAGE)
        fetch.assert_called_once()


class AmazonFetchWorkersSettingTests(SimpleTestCase):
    def test_default_is_three(self):
        with patch.dict(os.environ, {'DISCOVERY_AMAZON_FETCH_WORKERS': ''}):
            self.assertEqual(engine.amazon_fetch_workers(), 3)

    def test_setting_is_used_and_capped(self):
        for raw, expected in (('8', 8), ('0', 1), ('40', 16), ('lots', 3)):
            with patch.dict(os.environ, {'DISCOVERY_AMAZON_FETCH_WORKERS': raw}):
                self.assertEqual(engine.amazon_fetch_workers(), expected, raw)

    def test_product_pages_run_that_many_at_once(self):
        import threading
        import time

        running = []
        peak = [0]
        lock = threading.Lock()

        def slow_product(row, marketplace, local_errors, zip_code, browser_state):
            with lock:
                running.append(1)
                peak[0] = max(peak[0], len(running))
            time.sleep(0.05)
            with lock:
                running.pop()
            return {'asin': row['url'][-10:], 'title': 'ok'}

        rows = [{'url': f'https://www.amazon.com.au/dp/B0000000{i:02d}'} for i in range(24)]
        table = MagicMock()
        with patch.dict(os.environ, {'DISCOVERY_AMAZON_FETCH_WORKERS': '6'}), \
                patch.object(engine, '_catalog_amazon_product', side_effect=slow_product), \
                patch.object(engine, 'fill_image_columns'):
            engine._fetch_amazon_products(rows, 'amazon_au', [], None, table, lambda: False)
        self.assertEqual(peak[0], 6)
        self.assertEqual(table.add.call_count, 24)
