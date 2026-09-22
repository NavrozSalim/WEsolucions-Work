"""Temu managed-store tests: signing, validate, publish, stock, orders, template."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from marketplace.models import Marketplace
from stores.models import Store

from listings import csv_import, listing_service, order_service, shipping_service
from listings.errors import MarketplaceError
from listings.models import ListingStatus, MarketplaceOrder, OrderStatus, StoreListing
from listings.temu import orders as temu_orders
from listings.temu import products as temu_products
from listings.temu.client import (
    DEFAULT_BASE_URL,
    TemuClient,
    TemuResult,
    authorize_url,
    extract_access_token,
    sign_payload,
)

VALID_TEMU = {
    "sku": "TEMU-TEE-M",
    "product_key": "TEMU-TEE",
    "title": "Cotton Tee",
    "description": "Soft cotton tee.",
    "brand": "ExampleBrand",
    "category": "30847",
    "warehouse_id": "WH-1",
    "image_urls": "https://example.com/a.jpg|https://example.com/b.jpg",
    "inventory": 5,
    "sale_price": "24.99",
}


def _store_ns(**overrides):
    base = {
        "name": "Temu AU",
        "temu_region": "au",
        "temu_base_url": "",
        "temu_app_key": "app-key",
        "temu_app_secret": "app-secret",
        "temu_access_token": "token-123",
        "temu_mall_id": "",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _listing_ns(**overrides):
    data = {**VALID_TEMU, **overrides}
    extras = temu_products.build_extras(data)
    return SimpleNamespace(
        sku=data["sku"],
        title=data["title"],
        description=data["description"],
        brand=data["brand"],
        category=data["category"],
        barcode=data.get("barcode") or "",
        image_urls=data["image_urls"],
        variation_image_url=data.get("variation_image_url") or "",
        inventory=data["inventory"],
        infinite_quantity=bool(data.get("infinite_quantity")),
        sale_price=Decimal(str(data["sale_price"])),
        original_price=Decimal(str(data.get("original_price") or data["sale_price"])),
        sale_price_cents=2499,
        original_price_cents=2499,
        external_product_key=data.get("product_key") or "",
        external_variant_key=data.get("variant_key") or data["sku"],
        option_1_name=data.get("option_1_name") or "",
        option_1_value=data.get("option_1_value") or "",
        option_2_name=data.get("option_2_name") or "",
        option_2_value=data.get("option_2_value") or "",
        option_3_name=data.get("option_3_name") or "",
        option_3_value=data.get("option_3_value") or "",
        external_data_object_json=extras,
    )


class TemuSignatureTests(SimpleTestCase):
    def test_sign_is_uppercase_md5_wrapped_with_secret(self):
        import hashlib

        params = {"type": "bg.local.goods.list.query", "app_key": "k", "timestamp": "1"}
        expected = hashlib.md5(
            b"secretapp_keyktimestamp1typebg.local.goods.list.querysecret"
        ).hexdigest().upper()
        self.assertEqual(sign_payload(params, "secret"), expected)

    def test_sign_ignores_existing_sign_field(self):
        params = {"app_key": "k", "timestamp": "1"}
        with_sign = {**params, "sign": "STALE"}
        self.assertEqual(sign_payload(params, "s"), sign_payload(with_sign, "s"))

    def test_sign_serializes_nested_values_as_compact_json(self):
        one = sign_payload({"skuList": [{"a": 1}]}, "s")
        two = sign_payload({"skuList": '[{"a":1}]'}, "s")
        self.assertEqual(one, two)

    def test_body_uses_au_global_router_and_required_common_params(self):
        client = TemuClient(_store_ns())
        self.assertEqual(client.router_url, f"{DEFAULT_BASE_URL}/openapi/router")
        self.assertNotIn("openapi-b-us", client.router_url)
        self.assertNotIn("openapi-b-eu", client.router_url)
        body = client.build_body("bg.local.goods.list.query", {"pageNo": 1})
        for key in ("type", "app_key", "access_token", "timestamp", "data_type", "sign"):
            self.assertIn(key, body)
        self.assertEqual(body["data_type"], "JSON")
        self.assertEqual(len(body["timestamp"]), 10)
        self.assertEqual(body["sign"], sign_payload({k: v for k, v in body.items() if k != "sign"}, "app-secret"))

    def test_missing_credentials_raise_marketplace_error(self):
        with self.assertRaises(MarketplaceError):
            TemuClient(_store_ns(temu_access_token=""))

    def test_authorize_url_points_at_au_seller_center(self):
        url = authorize_url("k", "https://hub.example.com/cb", "store-1")
        self.assertTrue(url.startswith("https://au.seller.temu.com/open-platform/client-manage/authorization?"))
        self.assertIn("appKey=k", url)
        self.assertIn("state=store-1", url)

    def test_extract_access_token_reads_camel_case(self):
        token, mall = extract_access_token({"accessToken": "abc", "mallId": "77"})
        self.assertEqual((token, mall), ("abc", "77"))


class TemuClientResponseTests(SimpleTestCase):
    def _call(self, payload, status=200):
        client = TemuClient(_store_ns())
        response = SimpleNamespace(
            status_code=status,
            json=lambda: payload,
            text="",
            content=b"{}",
        )
        with patch("listings.temu.client.requests.post", return_value=response):
            return client.call("bg.local.goods.list.query")

    def test_success_code_unwraps_result(self):
        result = self._call({"success": True, "errorCode": 1000000, "result": {"goodsList": []}})
        self.assertTrue(result.ok)
        self.assertEqual(result.data, {"goodsList": []})

    def test_business_error_on_http_200_is_not_ok(self):
        result = self._call({"success": False, "errorCode": 4000001, "errorMsg": "catId invalid"})
        self.assertFalse(result.ok)
        self.assertIn("catId invalid", result.message)
        self.assertIn("4000001", result.message)

    def test_token_error_is_flagged_as_auth_error(self):
        result = self._call({"success": False, "errorCode": 3000000, "errorMsg": "access_token invalid"})
        self.assertTrue(result.is_auth_error)

    def test_network_failure_returns_readable_message(self):
        import requests

        client = TemuClient(_store_ns())
        with patch("listings.temu.client.requests.post", side_effect=requests.RequestException("boom")):
            result = client.call("bg.local.goods.list.query")
        self.assertFalse(result.ok)
        self.assertIn("Could not reach", result.message)


class TemuValidateTests(SimpleTestCase):
    def test_valid_row_has_no_errors(self):
        self.assertEqual(temu_products.validate_listing(VALID_TEMU), [])

    def test_missing_core_fields_are_reported(self):
        errors = temu_products.validate_listing({})
        blob = " ".join(errors)
        self.assertIn("SKU is required", blob)
        self.assertIn("Title is required", blob)
        self.assertIn("Category ID", blob)
        self.assertIn("Warehouse ID", blob)

    def test_non_numeric_category_is_rejected(self):
        errors = temu_products.validate_listing({**VALID_TEMU, "category": "Apparel"})
        self.assertTrue(any("Category ID" in e for e in errors))

    def test_zero_price_is_rejected(self):
        errors = temu_products.validate_listing({**VALID_TEMU, "sale_price": "0"})
        self.assertTrue(any("Price must be greater than 0" in e for e in errors))

    def test_partial_option_pair_is_rejected(self):
        errors = temu_products.validate_listing(
            {**VALID_TEMU, "option_1_name": "Size", "option_1_value": ""}
        )
        self.assertTrue(any("Option name and value" in e for e in errors))

    def test_duplicate_sku_flags_both_rows(self):
        rows = [
            {"sku": "A", "row_number": 2},
            {"sku": "B", "row_number": 3},
            {"sku": "A", "row_number": 4},
        ]
        errors = temu_products.duplicate_child_sku_errors(rows)
        self.assertEqual(set(errors), {0, 2})
        self.assertIn("rows 2 and 4", errors[0])


class TemuPayloadTests(SimpleTestCase):
    def test_sku_payload_uses_out_sku_sn_and_minor_units(self):
        payload = temu_products.sku_payload(_listing_ns())
        self.assertEqual(payload["outSkuSn"], "TEMU-TEE-M")
        self.assertEqual(payload["basePrice"], 2499)
        self.assertEqual(payload["quantity"], 5)
        self.assertEqual(payload["skuStockList"][0]["warehouseId"], "WH-1")

    def test_sku_payload_requires_warehouse(self):
        listing = _listing_ns()
        listing.external_data_object_json = temu_products.build_extras(
            {**VALID_TEMU, "warehouse_id": ""}
        )
        with self.assertRaises(MarketplaceError):
            temu_products.sku_payload(listing)

    def test_goods_payload_groups_variants_under_one_parent(self):
        rows = [
            _listing_ns(sku="TEMU-TEE-M", option_1_name="Size", option_1_value="M"),
            _listing_ns(sku="TEMU-TEE-L", option_1_name="Size", option_1_value="L"),
        ]
        packed = temu_products.group_by_parent(rows)
        self.assertEqual(len(packed), 1)
        payload, members = packed[0]
        self.assertEqual(payload["outGoodsSn"], "TEMU-TEE")
        self.assertEqual(payload["catId"], 30847)
        self.assertEqual(len(payload["skuList"]), 2)
        self.assertEqual(len(members), 2)

    def test_goods_payload_requires_images(self):
        listing = _listing_ns(image_urls="")
        with self.assertRaises(MarketplaceError):
            temu_products.goods_payload([listing])


class TemuOrderMappingTests(SimpleTestCase):
    def test_status_mapping_handles_names_and_codes(self):
        self.assertEqual(temu_orders.map_order_status("UNSHIPPED"), OrderStatus.PAID)
        self.assertEqual(temu_orders.map_order_status("SHIPPED"), OrderStatus.SENT)
        self.assertEqual(temu_orders.map_order_status(5), OrderStatus.CANCELLED)
        self.assertIsNone(temu_orders.map_order_status(""))

    def test_shipping_status_mapping(self):
        self.assertEqual(temu_orders.map_shipping_status("SHIPPED"), "shipped")
        self.assertEqual(temu_orders.map_shipping_status("CANCELED"), "cancelled")
        self.assertEqual(temu_orders.map_shipping_status("UNSHIPPED"), "pending")

    def test_ui_shape_flattens_parent_and_child_orders(self):
        raw = {
            "parentOrderSn": "PO-1",
            "parentOrderStatus": "UNSHIPPED",
            "parentOrderPaidAmount": 4998,
            "receiverName": "Jane Smith",
            "orderList": [
                {
                    "orderSn": "CO-1",
                    "outSkuSn": "TEMU-TEE-M",
                    "goodsName": "Cotton Tee",
                    "quantity": 2,
                    "originalOrderPrice": 2499,
                }
            ],
        }
        address = {"line1": "1 Test St", "city": "Sydney", "postcode": "2000", "country": "AU"}
        ui = temu_orders.to_ui_raw_shape(raw, address)
        self.assertEqual(ui["invoiceNumber"], "PO-1")
        self.assertEqual(ui["totalCents"], 4998)
        self.assertEqual(ui["lineItems"][0]["sku"], "TEMU-TEE-M")
        self.assertEqual(ui["lineItems"][0]["lineItemId"], "CO-1")
        self.assertEqual(ui["customer"]["name"], "Jane Smith")
        self.assertEqual(ui["customer"]["shippingAddress"]["postcode"], "2000")

    def test_shipment_payload_carries_child_order_lines(self):
        order = SimpleNamespace(
            external_order_key="PO-1",
            invoice_number="PO-1",
            line_items_json=[{"lineItemId": "CO-1", "quantity": 2}],
        )
        payload = temu_orders.build_shipment_payload(
            order, tracking_number="TRK1", ship_company_id="9"
        )
        self.assertEqual(payload["parentOrderSn"], "PO-1")
        package = payload["sendRequestList"][0]
        self.assertEqual(package["trackingNumber"], "TRK1")
        self.assertEqual(package["shipCompanyId"], "9")
        self.assertEqual(package["packageDetailList"][0]["orderSn"], "CO-1")

    def test_cancel_reasons_are_out_of_stock_based(self):
        reasons = temu_orders.cancel_reasons()
        self.assertTrue(reasons["ok"])
        self.assertEqual(reasons["marketplace"], "temu")
        self.assertIn("OUT_OF_STOCK", [r["value"] for r in reasons["reasons"]])


class TemuAdapterGuardTests(SimpleTestCase):
    def test_get_adapter_refuses_temu_instead_of_reverb_fallback(self):
        from store_adapters import get_adapter

        store = SimpleNamespace(
            marketplace=SimpleNamespace(code="temu", name="Temu"), api_token=""
        )
        with self.assertRaises(ValueError) as ctx:
            get_adapter(store)
        self.assertIn("listings.temu", str(ctx.exception))


class TemuManagedStoreTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="tm", email="tm@example.com", password="pw")
        temu, _ = Marketplace.objects.get_or_create(code="temu", defaults={"name": "Temu"})
        self.store = Store.objects.create(
            user=self.user,
            name="Temu AU Store",
            region="AU",
            marketplace=temu,
            management_mode="full_store",
            temu_region="au",
            temu_app_key="app-key",
            temu_app_secret="app-secret",
            temu_access_token="token-123",
        )

    def test_marketplace_kind_resolves_temu(self):
        from stores.credentials import marketplace_kind

        self.assertEqual(marketplace_kind(self.store.marketplace), "temu")

    def test_template_headers_and_parse_round_trip(self):
        csv_text = csv_import.build_template_csv("create", store=self.store)
        self.assertIn("Warehouse ID", csv_text)
        self.assertIn("Parent SKU", csv_text)
        self.assertIn("Category", csv_text)
        rows = csv_import.parse_upload("temu.csv", csv_text.encode())
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["product_key"], "TEMU-TEE")
        self.assertEqual(rows[0]["sku"], "TEMU-TEE-M")
        self.assertEqual(rows[0]["warehouse_id"], "WH-EXAMPLE-1")
        self.assertEqual(rows[0]["category"], "30847")
        self.assertEqual(temu_products.validate_listing(rows[0]), [])

    def test_export_fields_use_temu_headers(self):
        headers = [header for _key, header in csv_import.export_field_specs(self.store)]
        self.assertIn("Warehouse ID", headers)
        self.assertIn("Parent SKU", headers)

    def test_listing_env_is_always_production(self):
        from listings.models import Environment

        self.assertEqual(listing_service._listing_env(self.store), Environment.PRODUCTION)

    def test_create_stores_temu_extras_and_marks_ready(self):
        listing = listing_service.create(self.user, self.store, dict(VALID_TEMU))
        self.assertEqual(listing.status, ListingStatus.READY)
        self.assertEqual(listing.sku, "TEMU-TEE-M")
        self.assertEqual(listing.external_product_key, "TEMU-TEE")
        extras = temu_products.parse_extras(listing)
        self.assertEqual(extras["marketplace"], "temu")
        self.assertEqual(extras["warehouse_id"], "WH-1")

    def test_publish_sends_goods_add_and_marks_uploaded(self):
        listing = listing_service.create(self.user, self.store, dict(VALID_TEMU))
        add_result = TemuResult(ok=True, data={"goodsId": "G-1"})
        with patch.object(temu_products, "lookup_goods", return_value=None), patch.object(
            TemuClient, "add_goods", return_value=add_result
        ) as add:
            result = listing_service.publish(self.user, self.store)
        self.assertTrue(result["ok"])
        self.assertEqual(result["published"], 1)
        payload = add.call_args.args[0]
        self.assertEqual(payload["outGoodsSn"], "TEMU-TEE")
        listing.refresh_from_db()
        self.assertEqual(listing.status, ListingStatus.UPLOADED_PRODUCTION)
        self.assertEqual(temu_products.parse_extras(listing)["goods_id"], "G-1")

    def test_publish_failure_marks_listing_failed_with_message(self):
        listing_service.create(self.user, self.store, dict(VALID_TEMU))
        failed = TemuResult(ok=False, message="catId invalid", error_code=4000001)
        with patch.object(temu_products, "lookup_goods", return_value=None), patch.object(
            TemuClient, "add_goods", return_value=failed
        ):
            result = listing_service.publish(self.user, self.store)
        self.assertFalse(result["ok"])
        self.assertEqual(result["failed"], 1)
        listing = StoreListing.objects.get(store=self.store)
        self.assertEqual(listing.status, ListingStatus.FAILED)
        self.assertIn("catId invalid", " ".join(listing.validation_errors_json))

    def test_push_inventory_edits_stock_for_resolved_sku(self):
        listing = listing_service.create(self.user, self.store, dict(VALID_TEMU))
        listing.status = ListingStatus.UPLOADED_PRODUCTION
        listing.save(update_fields=["status"])
        resolved = {"goods_id": "G-1", "sku_id": "S-1", "out_sku_sn": listing.sku}
        with patch.object(temu_products, "lookup_sku", return_value=resolved), patch.object(
            TemuClient, "edit_stock", return_value=TemuResult(ok=True, data={})
        ) as edit, patch.object(
            TemuClient, "change_sku_price", return_value=TemuResult(ok=True, data={})
        ):
            result = listing_service.push_inventory(self.user, self.store)
        self.assertTrue(result["ok"])
        self.assertEqual(result["pushed"], 1)
        changes = edit.call_args.args[0]
        self.assertEqual(changes[0]["skuId"], "S-1")
        self.assertEqual(changes[0]["targetStockAvailable"], 5)
        self.assertEqual(changes[0]["warehouseId"], "WH-1")

    def test_push_inventory_reports_skus_missing_on_temu(self):
        listing = listing_service.create(self.user, self.store, dict(VALID_TEMU))
        listing.status = ListingStatus.UPLOADED_PRODUCTION
        listing.save(update_fields=["status"])
        with patch.object(temu_products, "lookup_sku", return_value=None):
            with self.assertRaises(MarketplaceError) as ctx:
                listing_service.push_inventory(self.user, self.store)
        self.assertIn("were found on Temu", str(ctx.exception))

    def test_mapped_action_requires_sku_on_temu(self):
        with patch.object(temu_products, "lookup_sku", return_value=None):
            listing = listing_service.create(
                self.user, self.store, dict(VALID_TEMU), action="mapped"
            )
        self.assertEqual(listing.status, ListingStatus.VALIDATION_FAILED)
        self.assertTrue(
            any("was found on Temu" in e for e in listing.validation_errors_json),
            listing.validation_errors_json,
        )

    def test_delete_takes_listing_off_sale_before_removing_locally(self):
        listing = listing_service.create(self.user, self.store, dict(VALID_TEMU))
        with patch.object(
            temu_products, "lookup_sku", return_value={"goods_id": "G-1", "sku_id": "S-1"}
        ), patch.object(
            TemuClient, "set_sale_status", return_value=TemuResult(ok=True, data={})
        ) as off_sale, patch.object(
            TemuClient, "delete_goods", return_value=TemuResult(ok=True, data={})
        ):
            result = listing_service.delete(self.user, self.store, listing)
        self.assertTrue(result["marketplace_deleted"])
        self.assertEqual(off_sale.call_args.kwargs["on_sale"], False)
        self.assertFalse(StoreListing.objects.filter(store=self.store).exists())

    def test_fetch_orders_upserts_and_advances_sync_cutoff(self):
        page = TemuResult(
            ok=True,
            data={
                "pageItems": [
                    {
                        "parentOrderSn": "PO-1",
                        "parentOrderStatus": "UNSHIPPED",
                        "parentOrderPaidAmount": 2499,
                        "receiverName": "Jane Smith",
                        "orderList": [
                            {
                                "orderSn": "CO-1",
                                "outSkuSn": "TEMU-TEE-M",
                                "quantity": 1,
                                "originalOrderPrice": 2499,
                            }
                        ],
                    }
                ]
            },
        )
        with patch.object(TemuClient, "list_orders", return_value=page), patch.object(
            temu_orders, "fetch_address", return_value=None
        ):
            result = order_service.fetch(self.user, self.store)
        self.assertTrue(result["ok"])
        self.assertEqual(result["fetched"], 1)
        order = MarketplaceOrder.objects.get(store=self.store)
        self.assertEqual(order.external_order_key, "PO-1")
        self.assertEqual(order.status, OrderStatus.PAID)
        self.store.refresh_from_db()
        self.assertIsNotNone(self.store.temu_last_order_sync_at)

    def test_cancel_marks_local_cancelled_when_temu_refuses(self):
        order = MarketplaceOrder.objects.create(
            user=self.user,
            store=self.store,
            external_order_key="PO-2",
            invoice_number="PO-2",
            environment="production",
            status=OrderStatus.PAID,
        )
        refused = TemuResult(ok=False, message="Order already shipped")
        with patch.object(TemuClient, "cancel_out_of_stock", return_value=refused):
            result = order_service.cancel(order, reason="OUT_OF_STOCK")
        self.assertTrue(result["ok"])
        self.assertFalse(result["marketplace_ok"])
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.CANCELLED)

    def test_submit_shipping_creates_then_confirms_shipment(self):
        order = MarketplaceOrder.objects.create(
            user=self.user,
            store=self.store,
            external_order_key="PO-3",
            invoice_number="PO-3",
            environment="production",
            status=OrderStatus.PAID,
            line_items_json=[{"lineItemId": "CO-3", "quantity": 1}],
        )
        with patch.object(
            temu_orders, "resolve_shipping_company", return_value=("9", "Australia Post")
        ), patch.object(
            TemuClient, "create_shipment", return_value=TemuResult(ok=True, data={})
        ) as create, patch.object(
            TemuClient, "confirm_shipment", return_value=TemuResult(ok=True, data={})
        ) as confirm:
            result = shipping_service.submit(
                order, tracking_number="TRK9", carrier="Australia Post"
            )
        self.assertTrue(result["ok"])
        self.assertTrue(create.called)
        self.assertTrue(confirm.called)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.SHIPPING_SUBMITTED)

    def test_tickets_stay_unsupported_for_temu(self):
        from listings import ticket_service

        with self.assertRaises(MarketplaceError) as ctx:
            ticket_service.fetch(self.user, self.store)
        self.assertIn("not supported", str(ctx.exception).lower())

    def test_marketplace_lookup_reports_found_sku(self):
        from listings import marketplace_lookup

        resolved = {
            "goods_id": "G-1",
            "sku_id": "S-1",
            "out_sku_sn": "TEMU-TEE-M",
            "goods_name": "Cotton Tee",
            "status": "on_sale",
        }
        with patch.object(temu_products, "lookup_sku", return_value=resolved):
            result = marketplace_lookup.lookup_sku(self.store, "TEMU-TEE-M")
        self.assertTrue(result["found"])
        self.assertEqual(result["marketplace"], "temu")
        self.assertEqual(result["results"][0]["sku"], "TEMU-TEE-M")

    def test_shopify_forwarding_allows_temu(self):
        from listings.shopify.orders import SHOPIFY_ORDER_MARKETPLACES

        self.assertIn("temu", SHOPIFY_ORDER_MARKETPLACES)

    def test_order_sync_task_includes_temu_by_default(self):
        from listings import tasks

        with patch.object(tasks, "_managed_stores_qs", return_value=[]) as qs:
            tasks.sync_store_orders(region="AU")
        self.assertIn("temu", qs.call_args.kwargs["marketplace_codes"])
