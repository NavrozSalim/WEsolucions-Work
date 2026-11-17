"""Reverb Ship: POST /api/my/orders/selling/{order}/ship and the provider list."""
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from marketplace.models import Marketplace
from store_adapters.reverb_adapter import ReverbAdapter, ReverbAPIError
from stores.models import Store

from . import shipping_service
from .errors import MarketplaceError
from .models import MarketplaceOrder, OrderStatus
from .reverb import orders as reverb_orders


class ReverbShippingTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username="reverb-ship", email="reverb-ship@example.com", password="pw",
        )
        market, _ = Marketplace.objects.get_or_create(code="reverb", defaults={"name": "Reverb"})
        self.store = Store.objects.create(
            user=user,
            name="reverb ship",
            region="USA",
            api_token="reverb-token-1234567890",
            marketplace=market,
            management_mode="full_store",
        )
        self.order = MarketplaceOrder.objects.create(
            user=user,
            store=self.store,
            external_order_key="25137835",
            invoice_number="25137835",
            status=OrderStatus.PAID,
            total_amount_cents=13500,
            environment="production",
        )

    @patch("listings.shopify.orders.push_fulfillment_to_shopify")
    @patch("listings.reverb.orders.get_adapter")
    def test_ship_sends_provider_and_tracking_and_marks_submitted(self, mock_get_adapter, _push):
        adapter = MagicMock()
        adapter.ship_order.return_value = {"message": "ok"}
        mock_get_adapter.return_value = adapter

        result = shipping_service.submit(self.order, tracking_number=" 1Z999 ", carrier="UPS")

        self.assertTrue(result["ok"])
        adapter.ship_order.assert_called_once_with(
            "25137835", provider="UPS", tracking_number="1Z999", send_notification=True,
        )
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.SHIPPING_SUBMITTED)
        self.assertEqual(self.order.shipping_status, "submitted")
        shipment = self.order.shipments.first()
        self.assertEqual(shipment.status, "submitted")
        self.assertEqual(shipment.tracking_number, "1Z999")

    @patch("listings.shopify.orders.push_fulfillment_to_shopify")
    @patch("listings.reverb.orders.get_adapter")
    def test_ship_rejected_by_reverb_leaves_order_open(self, mock_get_adapter, push):
        adapter = MagicMock()
        adapter.ship_order.side_effect = ReverbAPIError("Reverb API POST: 422 — provider is invalid")
        mock_get_adapter.return_value = adapter

        result = shipping_service.submit(self.order, tracking_number="123", carrier="Pigeon")

        self.assertFalse(result["ok"])
        self.assertIn("provider is invalid", result["message"])
        push.assert_not_called()
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.PAID)
        self.assertEqual(self.order.shipments.first().status, "failed")

    def test_ship_requires_tracking_number(self):
        with self.assertRaises(MarketplaceError):
            shipping_service.submit(self.order, tracking_number="  ", carrier="UPS")

    def test_complete_is_local_for_reverb(self):
        self.order.status = OrderStatus.SHIPPING_SUBMITTED
        self.order.shipping_status = "submitted"
        self.order.save()
        result = shipping_service.complete(self.order)
        self.assertTrue(result["ok"])
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.SHIPPING_COMPLETE)

    def test_normalize_shipping_providers_accepts_strings_and_objects(self):
        self.assertEqual(
            reverb_orders.normalize_shipping_providers({"shipping_providers": ["USPS", "UPS", "usps"]}),
            [{"code": "USPS", "name": "USPS"}, {"code": "UPS", "name": "UPS"}],
        )
        self.assertEqual(
            reverb_orders.normalize_shipping_providers(
                {"providers": [{"name": "FedEx"}, {"provider": "DHL", "display_name": "DHL Express"}]}
            ),
            [{"code": "FedEx", "name": "FedEx"}, {"code": "DHL", "name": "DHL Express"}],
        )
        self.assertEqual(reverb_orders.normalize_shipping_providers(None), [])

    @patch("listings.reverb.orders.get_adapter")
    def test_shipping_providers_falls_back_when_reverb_fails(self, mock_get_adapter):
        mock_get_adapter.return_value.list_shipping_providers.side_effect = ReverbAPIError("down")
        result = reverb_orders.shipping_providers(self.store)
        self.assertEqual(result["source"], "fallback")
        self.assertIn({"code": "USPS", "name": "USPS"}, result["carriers"])

    def test_adapter_ship_order_posts_documented_body(self):
        adapter = ReverbAdapter(self.store)
        response = MagicMock(status_code=200, text='{"ok": true}')
        response.json.return_value = {"ok": True}
        with patch.object(adapter._session, "request", return_value=response) as request:
            adapter.ship_order("25137835", provider="USPS", tracking_number="12345678")
        request.assert_called_once_with(
            "POST",
            "https://api.reverb.com/api/my/orders/selling/25137835/ship",
            json={"provider": "USPS", "tracking_number": "12345678", "send_notification": True},
            params=None,
            timeout=30,
        )
