"""Partial refunds for marketplaces that expose a refund route."""
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from marketplace.models import Marketplace
from stores.models import Store

from . import order_service
from .errors import MarketplaceError
from .lasoo.client import LasooResult
from .models import MarketplaceOrder, OrderStatus
from .mydeal.client import MyDealResult


class PartialRefundTests(TestCase):
    def _store(self, code, **extra):
        User = get_user_model()
        user = User.objects.create_user(
            username=f"pr-{code}-{MarketplaceOrder.objects.count()}",
            email=f"pr-{code}-{MarketplaceOrder.objects.count()}@example.com",
            password="pw",
        )
        market, _ = Marketplace.objects.get_or_create(code=code, defaults={"name": code.title()})
        return Store.objects.create(
            user=user,
            name=f"{code} partial",
            region="AU" if code != "reverb" else "USA",
            api_token=extra.pop("api_token", "token-1234567890"),
            marketplace=market,
            management_mode="full_store",
            **extra,
        )

    def _order(self, store, **extra):
        defaults = {
            "user": store.user,
            "store": store,
            "external_order_key": "9001",
            "invoice_number": "9001",
            "status": OrderStatus.PAID,
            "total_amount_cents": 10000,
            "line_items_json": [
                {"lineItemId": 55, "title": "Thing", "quantity": 1, "priceCents": 10000, "totalCents": 10000, "sku": "SKU-1"},
            ],
            "environment": "staging",
        }
        defaults.update(extra)
        return MarketplaceOrder.objects.create(**defaults)

    @patch("listings.order_service.LasooClient")
    def test_lasoo_refunds_fifteen_percent_and_keeps_order_open(self, mock_client_cls):
        store = self._store("lasoo", api_token="", lasoo_environment="staging", lasoo_staging_auth_key="test-key")
        order = self._order(store, total_amount_cents=5000, line_items_json=[
            {"lineItemId": 55, "title": "Thing", "quantity": 2, "priceCents": 2500, "totalCents": 5000},
        ])
        mock_client = MagicMock()
        mock_client.auth_key = "test-key"
        mock_client.send.return_value = LasooResult(
            ok=True, data={"success": True, "results": {"success": True}}, message="Success.", status=200,
        )
        mock_client_cls.return_value = mock_client

        result = order_service.partial_refund(order, percent=15, reason="Pricing error")
        order.refresh_from_db()

        self.assertTrue(result["marketplace_ok"])
        self.assertEqual(result["refund_amount_cents"], 750)
        self.assertEqual(order.status, OrderStatus.PAID)
        self.assertEqual(order.refunded_amount_cents, 750)
        self.assertEqual(order.partial_refunds_json[0]["percent"], "15.00")
        _, payload = mock_client.send.call_args[0]
        self.assertEqual(payload["query"], "Refunds_Create")
        self.assertTrue(payload["data"]["partialRefund"])
        self.assertEqual(payload["data"]["refundAmountCents"], 750)
        item = payload["data"]["items"][0]
        self.assertEqual(item["itemId"], 55)
        self.assertEqual(item["salePriceCents"], 2500)
        self.assertEqual(item["purchasePricePerItemCents"], 2500)
        self.assertEqual(item["purchasedQuantity"], 2)
        self.assertEqual(item["refundQuantity"], 0)
        self.assertEqual(item["refundAmountCents"], 750)
        self.assertEqual(item["totalCents"], 5000)
        self.assertFalse(payload["data"]["refundPostage"])

    @patch("listings.order_service.LasooClient")
    def test_lasoo_partial_keeps_the_real_sale_price(self, mock_client_cls):
        store = self._store("lasoo", api_token="", lasoo_environment="production", lasoo_production_auth_key="test-key")
        order = self._order(
            store,
            external_order_key="1053373",
            invoice_number="1053373",
            status=OrderStatus.SHIPPING_SUBMITTED,
            total_amount_cents=35530,
            environment="production",
            line_items_json=[
                {
                    "lineItemId": 1059023,
                    "title": "Thing",
                    "quantity": 1,
                    "priceCents": 35530,
                    "totalCents": None,
                    "_raw": {
                        "id": 1059023,
                        "quantity": 1,
                        "originalPriceCents": 40000,
                        "salePriceCents": 35530,
                        "totalCents": 35530,
                    },
                },
            ],
        )
        mock_client = MagicMock()
        mock_client.auth_key = "test-key"
        mock_client.send.return_value = LasooResult(
            ok=True, data={"success": True, "results": {"success": True}}, message="Success.", status=200,
        )
        mock_client_cls.return_value = mock_client

        result = order_service.partial_refund(order, percent=50, reason="Change of mind")
        order.refresh_from_db()

        self.assertTrue(result["marketplace_ok"])
        self.assertEqual(order.status, OrderStatus.SHIPPING_SUBMITTED)
        self.assertEqual(order.refunded_amount_cents, 17765)
        _, payload = mock_client.send.call_args[0]
        item = payload["data"]["items"][0]
        self.assertEqual(payload["data"]["refundAmountCents"], 17765)
        self.assertEqual(item["itemId"], 1059023)
        self.assertEqual(item["purchasedQuantity"], 1)
        self.assertEqual(item["originalPricePerItemCents"], 40000)
        self.assertEqual(item["salePriceCents"], 35530)
        self.assertEqual(item["purchasePricePerItemCents"], 35530)
        self.assertEqual(item["refundAmountCents"], 17765)
        self.assertEqual(item["refundQuantity"], 0)

    @patch("listings.order_service.LasooClient")
    def test_lasoo_failure_does_not_record_a_refund(self, mock_client_cls):
        store = self._store("lasoo", api_token="", lasoo_environment="staging", lasoo_staging_auth_key="test-key")
        order = self._order(store)
        mock_client = MagicMock()
        mock_client.auth_key = "test-key"
        mock_client.send.return_value = LasooResult(
            ok=True,
            data={"success": True, "results": {"success": False, "message": "Rejected"}},
            message="Success.",
            status=200,
        )
        mock_client_cls.return_value = mock_client

        result = order_service.partial_refund(order, percent="15", reason="Other")
        order.refresh_from_db()
        self.assertFalse(result["ok"])
        self.assertEqual(order.status, OrderStatus.PAID)
        self.assertEqual(order.refunded_amount_cents, 0)

    @patch("listings.order_service.LasooClient")
    def test_later_cancel_refunds_only_the_remainder(self, mock_client_cls):
        store = self._store("lasoo", api_token="", lasoo_environment="staging", lasoo_staging_auth_key="test-key")
        order = self._order(store, total_amount_cents=5000, refunded_amount_cents=750, line_items_json=[
            {"lineItemId": 55, "title": "Thing", "quantity": 2, "priceCents": 2500, "totalCents": 5000},
        ])
        mock_client = MagicMock()
        mock_client.auth_key = "test-key"
        mock_client.send.return_value = LasooResult(
            ok=True, data={"success": True, "results": {"success": True}}, message="Success.", status=200,
        )
        mock_client_cls.return_value = mock_client

        order_service.cancel(order, reason="Out of stock")
        _, payload = mock_client.send.call_args[0]
        self.assertTrue(payload["data"]["partialRefund"])
        self.assertEqual(payload["data"]["refundAmountCents"], 4250)
        item = payload["data"]["items"][0]
        self.assertEqual(item["itemId"], 55)
        self.assertEqual(item["salePriceCents"], 2500)
        self.assertEqual(item["refundAmountCents"], 4250)
        self.assertEqual(item["refundQuantity"], 0)

    @patch("listings.reverb.orders.get_adapter")
    def test_reverb_sends_partial_amount_and_does_not_cancel(self, mock_get_adapter):
        store = self._store("reverb", api_token="reverb-token-1234567890")
        order = self._order(
            store,
            external_order_key="25137835",
            invoice_number="25137835",
            total_amount_cents=13500,
            environment="production",
            raw_response_json={"currency": "USD", "_reverb": {"total": {"amount_cents": 13500, "currency": "USD"}}},
        )
        adapter = MagicMock()
        adapter.create_seller_refund_request.return_value = {"id": 99, "state": "approved"}
        mock_get_adapter.return_value = adapter

        result = order_service.partial_refund(order, percent=15, reason="shipping_damage", note="Damaged corner")
        order.refresh_from_db()
        self.assertTrue(result["marketplace_ok"])
        self.assertEqual(order.status, OrderStatus.PAID)
        self.assertEqual(order.refunded_amount_cents, 2025)
        adapter.create_seller_refund_request.assert_called_once_with(
            "25137835",
            reason="shipping_damage",
            amount="20.25",
            currency="USD",
            state="approved",
            note_to_buyer="Damaged corner",
        )

    @patch("listings.mydeal.orders.MyDealClient")
    def test_mydeal_splits_percent_across_shipped_lines(self, mock_client_cls):
        store = self._store("mydeal", api_token="")
        order = self._order(
            store,
            status=OrderStatus.SENT,
            total_amount_cents=10000,
            line_items_json=[
                {"lineItemId": 11, "sku": "A", "quantity": 1, "totalCents": 6000},
                {"lineItemId": 12, "sku": "B", "quantity": 1, "totalCents": 2000},
            ],
        )
        client = MagicMock()
        client.refund_order.return_value = MyDealResult(ok=True, message="Complete")
        mock_client_cls.return_value = client

        result = order_service.partial_refund(order, percent=15, reason="Compensation")
        order.refresh_from_db()
        self.assertTrue(result["marketplace_ok"])
        self.assertEqual(order.status, OrderStatus.SENT)
        self.assertEqual(order.refunded_amount_cents, 1500)
        body = client.refund_order.call_args[0][1]
        amounts = [Decimal(str(item["RefundAmount"])) for item in body["Items"]]
        shipping = sum(Decimal(str(item.get("RefundShippingAmount") or 0)) for item in body["Items"])
        self.assertEqual(sum(amounts) + shipping, Decimal("15.00"))
        self.assertEqual(body["Items"][0]["Reason"], "COMPENSATION")
        self.assertGreater(shipping, 0)

    @patch("listings.mydeal.orders.MyDealClient")
    def test_mydeal_refuses_unshipped_orders(self, mock_client_cls):
        store = self._store("mydeal", api_token="")
        order = self._order(store, status=OrderStatus.PAID, shipping_status="pending")
        result = order_service.partial_refund(order, percent=15, reason="Compensation")
        self.assertFalse(result["ok"])
        mock_client_cls.assert_not_called()

    def test_rejects_full_percent_and_unsupported_marketplaces(self):
        store = self._store("lasoo", api_token="", lasoo_environment="staging", lasoo_staging_auth_key="test-key")
        order = self._order(store)
        with self.assertRaises(MarketplaceError):
            order_service.partial_refund(order, percent=100, reason="Other")
        with self.assertRaises(MarketplaceError):
            order_service.partial_refund(order, percent=0, reason="Other")

        etsy = self._store("etsy")
        etsy_order = self._order(etsy, external_order_key="etsy-1")
        with self.assertRaises(MarketplaceError):
            order_service.partial_refund(etsy_order, percent=15, reason="Other")

    def test_second_partial_cannot_finish_the_order(self):
        store = self._store("reverb", api_token="reverb-token-1234567890")
        order = self._order(store, total_amount_cents=10000, refunded_amount_cents=8500)
        with self.assertRaises(MarketplaceError):
            order_service.partial_refund(order, percent=15, reason="shipping_damage")
