"""eBay US Chrome: Google Chrome switch and a saved profile per proxy."""
import os
import tempfile
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from scrapers import ebay_chrome_profiles as profiles
from scrapers.ebay_common import EBAY_MARKET_US, EbayDriver, _bind_us_profile, _ebay_sk


class ProxyProfileTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            'EBAY_US_PROFILE_PER_PROXY': '1',
            'EBAY_US_PROFILE_DIR': self.tmp.name,
            'EBAY_US_PROFILE_SLOTS': '2',
        })
        self.env.start()
        self.held = []

    def tearDown(self):
        for p in self.held:
            p.release()
        self.env.stop()
        self.tmp.cleanup()

    def _take(self, proxy):
        p = profiles.acquire_proxy_profile(proxy)
        if p:
            self.held.append(p)
        return p

    def test_switch_off_means_no_profile(self):
        with patch.dict(os.environ, {'EBAY_US_PROFILE_PER_PROXY': '0'}):
            self.assertIsNone(profiles.acquire_proxy_profile('http://u:p@1.2.3.4:8000'))

    def test_folder_name_never_contains_the_proxy_password(self):
        key = profiles.proxy_profile_key('http://user:s3cret@1.2.3.4:8000')
        self.assertNotIn('s3cret', key)
        self.assertNotIn('user', key)
        self.assertEqual(key, profiles.proxy_profile_key('http://other:pw@1.2.3.4:8000'))
        self.assertNotEqual(key, profiles.proxy_profile_key('http://user:s3cret@1.2.3.4:8001'))

    def test_one_chrome_per_profile_then_next_slot_then_none(self):
        first = self._take('http://1.2.3.4:8000')
        second = self._take('http://1.2.3.4:8000')
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(first.path, second.path)
        self.assertIsNone(self._take('http://1.2.3.4:8000'))

    def test_released_profile_is_reused(self):
        first = self._take('http://1.2.3.4:8000')
        path = first.path
        first.release()
        self.held.remove(first)
        again = self._take('http://1.2.3.4:8000')
        self.assertEqual(again.path, path)

    def test_other_proxy_gets_its_own_profile(self):
        a = self._take('http://1.2.3.4:8000')
        b = self._take('http://5.6.7.8:8000')
        self.assertNotEqual(os.path.basename(a.path)[:-2], os.path.basename(b.path)[:-2])

    def test_saved_cookies_seed_each_site_once(self):
        p = self._take('http://1.2.3.4:8000')
        self.assertFalse(p.seeded('www.ebay.com'))
        p.mark_seeded('www.ebay.com')
        self.assertTrue(p.seeded('www.ebay.com'))
        self.assertFalse(p.seeded('www.ebay.ca'))

    def test_leftover_chrome_lock_files_are_cleared(self):
        p = self._take('http://1.2.3.4:8000')
        path = p.path
        with open(os.path.join(path, 'SingletonLock'), 'w') as fh:
            fh.write('stale')
        p.release()
        self.held.remove(p)
        again = self._take('http://1.2.3.4:8000')
        self.assertFalse(os.path.exists(os.path.join(again.path, 'SingletonLock')))


class GoogleChromeSwitchTests(SimpleTestCase):
    def test_off_by_default(self):
        with patch.dict(os.environ, {'EBAY_US_GOOGLE_CHROME': '0'}):
            self.assertIsNone(profiles.google_chrome_paths())

    def test_missing_install_falls_back_to_chromium(self):
        with patch.dict(os.environ, {
            'EBAY_US_GOOGLE_CHROME': '1',
            'GOOGLE_CHROME_BIN': '/nope/google-chrome',
            'GOOGLE_CHROMEDRIVER_PATH': '/nope/chromedriver',
        }):
            self.assertIsNone(profiles.google_chrome_paths())

    def test_installed_paths_are_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            chrome = os.path.join(tmp, 'google-chrome')
            driver = os.path.join(tmp, 'chromedriver-google')
            for f in (chrome, driver):
                open(f, 'w').close()
            with patch.dict(os.environ, {
                'EBAY_US_GOOGLE_CHROME': '1', 'GOOGLE_CHROME_BIN': chrome, 'GOOGLE_CHROMEDRIVER_PATH': driver,
            }):
                self.assertEqual(profiles.google_chrome_paths(), (chrome, driver))


class DriverProfileBindingTests(SimpleTestCase):
    def test_quitting_chrome_frees_its_profile(self):
        profile = MagicMock()
        driver = MagicMock()
        original_quit = driver.quit
        _bind_us_profile(driver, profile)
        driver.quit()
        original_quit.assert_called_once()
        profile.release.assert_called_once()

    def test_profile_freed_even_when_quit_fails(self):
        profile = MagicMock()
        driver = MagicMock()
        driver.quit.side_effect = RuntimeError('chrome already gone')
        _bind_us_profile(driver, profile)
        with self.assertRaises(RuntimeError):
            driver.quit()
        profile.release.assert_called_once()

    @patch('scrapers.ebay_chrome_profiles.google_chrome_installed', return_value=True)
    @patch('scrapers.ebay_common.ebay_us_browser_first', return_value=True)
    @patch('scrapers.ebay_common.EbayDriver._us_proxy_url', return_value='http://1.2.3.4:8000')
    def test_us_chrome_launch_gets_profile_and_google_chrome(self, _proxy, _browser_first, _installed):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            'EBAY_US_PROFILE_PER_PROXY': '1', 'EBAY_US_PROFILE_DIR': tmp, 'EBAY_US_GOOGLE_CHROME': '1',
            'EBAY_US_CHROME_MIX': '',
        }), patch.object(EbayDriver, '_create_driver', return_value=MagicMock()) as create:
            session = {}
            driver = EbayDriver.get_or_create(session, EBAY_MARKET_US)
            kwargs = create.call_args.kwargs
            self.assertTrue(kwargs['google_chrome'])
            self.assertTrue(kwargs['profile_dir'].startswith(tmp))
            self.assertIsNotNone(driver._ebay_us_profile)
            self.assertEqual(session[_ebay_sk(EBAY_MARKET_US, 'chrome_setup')], 'google+profile')
            driver.quit()

    @patch('scrapers.ebay_common.ebay_us_browser_first', return_value=True)
    @patch('scrapers.ebay_common.EbayDriver._us_proxy_url', return_value='http://1.2.3.4:8000')
    def test_switches_off_keep_the_old_launch(self, _proxy, _browser_first):
        with patch.dict(os.environ, {'EBAY_US_PROFILE_PER_PROXY': '0', 'EBAY_US_GOOGLE_CHROME': '0'}), \
                patch.object(EbayDriver, '_create_driver', return_value=MagicMock()) as create:
            EbayDriver.get_or_create({}, EBAY_MARKET_US)
            kwargs = create.call_args.kwargs
            self.assertFalse(kwargs['google_chrome'])
            self.assertIsNone(kwargs['profile_dir'])


class ChromeSetupMixTests(SimpleTestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()

    def test_no_mix_follows_the_two_switches(self):
        with patch.dict(os.environ, {'EBAY_US_CHROME_MIX': '', 'EBAY_US_GOOGLE_CHROME': '0',
                                     'EBAY_US_PROFILE_PER_PROXY': '1'}):
            self.assertEqual(profiles.choose_chrome_setup(), profiles.ChromeSetup('chromium+profile', False, True))

    @patch('scrapers.ebay_chrome_profiles.google_chrome_installed', return_value=True)
    def test_all_means_every_setup(self, _installed):
        with patch.dict(os.environ, {'EBAY_US_CHROME_MIX': 'all'}):
            self.assertEqual(profiles.mix_setup_names(), list(profiles.SETUPS))

    @patch('scrapers.ebay_chrome_profiles.google_chrome_installed', return_value=False)
    def test_google_setups_left_out_without_google_chrome(self, _installed):
        with patch.dict(os.environ, {'EBAY_US_CHROME_MIX': 'all'}):
            self.assertEqual(profiles.mix_setup_names(), ['chromium+profile', 'chromium'])

    @patch('scrapers.ebay_chrome_profiles.google_chrome_installed', return_value=True)
    def test_unknown_names_are_ignored(self, _installed):
        with patch.dict(os.environ, {'EBAY_US_CHROME_MIX': 'google, firefox ,chromium,google'}):
            self.assertEqual(profiles.mix_setup_names(), ['google', 'chromium'])

    @patch('scrapers.ebay_chrome_profiles.google_chrome_installed', return_value=True)
    def test_even_mix_uses_every_setup(self, _installed):
        with patch.dict(os.environ, {'EBAY_US_CHROME_MIX': 'all', 'EBAY_US_CHROME_MIX_ADAPTIVE': '0'}):
            seen = {profiles.choose_chrome_setup().name for _ in range(200)}
        self.assertEqual(seen, set(profiles.SETUPS))

    def test_results_are_counted_per_setup(self):
        for _ in range(3):
            profiles.record_setup_result('google', loaded=True)
        profiles.record_setup_result('google', loaded=False)
        profiles.record_setup_result('chromium', loaded=False)
        self.assertEqual(profiles.setup_stats(['google', 'chromium']), {'google': (3, 1), 'chromium': (0, 1)})

    @patch('scrapers.ebay_chrome_profiles.google_chrome_installed', return_value=True)
    def test_adaptive_mix_prefers_the_winner_but_still_tries_others(self, _installed):
        for _ in range(60):
            profiles.record_setup_result('google', loaded=True)
            profiles.record_setup_result('chromium', loaded=False)
        for _ in range(20):
            profiles.record_setup_result('google', loaded=False)
            profiles.record_setup_result('chromium', loaded=True)
        with patch.dict(os.environ, {'EBAY_US_CHROME_MIX': 'google,chromium', 'EBAY_US_CHROME_MIX_ADAPTIVE': '1'}):
            picks = [profiles.choose_chrome_setup().name for _ in range(400)]
        self.assertGreater(picks.count('google'), 300)
        self.assertGreater(picks.count('chromium'), 0)

    @patch('scrapers.ebay_chrome_profiles.google_chrome_installed', return_value=True)
    @patch('scrapers.ebay_common.ebay_us_browser_first', return_value=True)
    @patch('scrapers.ebay_common.EbayDriver._us_proxy_url', return_value='http://1.2.3.4:8000')
    def test_busy_profile_is_logged_as_the_setup_without_profile(self, _proxy, _browser_first, _installed):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            'EBAY_US_CHROME_MIX': 'google+profile', 'EBAY_US_PROFILE_DIR': tmp, 'EBAY_US_PROFILE_SLOTS': '1',
        }), patch.object(EbayDriver, '_create_driver', side_effect=lambda **kw: MagicMock()) as create:
            first_session, second_session = {}, {}
            first = EbayDriver.get_or_create(first_session, EBAY_MARKET_US)
            EbayDriver.get_or_create(second_session, EBAY_MARKET_US)
            key = _ebay_sk(EBAY_MARKET_US, 'chrome_setup')
            self.assertEqual(first_session[key], 'google+profile')
            self.assertEqual(second_session[key], 'google')
            self.assertIsNone(create.call_args.kwargs['profile_dir'])
            first.quit()
