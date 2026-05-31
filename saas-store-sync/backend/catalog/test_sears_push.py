"""Tests for Sears pricing/inventory XML and marketplace push helpers."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from catalog.marketplace_rrp import adapter_push_kwargs, compute_marketplace_rrp
from catalog.reverb_catalog import store_is_sears
from store_adapters.sears_adapter import (
    SearsAPIError,
    SearsAdapter,
    build_inventory_feed_xml,
    build_pricing_feed_xml,
    parse_document_id,
    parse_processing_report_summary,
    processing_report_pending,
)


def _accepted_report(doc_id: str = '123456789') -> str:
    return (
        f'<?xml version="1.0"?><processing-report>'
        f'<document-id>{doc_id}</document-id>'
        '<report><summary>'
        '<records-accepted>1</records-accepted>'
        '<records-with-errors>0</records-with-errors>'
        '</summary></report></processing-report>'
    )


def _sears_request_side_effect(method, path, **kwargs):
    if method == 'PUT':
        return '<?xml version="1.0"?><document-id>123456789</document-id>'
    if method == 'GET' and '/processing-report/' in path:
        return _accepted_report()
    return ''


def _store(code: str):
    st = MagicMock()
    st.marketplace = MagicMock()
    st.marketplace.code = code
    st.marketplace.name = code.title()
    return st


class MarketplaceRrpTests(SimpleTestCase):
    def test_compute_rrp_discount_formula(self):
        self.assertEqual(compute_marketplace_rrp(Decimal('74'), Decimal('26')), Decimal('100.00'))

    def test_compute_rrp_returns_none_without_discount(self):
        self.assertIsNone(compute_marketplace_rrp(Decimal('50'), None))

    def test_compute_rrp_for_catalog_posted_price(self):
        """Posted $143.98 with ~28.72% RRP discount → Standard $201.99 on Sears."""
        rrp = compute_marketplace_rrp(Decimal('143.98'), Decimal('28.72'))
        self.assertEqual(rrp, Decimal('201.99'))

    def test_adapter_push_kwargs_quantizes_posted_price(self):
        store = _store('sears')
        pm = MagicMock()
        pm.product_id = 1
        pm.product.vendor_id = 'vid-1'
        ps = MagicMock()
        ps.mydeal_rrp_margin_percentage = Decimal('28.72')
        kwargs = adapter_push_kwargs(
            store,
            pm,
            Decimal('143.98'),
            3,
            price_by_vendor_id={'vid-1': ps},
        )
        self.assertEqual(kwargs['price'], Decimal('143.98'))
        self.assertEqual(kwargs['rrp'], Decimal('201.99'))

    def test_adapter_push_kwargs_adds_rrp_for_sears(self):
        store = _store('sears')
        pm = MagicMock()
        pm.product_id = 1
        pm.product.vendor_id = 'vid-1'
        ps = MagicMock()
        ps.mydeal_rrp_margin_percentage = Decimal('26')
        kwargs = adapter_push_kwargs(
            store,
            pm,
            74.0,
            5,
            price_by_vendor_id={'vid-1': ps},
            price_fallback=None,
        )
        self.assertEqual(kwargs['price'], Decimal('74.00'))
        self.assertEqual(kwargs['stock'], 5)
        self.assertEqual(kwargs['rrp'], Decimal('100.00'))

    def test_adapter_push_kwargs_skips_rrp_for_reverb(self):
        store = _store('reverb')
        pm = MagicMock()
        kwargs = adapter_push_kwargs(store, pm, 99.0, 1)
        self.assertEqual(kwargs, {'price': Decimal('99.00'), 'stock': 1})
        self.assertNotIn('rrp', kwargs)


class SearsXmlTests(SimpleTestCase):
    def test_pricing_xml_standard_and_sale(self):
        xml = build_pricing_feed_xml(
            'CHILD-1',
            standard_price='100.00',
            sale_price='74.00',
            sale_start_date=date(2026, 1, 1),
            sale_end_date=date(2027, 1, 1),
        )
        self.assertIn('item-id="CHILD-1"', xml)
        self.assertIn('<standard-price>100.00</standard-price>', xml)
        self.assertIn('<sale-price>74.00</sale-price>', xml)
        self.assertIn('<sale-start-date>2026-01-01</sale-start-date>', xml)
        self.assertIn('pricing-feed xmlns="http://seller.marketplace.sears.com/pricing/v6"', xml)

    def test_pricing_xml_standard_only_when_no_sale(self):
        xml = build_pricing_feed_xml('CHILD-2', standard_price='49.99')
        self.assertIn('<standard-price>49.99</standard-price>', xml)
        self.assertNotIn('<sale>', xml)

    def test_inventory_xml_lmp_quantity(self):
        xml = build_inventory_feed_xml(
            'CHILD-3',
            12,
            lmp=True,
            location_id='LOC-99',
            pick_up_now_eligible=False,
            inventory_timestamp='2026-05-30T12:00:00',
        )
        self.assertIn('item-id="CHILD-3"', xml)
        self.assertIn('<quantity>12</quantity>', xml)
        self.assertIn('location-id="LOC-99"', xml)
        self.assertIn('<pick-up-now-eligible>false</pick-up-now-eligible>', xml)
        self.assertIn('<inventory-timestamp>2026-05-30T12:00:00</inventory-timestamp>', xml)
        self.assertIn('<store-inventory xmlns="http://seller.marketplace.sears.com/catalog/v7"', xml)
        self.assertNotIn('<inventory-feed', xml)
        self.assertNotIn('<fbm-inventory>', xml)

    def test_inventory_xml_lmp_requires_location_id(self):
        with self.assertRaises(SearsAPIError):
            build_inventory_feed_xml('CHILD-3', 1, lmp=True)

    def test_inventory_xml_legacy_fbm(self):
        xml = build_inventory_feed_xml('CHILD-4', 5, lmp=False)
        self.assertIn('<fbm-inventory>', xml)
        self.assertIn('inventory-feed xmlns="http://seller.marketplace.sears.com/inventory/v7"', xml)
        self.assertNotIn('<store-inventory', xml)


class SearsProcessingReportTests(SimpleTestCase):
    def test_parse_document_id(self):
        body = '<?xml version="1.0"?><document-id>156721745210</document-id>'
        self.assertEqual(parse_document_id(body), '156721745210')
        self.assertIsNone(parse_document_id(''))

    def test_processing_report_pending_submitted(self):
        body = (
            '<?xml version="1.0"?><processing-report>'
            '<document-id>1</document-id><status>Submitted</status></processing-report>'
        )
        self.assertTrue(processing_report_pending(body))

    def test_processing_report_not_pending_when_summary_present(self):
        self.assertFalse(processing_report_pending(_accepted_report()))

    def test_parse_processing_report_summary(self):
        summary = parse_processing_report_summary(_accepted_report())
        self.assertEqual(summary['accepted'], 1)
        self.assertEqual(summary['errors'], 0)

    def test_parse_processing_report_rejection(self):
        body = (
            '<?xml version="1.0"?><processing-report><report><summary>'
            '<records-accepted>0</records-accepted>'
            '<records-with-errors>1</records-with-errors>'
            '</summary><detail><errors><error>'
            '<error-info>Invalid XML</error-info>'
            '</error></errors></detail></report></processing-report>'
        )
        summary = parse_processing_report_summary(body)
        self.assertEqual(summary['accepted'], 0)
        self.assertEqual(summary['errors'], 1)
        self.assertEqual(summary['error_infos'], ['Invalid XML'])

    @patch('store_adapters.sears_adapter.time.sleep')
    @patch.object(SearsAdapter, '_request')
    def test_wait_for_processing_report_polls_until_ready(self, mock_request, mock_sleep):
        responses = [
            '<?xml version="1.0"?><processing-report><document-id>1</document-id>'
            '<status>Submitted</status></processing-report>',
            _accepted_report('1'),
        ]

        def side_effect(method, path, **kwargs):
            if method == 'GET':
                return responses.pop(0)
            return ''

        mock_request.side_effect = side_effect
        adapter = SearsAdapter(MagicMock(api_token=(
            '{"seller_id":"123","email":"a@b.com","secret_key":"secretkeysecretkeysecretkey12"}'
        )))
        adapter._wait_for_processing_report('1')
        self.assertEqual(mock_request.call_count, 2)
        mock_sleep.assert_called_once()


class SearsAdapterPushTests(SimpleTestCase):
    def _adapter(self):
        store = MagicMock()
        store.api_token = (
            '{"seller_id":"123","email":"a@b.com","secret_key":"secretkeysecretkeysecretkey12",'
            '"location_id":"WH-1"}'
        )
        return SearsAdapter(store)

    @patch.object(SearsAdapter, '_request')
    def test_update_product_sends_price_and_inventory(self, mock_request):
        mock_request.side_effect = _sears_request_side_effect
        adapter = self._adapter()
        adapter.update_product('CHILD-99', price=Decimal('74.00'), rrp=Decimal('100.00'), stock=8)
        self.assertEqual(mock_request.call_count, 4)
        price_put = mock_request.call_args_list[0]
        price_report = mock_request.call_args_list[1]
        inv_put = mock_request.call_args_list[2]
        inv_report = mock_request.call_args_list[3]
        self.assertEqual(price_put.args[0], 'PUT')
        self.assertEqual(price_put.args[1], '/pricing/fbm/v6')
        self.assertIn('<standard-price>100.00</standard-price>', price_put.kwargs['data'])
        self.assertIn('<sale-price>74.00</sale-price>', price_put.kwargs['data'])
        self.assertEqual(price_report.args[0], 'GET')
        self.assertIn('/processing-report/', price_report.args[1])
        self.assertEqual(inv_put.args[1], '/inventory/fbm-lmp/v7')
        self.assertIn('<store-inventory xmlns="http://seller.marketplace.sears.com/catalog/v7"', inv_put.kwargs['data'])
        self.assertIn('location-id="WH-1"', inv_put.kwargs['data'])
        self.assertIn('<quantity>8</quantity>', inv_put.kwargs['data'])
        self.assertIn('/processing-report/', inv_report.args[1])

    @patch.object(SearsAdapter, '_request')
    def test_update_product_posted_only_without_rrp(self, mock_request):
        mock_request.side_effect = _sears_request_side_effect
        adapter = self._adapter()
        adapter.update_product('CHILD-100', price=Decimal('59.99'), stock=0)
        price_xml = mock_request.call_args_list[0].kwargs['data']
        self.assertIn('<standard-price>59.99</standard-price>', price_xml)
        self.assertNotIn('<sale>', price_xml)

    @patch.object(SearsAdapter, '_request')
    def test_update_product_succeeds_when_inventory_fails_after_pricing(self, mock_request):
        def side_effect(method, path, **kwargs):
            if method == 'PUT' and path == '/inventory/fbm-lmp/v7':
                raise SearsAPIError('Sears API PUT /inventory/fbm-lmp/v7: 403', status_code=403)
            return _sears_request_side_effect(method, path, **kwargs)

        mock_request.side_effect = side_effect
        adapter = self._adapter()
        result = adapter.update_product(
            'CHILD-101',
            price=Decimal('143.98'),
            rrp=Decimal('194.57'),
            stock=2,
        )
        self.assertTrue(result)
        self.assertIsNotNone(adapter.last_inventory_warning)
        self.assertIn('inventory not updated', adapter.last_inventory_warning.lower())
        self.assertEqual(mock_request.call_count, 3)
        self.assertEqual(mock_request.call_args_list[0].args[1], '/pricing/fbm/v6')
        self.assertIn('/processing-report/', mock_request.call_args_list[1].args[1])

    @patch.object(SearsAdapter, '_request')
    def test_update_pricing_raises_when_processing_report_rejects(self, mock_request):
        def side_effect(method, path, **kwargs):
            if method == 'PUT':
                return '<?xml version="1.0"?><document-id>999</document-id>'
            if method == 'GET':
                return (
                    '<?xml version="1.0"?><processing-report><report><summary>'
                    '<records-accepted>0</records-accepted>'
                    '<records-with-errors>1</records-with-errors>'
                    '</summary><detail><errors><error>'
                    '<error-info>Cannot update pricing</error-info>'
                    '</error></errors></detail></report></processing-report>'
                )
            return ''

        mock_request.side_effect = side_effect
        adapter = self._adapter()
        with self.assertRaises(SearsAPIError) as ctx:
            adapter.update_pricing('CHILD-REJ', standard_price='10.00')
        self.assertIn('feed rejected', str(ctx.exception).lower())

    @patch.object(SearsAdapter, '_request')
    def test_update_product_raises_when_inventory_fails_without_pricing(self, mock_request):
        mock_request.side_effect = SearsAPIError('Sears API PUT /inventory/fbm-lmp/v7: 403', status_code=403)
        adapter = self._adapter()
        with self.assertRaises(SearsAPIError):
            adapter.update_product('CHILD-102', stock=1)

    def test_lookup_uses_child_sku(self):
        adapter = self._adapter()
        self.assertEqual(adapter.lookup_listing_by_sku('CHILD-ABC'), 'CHILD-ABC')

    @patch.object(SearsAdapter, '_request')
    def test_legacy_fbm_inventory_uses_fbm_path(self, mock_request):
        mock_request.side_effect = _sears_request_side_effect
        store = MagicMock()
        store.api_token = (
            '{"seller_id":"123","email":"a@b.com","secret_key":"secretkeysecretkeysecretkey12",'
            '"inventory_program":"fbm"}'
        )
        adapter = SearsAdapter(store)
        adapter.update_inventory('CHILD-LEG', 3)
        self.assertEqual(mock_request.call_count, 2)
        self.assertEqual(mock_request.call_args_list[0].args[1], '/inventory/fbm/v7')
        self.assertIn('<fbm-inventory>', mock_request.call_args_list[0].kwargs['data'])
        self.assertIn('/processing-report/', mock_request.call_args_list[1].args[1])

    def test_store_is_sears(self):
        self.assertTrue(store_is_sears(_store('sears')))
