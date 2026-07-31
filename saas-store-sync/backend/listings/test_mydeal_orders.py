"""Unit tests for MyDeal order normalization + fulfillment payload."""
from django.test import SimpleTestCase
from types import SimpleNamespace

from listings.mydeal import orders as mydeal_orders


class MyDealOrdersNormalizeTests(SimpleTestCase):
    def test_normalize_line_items_and_money(self):
        raw = {
            "OrderId": 343544536,
            "OrderStatus": "ReadytoFulfill",
            "PurchaseDate": "2026-01-01T00:00:00Z",
            "SubTotalPrice": 50.0,
            "TotalPrice": 60.0,
            "TotalShippingPrice": 10.0,
            "Currency": "AUD",
            "CustomerEmail": "buyer@example.com",
            "ShippingAddress": {
                "FirstName": "Sam",
                "LastName": "Seller",
                "Phone": "0400000000",
                "Address1": "1 Test St",
                "Suburb": "Sydney",
                "State": "NSW",
                "PostalCode": "2000",
                "CountryCode": "AU",
            },
            "LineItems": [
                {
                    "OrderItemId": 368272200,
                    "SKU": "SKU-1",
                    "Quantity": 2,
                    "UnitPrice": 25.0,
                    "TotalPrice": 50.0,
                    "ProductId": 99,
                    "ProductTitle": "Widget",
                }
            ],
        }
        ui = mydeal_orders.to_ui_raw_shape(raw)
        self.assertEqual(ui["invoiceNumber"], "343544536")
        self.assertEqual(ui["totalCents"], 6000)
        self.assertEqual(ui["shippingCents"], 1000)
        self.assertEqual(ui["customer"]["email"], "buyer@example.com")
        self.assertEqual(ui["customer"]["shippingAddress"]["city"], "Sydney")
        self.assertEqual(len(ui["lineItems"]), 1)
        self.assertEqual(ui["lineItems"][0]["sku"], "SKU-1")
        self.assertEqual(ui["lineItems"][0]["priceCents"], 2500)
        self.assertEqual(ui["lineItems"][0]["lineItemId"], "368272200")

    def test_map_status(self):
        self.assertEqual(mydeal_orders.map_order_status("ReadytoFulfill"), "paid")
        self.assertEqual(mydeal_orders.map_order_status("Shipped"), "sent")
        self.assertEqual(mydeal_orders.map_shipping_status("Shipped"), "shipped")

    def test_cancel_reason_normalize(self):
        self.assertEqual(mydeal_orders.normalize_cancel_reason("Out of stock"), "OUT_OF_STOCK")
        self.assertEqual(mydeal_orders.normalize_cancel_reason("OUT_OF_STOCK"), "OUT_OF_STOCK")
        self.assertEqual(mydeal_orders.normalize_cancel_reason(""), "OUT_OF_STOCK")

    def test_fulfillment_payload(self):
        order = SimpleNamespace(
            external_order_key="343544536",
            line_items_json=[
                {"lineItemId": "368272200", "sku": "SKU-1", "quantity": 1},
            ],
        )
        body = mydeal_orders.build_fulfillment_payload(
            order,
            tracking_number="TRACK1",
            carrier="Australia Post",
            shipped_date="2026-01-02T00:00:00Z",
        )
        self.assertEqual(body["OrderId"], 343544536)
        self.assertEqual(len(body["FulfillmentItems"]), 1)
        self.assertEqual(body["FulfillmentItems"][0]["TrackingCode"], "TRACK1")
        self.assertEqual(body["FulfillmentItems"][0]["SKU"], "SKU-1")
