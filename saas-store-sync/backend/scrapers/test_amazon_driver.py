"""AmazonDriver prefers system Chromium/chromedriver over WebDriver Manager."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase


class AmazonDriverCreateTests(SimpleTestCase):
    @patch.dict(
        os.environ,
        {
            'CHROME_BIN': '/usr/bin/chromium',
            'CHROMEDRIVER_PATH': '/usr/bin/chromedriver',
        },
        clear=False,
    )
    @patch('selenium.webdriver.Chrome')
    @patch('selenium.webdriver.chrome.service.Service')
    @patch('os.path.isfile', return_value=True)
    def test_uses_system_chromedriver_when_configured(self, _isfile, mock_service, mock_chrome):
        from scrapers.amazon_us_scraper import AmazonDriver

        driver = MagicMock()
        mock_chrome.return_value = driver

        out = AmazonDriver.create()

        self.assertIs(out, driver)
        mock_service.assert_called_once_with(executable_path='/usr/bin/chromedriver')
        kwargs = mock_chrome.call_args.kwargs
        self.assertEqual(kwargs['options'].binary_location, '/usr/bin/chromium')
        driver.execute_cdp_cmd.assert_called()
