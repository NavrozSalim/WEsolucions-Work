"""Tests for order persist rules: processed status/data must not reset on fetch."""
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from marketplace.models import Marketplace
from stores.models import Store

from listings import order_service
from listings.models import MarketplaceOrder, OrderStatus
from listings.order_upsert import merge_order_defaults, persist_marketplace_order, resolve_shipping_status, resolve_status
from listings.mydeal import orders as mydeal_orders
from listings.reverb import orders as reverb_orders


class ResolveStatusTests(SimpleTestCase):
    def test_new_row_defaults_to_new(self):
        self.assertEqual(resolve_status(None, None), OrderStatus.NEW)

    def test_new_row_uses_incoming(self):
        self.assertEqual(resolve_status(None, OrderStatus.PAID), OrderStatus.PAID)

    def test_unknown_incoming_keeps_existing(self):
        self.assertEqual(resolve_status(OrderStatus.PAID, None), OrderStatus.PAID)
        self.assertEqual(resolve_status(OrderStatus.SHIPPING_SUBMITTED, None), OrderStatus.SHIPPING_SUBMITTED)

    def test_open_orders_do_not_move_backwards(self):
        self.assertEqual(resolve_status(OrderStatus.PAID, OrderStatus.NEW), OrderStatus.PAID)
        self.assertEqual(resolve_status(OrderStatus.SENT, OrderStatus.PAID), OrderStatus.SENT)

    def test_open_orders_can_move_forward(self):
        self.assertEqual(resolve_status(OrderStatus.NEW, OrderStatus.PAID), OrderStatus.PAID)
        self.assertEqual(resolve_status(OrderStatus.PAID, OrderStatus.SENT), OrderStatus.SENT)

    def test_processed_shipping_is_not_reset(self):
        self.assertEqual(
            resolve_status(OrderStatus.SHIPPING_SUBMITTED, OrderStatus.NEW),
            OrderStatus.SHIPPING_SUBMITTED,
        )
        self.assertEqual(
            resolve_status(OrderStatus.SHIPPING_SUBMITTED, OrderStatus.PAID),
            OrderStatus.SHIPPING_SUBMITTED,
        )
        self.assertEqual(
            resolve_status(OrderStatus.SHIPPING_SUBMITTED, OrderStatus.SENT),
            OrderStatus.SHIPPING_SUBMITTED,
        )
        self.assertEqual(
            resolve_status(OrderStatus.SHIPPING_COMPLETE, OrderStatus.SENT),
            OrderStatus.SHIPPING_COMPLETE,
        )

    def test_submitted_can_advance_to_complete(self):
        self.assertEqual(
            resolve_status(OrderStatus.SHIPPING_SUBMITTED, OrderStatus.SHIPPING_COMPLETE),
            OrderStatus.SHIPPING_COMPLETE,
        )

    def test_cancelled_is_not_reopened(self):
        self.assertEqual(resolve_status(OrderStatus.CANCELLED, OrderStatus.PAID), OrderStatus.CANCELLED)
        self.assertEqual(resolve_status(OrderStatus.REFUNDED, OrderStatus.NEW), OrderStatus.REFUNDED)

    def test_marketplace_terminal_wins(self):
        self.assertEqual(
            resolve_status(OrderStatus.SHIPPING_SUBMITTED, OrderStatus.CANCELLED),
            OrderStatus.CANCELLED,
        )
        self.assertEqual(
            resolve_status(OrderStatus.PAID, OrderStatus.REFUNDED),
            OrderStatus.REFUNDED,
        )

    def test_shipping_status_does_not_go_backwards(self):
        self.assertEqual(resolve_shipping_status("submitted", "pending"), "submitted")
        self.assertEqual(resolve_shipping_status("complete", "shipped"), "complete")
        self.assertEqual(resolve_shipping_status("submitted", "shipped"), "shipped")


class MergeDefaultsTests(SimpleTestCase):
    def test_processed_order_keeps_line_items_and_customer(self):
        existing = MarketplaceOrder(
            status=OrderStatus.SHIPPING_SUBMITTED,
            customer_info_json={"name": "Ada", "email": "ada@example.com"},
            line_items_json=[{"title": "Widget", "sku": "W1", "quantity": 1}],
            total_amount_cents=1999,
            invoice_number="INV-1",
            raw_response_json={"id": "1", "kept": True},
            shipping_status="submitted",
        )
        merged = merge_order_defaults(existing, {
            "status": OrderStatus.NEW,
            "shipping_status": "pending",
            "customer_info_json": None,
            "line_items_json": [],
            "total_amount_cents": None,
            "invoice_number": "INV-1",
            "raw_response_json": {"id": "1"},
        })
        self.assertEqual(merged["status"], OrderStatus.SHIPPING_SUBMITTED)
        self.assertEqual(merged["shipping_status"], "submitted")
        self.assertEqual(merged["customer_info_json"]["email"], "ada@example.com")
        self.assertEqual(merged["line_items_json"][0]["sku"], "W1")
        self.assertEqual(merged["total_amount_cents"], 1999)
        self.assertEqual(merged["raw_response_json"]["kept"], True)

    def test_open_order_keeps_customer_when_refetch_is_thin(self):
        existing = MarketplaceOrder(
            status=OrderStatus.PAID,
            customer_info_json={"name": "Ada", "email": "ada@example.com"},
            line_items_json=[{"title": "Widget"}],
            total_amount_cents=500,
            invoice_number="INV-2",
        )
        merged = merge_order_defaults(existing, {
            "status": OrderStatus.PAID,
            "customer_info_json": {},
            "line_items_json": [],
            "total_amount_cents": None,
        })
        self.assertEqual(merged["customer_info_json"]["email"], "ada@example.com")
        self.assertEqual(merged["line_items_json"][0]["title"], "Widget")
        self.assertEqual(merged["total_amount_cents"], 500)


class PersistOrderTests(TestCase):
    def _store(self, suffix="1"):
        User = get_user_model()
        user = User.objects.create_user(
            username=f"ordfreeze{suffix}",
            email=f"ordfreeze{suffix}@example.com",
            password="pw",
        )
        lasoo, _ = Marketplace.objects.get_or_create(code="lasoo", defaults={"name": "Lasoo"})
        store = Store.objects.create(
            user=user, name=f"Freeze {suffix}", region="AU", api_token="", marketplace=lasoo,
            management_mode="full_store", lasoo_environment="staging",
        )
        return user, store

    def test_lasoo_refetch_does_not_reset_processed_order(self):
        user, store = self._store("a")
        order_service._upsert_order(user, store, "staging", {
            "id": "9001",
            "invoiceNumber": "INV-9001",
            "status": "Paid",
            "totalCents": 2500,
            "customer": {"firstName": "Ada", "lastName": "Lovelace", "email": "ada@example.com"},
            "lineItems": [{"title": "Widget", "sku": "W1", "quantity": 1, "priceCents": 2500}],
        })
        order = MarketplaceOrder.objects.get(store=store, external_order_key="9001")
        order.status = OrderStatus.SHIPPING_SUBMITTED
        order.shipping_status = "submitted"
        order.save(update_fields=["status", "shipping_status", "updated_at"])

        order_service._upsert_order(user, store, "staging", {
            "id": "9001",
            "invoiceNumber": "INV-9001",
            "status": "Paid",
            "totalCents": 2500,
            "customer": {},
            "lineItems": [{"title": "Widget", "sku": "W1", "quantity": 1, "priceCents": 2500}],
        })
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.SHIPPING_SUBMITTED)
        self.assertEqual(order.shipping_status, "submitted")
        self.assertEqual(order.customer_info_json["email"], "ada@example.com")
        self.assertEqual(order.line_items_json[0]["sku"], "W1")

    def test_lasoo_unknown_status_does_not_become_new(self):
        user, store = self._store("b")
        order_service._upsert_order(user, store, "staging", {
            "id": "9002",
            "invoiceNumber": "INV-9002",
            "status": "Paid",
            "totalCents": 1000,
            "lineItems": [{"title": "Item", "sku": "S1", "quantity": 1, "priceCents": 1000}],
        })
        order_service._upsert_order(user, store, "staging", {
            "id": "9002",
            "invoiceNumber": "INV-9002",
            "status": "AWAITING_FULFILLMENT",
            "lineItems": [{"title": "Item", "sku": "S1", "quantity": 1, "priceCents": 1000}],
        })
        order = MarketplaceOrder.objects.get(store=store, external_order_key="9002")
        self.assertEqual(order.status, OrderStatus.PAID)

    def test_lasoo_maps_invoice_status_object(self):
        user, store = self._store("c")
        order = order_service._upsert_order(user, store, "staging", {
            "id": "9003",
            "invoiceNumber": "INV-9003",
            "invoiceStatus": {"name": "PAID"},
            "totalCents": 1000,
            "lineItems": [{"title": "Item", "sku": "S1", "quantity": 1, "priceCents": 1000}],
        })
        self.assertEqual(order.status, OrderStatus.PAID)

    def test_marketplace_refund_still_applies(self):
        user, store = self._store("d")
        order_service._upsert_order(user, store, "staging", {
            "id": "9004",
            "invoiceNumber": "INV-9004",
            "status": "Paid",
            "totalCents": 1000,
            "lineItems": [{"title": "Item", "sku": "S1", "quantity": 1, "priceCents": 1000}],
        })
        order = MarketplaceOrder.objects.get(store=store, external_order_key="9004")
        order.status = OrderStatus.SHIPPING_SUBMITTED
        order.save(update_fields=["status", "updated_at"])

        order_service._upsert_order(user, store, "staging", {
            "id": "9004",
            "invoiceNumber": "INV-9004",
            "status": "Refunded",
            "lineItems": [{"title": "Item", "sku": "S1", "quantity": 1, "priceCents": 1000}],
        })
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.REFUNDED)

    def test_reverb_processed_order_not_reset_to_paid(self):
        User = get_user_model()
        user = User.objects.create_user(username="rvfreeze", email="rvf@example.com", password="pw")
        reverb, _ = Marketplace.objects.get_or_create(code="reverb", defaults={"name": "Reverb"})
        store = Store.objects.create(
            user=user, name="Reverb Freeze", region="USA",
            api_token="reverb-token-1234567890", marketplace=reverb,
            management_mode="full_store",
        )
        raw = {
            "order_number": "RV-LOCK",
            "status": "paid",
            "sku": "PEDAL-1",
            "title": "Pedal",
            "quantity": 1,
            "total": {"amount_cents": 1000, "currency": "USD"},
            "amount_product_subtotal": {"amount_cents": 1000, "currency": "USD"},
            "buyer_name": "Buyer",
            "shipping_address": {
                "street_address": "1 St",
                "locality": "Austin",
                "region": "TX",
                "postal_code": "78701",
                "country_code": "US",
            },
        }
        order = reverb_orders.upsert_order(user, store, raw)
        order.status = OrderStatus.SHIPPING_COMPLETE
        order.shipping_status = "complete"
        order.save(update_fields=["status", "shipping_status", "updated_at"])

        raw["status"] = "paid"
        reverb_orders.upsert_order(user, store, raw)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.SHIPPING_COMPLETE)
        self.assertEqual(order.shipping_status, "complete")
        self.assertEqual(order.line_items_json[0]["sku"], "PEDAL-1")

    def test_persist_unknown_status_on_create_is_new(self):
        user, store = self._store("e")
        order, created = persist_marketplace_order(
            store=store,
            external_order_key="9005",
            environment="staging",
            defaults={"user": user, "status": None, "invoice_number": "INV-9005"},
        )
        self.assertTrue(created)
        self.assertEqual(order.status, OrderStatus.NEW)

    def test_mydeal_unknown_status_maps_to_none(self):
        self.assertIsNone(mydeal_orders.map_order_status(""))
        self.assertIsNone(mydeal_orders.map_order_status("all"))
        self.assertIsNone(mydeal_orders.map_order_status("UnknownState"))
        self.assertEqual(mydeal_orders.map_order_status("ReadytoFulfill"), OrderStatus.PAID)

    def test_reverb_unknown_status_maps_to_none(self):
        self.assertIsNone(reverb_orders.map_reverb_status(""))
        self.assertIsNone(reverb_orders.map_reverb_status("weird_status"))
        self.assertEqual(reverb_orders.map_reverb_status("paid"), OrderStatus.PAID)
