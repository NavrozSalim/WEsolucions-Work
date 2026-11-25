"""Discovery: Stop between a product's retries, and eBay US Chrome tries counted per setup."""
import os
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import SimpleTestCase

from discovery import engine
from scrapers import ebay_chrome_profiles as profiles
from scrapers.ebay_common import EBAY_MARKET_US, _ebay_sk
from scrapers.stop_signal import raise_if_scrape_stopped


class _Flip:
    """stop(): False for the first ``after`` calls, then True."""

    def __init__(self, after):
        self.after = after
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.calls > self.after


class DiscoveryStopTests(SimpleTestCase):
    def test_ebay_stop_during_a_product_retry_cancels_the_job(self):
        stop = _Flip(after=1)  # the loop's own check passes, the retry check sees Stop

        def retrying(url, marketplace, state):
            raise_if_scrape_stopped()
            return '<html>never</html>', ''

        with patch.object(engine, '_load_ebay_product_html', side_effect=retrying), \
                patch.object(engine, 'stamp_identity', side_effect=lambda mp, row: row), \
                patch('scrapers.ebay_common.close_ebay_market_session'):
            with self.assertRaises(engine.DiscoveryCancelled):
                engine._fetch_ebay_products(
                    [{'url': 'https://www.ebay.com/itm/1'}], 'ebay_us', [], None, MagicMock(), stop,
                )

    def test_amazon_stop_during_a_product_retry_ends_the_page_without_an_error(self):
        stop = _Flip(after=0)
        errors = []

        def retrying(row, marketplace, local_errors, zip_code, browser_state):
            raise_if_scrape_stopped()
            local_errors.append('should not get here')
            return None

        with patch.object(engine, '_catalog_amazon_product', side_effect=retrying):
            with self.assertRaises(engine.DiscoveryCancelled):
                engine._fetch_amazon_products(
                    [{'url': 'https://www.amazon.com/dp/B000000001'}], 'amazon_us', errors, None,
                    MagicMock(), stop,
                )
        self.assertEqual(errors, [])


class DiscoveryEbayTryCountTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.state = {_ebay_sk(EBAY_MARKET_US, 'chrome_setup'): 'google'}

    def _stats(self):
        return profiles.setup_stats(['google'])['google']

    def test_loaded_try_is_logged_and_counted(self):
        with self.assertLogs('discovery.engine', level='INFO') as logs:
            engine._note_discovery_ebay_try(self.state, 0, True, '<html>ok</html>', '')
        self.assertIn('result=page loaded setup=google', logs.output[0])
        self.assertEqual(self._stats(), (1, 0))

    def test_blocked_try_is_counted_as_blocked(self):
        engine._note_discovery_ebay_try(self.state, 1, False, None, 'browser_blocked')
        self.assertEqual(self._stats(), (0, 1))

    def test_timeout_is_logged_but_not_counted(self):
        with self.assertLogs('discovery.engine', level='INFO') as logs:
            engine._note_discovery_ebay_try(self.state, 0, False, None, 'timeout')
        self.assertIn('result=timeout', logs.output[0])
        self.assertEqual(self._stats(), (0, 0))

    def test_discovery_counts_stay_apart_from_the_us_scraper(self):
        with patch.dict(os.environ, {'EBAY_US_CHROME_MIX_GROUP': 'discovery'}):
            profiles.record_setup_result('google', loaded=True)
            self.assertEqual(self._stats(), (1, 0))
        with patch.dict(os.environ, {'EBAY_US_CHROME_MIX_GROUP': ''}):
            self.assertEqual(self._stats(), (0, 0))


class DiscoveryAmazonUsProxyRotationTests(SimpleTestCase):
    @patch('scrapers.amazon_us_proxies.mark_success')
    @patch('scrapers.amazon_us_proxies.mark_blocked')
    @patch('scrapers.amazon_us_proxies.proxies_configured', return_value=True)
    @patch('scrapers.core.backoff_delay')
    def test_blocked_page_moves_to_the_next_us_proxy(self, _sleep, _configured, mark_blocked, mark_success):
        state = {'amazon_us_http_proxy': 'proxy-A', '_discovery_zip_set': True}
        answers = iter([('<html>robot check</html>', 'blocked_captcha'), ('<html>ok</html>', '')])

        def rotate(session_state, rotate=False):
            self.assertTrue(rotate)
            session_state['amazon_us_http_proxy'] = 'proxy-B'

        with patch.object(engine, '_amazon_http_once', side_effect=lambda *a: next(answers)), \
                patch('scrapers.amazon_us_scraper.AmazonHTTP._get_session', side_effect=rotate) as get_session:
            html, err = engine._load_amazon_product_html(
                'https://www.amazon.com/dp/B000000001', 'amazon_us', state, {}, '10001',
            )

        self.assertEqual((html, err), ('<html>ok</html>', ''))
        mark_blocked.assert_called_once_with('proxy-A')
        get_session.assert_called_once()
        mark_success.assert_called_once_with('proxy-B')
        self.assertNotIn('_discovery_zip_set', state)

    @patch('scrapers.amazon_us_proxies.mark_blocked')
    @patch('scrapers.amazon_us_proxies.proxies_configured', return_value=False)
    @patch('scrapers.core.backoff_delay')
    def test_without_us_proxies_nothing_rotates(self, _sleep, _configured, mark_blocked):
        answers = iter([('<html>robot</html>', 'blocked_captcha'), ('<html>ok</html>', '')])
        with patch.object(engine, '_amazon_http_once', side_effect=lambda *a: next(answers)), \
                patch.object(engine, '_amazon_requests_session', return_value=MagicMock()):
            html, err = engine._load_amazon_product_html(
                'https://www.amazon.com/dp/B000000001', 'amazon_us', {}, {}, '10001',
            )
        self.assertEqual(err, '')
        mark_blocked.assert_not_called()
