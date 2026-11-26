"""Amazon AU and eBay AU pages are priced for North Kellyville NSW 2155."""
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from scrapers import ebay_au_location as loc
from scrapers.amazon_au_scraper import (
    AmazonAUHTTP,
    amazon_au_location_forms,
    amazon_au_suburb,
    set_amazon_au_location,
)

ITEM = 'https://www.ebay.com.au/itm/358798106318'
NO_AU_ENV = {'AMAZON_AU_POSTCODE': '', 'AMAZON_AU_SUBURB': '', 'EBAY_AU_POSTCODE': '', 'EBAY_AU_SET_POSTCODE': ''}


def _reply(updated):
    return SimpleNamespace(status_code=200, json=lambda: {'isAddressUpdated': 1 if updated else 0})


class AmazonAuSuburbTests(SimpleTestCase):
    def test_default_is_north_kellyville_2155(self):
        with patch.dict(os.environ, NO_AU_ENV):
            forms = amazon_au_location_forms('2155')
        self.assertEqual(forms[0], {'locationType': 'POSTAL_CODE_WITH_CITY', 'zipCode': '2155', 'city': 'NORTH KELLYVILLE'})
        self.assertEqual(forms[1]['locationType'], 'LOCATION_INPUT')

    def test_order_postcode_goes_without_a_suburb(self):
        with patch.dict(os.environ, NO_AU_ENV):
            self.assertEqual(amazon_au_suburb('2603'), '')
            self.assertEqual(amazon_au_location_forms('2603'), [{'locationType': 'LOCATION_INPUT', 'zipCode': '2603'}])

    def test_other_postcode_with_its_own_suburb(self):
        with patch.dict(os.environ, {'AMAZON_AU_POSTCODE': '2000', 'AMAZON_AU_SUBURB': 'Sydney'}):
            self.assertEqual(amazon_au_suburb('2000'), 'SYDNEY')

    def test_other_postcode_without_suburb_lets_amazon_pick(self):
        with patch.dict(os.environ, {'AMAZON_AU_POSTCODE': '2000', 'AMAZON_AU_SUBURB': ''}):
            self.assertEqual(amazon_au_suburb('2000'), '')

    def test_suburb_is_sent_first_and_plain_postcode_is_the_fallback(self):
        post = MagicMock(side_effect=[_reply(False), _reply(True)])
        with patch.dict(os.environ, NO_AU_ENV):
            self.assertTrue(set_amazon_au_location(post, 'https://x/change', 'https://x/', '2155'))
        first, second = (c.kwargs['data'] for c in post.call_args_list)
        self.assertEqual((first['locationType'], first['city']), ('POSTAL_CODE_WITH_CITY', 'NORTH KELLYVILLE'))
        self.assertEqual(second['locationType'], 'LOCATION_INPUT')

    def test_http_session_sets_the_suburb(self):
        s = MagicMock()
        s.post.return_value = _reply(True)
        state = {}
        with patch.dict(os.environ, NO_AU_ENV):
            AmazonAUHTTP._ensure_zip(s, 'https://www.amazon.com.au/dp/B000000001', state, True)
        data = s.post.call_args.kwargs['data']
        self.assertEqual((data['zipCode'], data['city']), ('2155', 'NORTH KELLYVILLE'))
        self.assertTrue(state['amazon_au_http_zip_set'])


class _Client:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return SimpleNamespace(status_code=200)


class EbayAuDeliverToTests(SimpleTestCase):
    def test_client_tells_ebay_2155_once(self):
        client = _Client()
        with patch.dict(os.environ, NO_AU_ENV):
            loc.ensure_http_postcode(client, ITEM)
            loc.ensure_http_postcode(client, 'https://www.ebay.com.au/itm/257818458051')
        self.assertEqual(len(client.calls), 1)
        self.assertIn('/itemmodules/358798106318?', client.calls[0])
        self.assertIn('shipToCountryCode=AUS&shippingZipCode=2155', client.calls[0])

    def test_new_client_after_proxy_change_asks_again(self):
        first, second = _Client(), _Client()
        with patch.dict(os.environ, NO_AU_ENV):
            loc.ensure_http_postcode(first, ITEM)
            loc.ensure_http_postcode(second, ITEM)
        self.assertEqual((len(first.calls), len(second.calls)), (1, 1))

    def test_after_an_order_quote_the_next_page_sets_2155_again(self):
        client = _Client()
        with patch.dict(os.environ, NO_AU_ENV):
            loc.ensure_http_postcode(client, ITEM)
            loc.remember_postcode(client, '2603')
            loc.ensure_http_postcode(client, ITEM)
        self.assertEqual(len(client.calls), 2)

    def test_switch_off_and_other_sites(self):
        client = _Client()
        with patch.dict(os.environ, {**NO_AU_ENV, 'EBAY_AU_SET_POSTCODE': '0'}):
            loc.ensure_http_postcode(client, ITEM)
        with patch.dict(os.environ, NO_AU_ENV):
            loc.ensure_http_postcode(client, 'https://www.ebay.com/itm/358798106318')
            loc.ensure_http_postcode(client, 'https://www.ebay.com.au/b/Dog-Beds/20744')
        self.assertEqual(client.calls, [])

    def test_failed_call_is_not_repeated_before_every_item(self):
        client = MagicMock()
        client.get.side_effect = RuntimeError('proxy down')
        with patch.dict(os.environ, NO_AU_ENV):
            loc.ensure_http_postcode(client, ITEM)
            loc.ensure_http_postcode(client, ITEM)
        self.assertEqual(client.get.call_count, 1)

    def test_browser_asks_from_an_ebay_page(self):
        driver = MagicMock(current_url='https://www.ebay.com.au/')
        driver.execute_async_script.return_value = 200
        with patch.dict(os.environ, NO_AU_ENV):
            loc.ensure_driver_postcode(driver, ITEM)
            loc.ensure_driver_postcode(driver, ITEM)
        driver.execute_async_script.assert_called_once()
        self.assertIn('shippingZipCode=2155', driver.execute_async_script.call_args.args[1])

    def test_browser_not_on_ebay_waits(self):
        driver = MagicMock(current_url='data:,')
        with patch.dict(os.environ, NO_AU_ENV):
            loc.ensure_driver_postcode(driver, ITEM)
        driver.execute_async_script.assert_not_called()

    def test_custom_postcode(self):
        client = _Client()
        with patch.dict(os.environ, {**NO_AU_ENV, 'EBAY_AU_POSTCODE': '3000'}):
            loc.ensure_http_postcode(client, ITEM)
        self.assertIn('shippingZipCode=3000', client.calls[0])
