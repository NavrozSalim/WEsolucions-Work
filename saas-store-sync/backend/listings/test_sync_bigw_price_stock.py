"""One-time BigW (MyDeal) price and stock push from SKU, Price, Inventory."""
import os
import tempfile
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from openpyxl import Workbook
from unittest.mock import patch

from marketplace.models import Marketplace
from stores.models import Store

from .models import InventorySyncStatus, ListingStatus, StoreListing
from .mydeal.client import MyDealResult


def _workbook(rows, *, headers=None, sheets=None) -> str:
    book = Workbook()
    if sheets is None:
        sheets = {"Sheet1": rows}
        if headers is None:
            headers = ["SKU", "Price", "Inventory"]
    first = True
    for name, sheet_rows in sheets.items():
        sheet = book.active if first else book.create_sheet(name)
        if first:
            sheet.title = name
            first = False
        if name == "Sheet1" and headers:
            sheet.append(headers)
        elif headers and name == next(iter(sheets)):
            sheet.append(headers)
        for row in sheet_rows:
            sheet.append(list(row))
    handle = tempfile.NamedTemporaryFile(prefix="bigw_price_", suffix=".xlsx", delete=False)
    handle.close()
    book.save(handle.name)
    return handle.name


class SyncBigWPriceStockTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="bigw_semaya",
            email="bigw-semaya@example.com",
            password="pw",
        )
        self.other = User.objects.create_user(
            username="other_bigw",
            email="other-bigw@example.com",
            password="pw",
        )
        self.mydeal, _ = Marketplace.objects.get_or_create(code="mydeal", defaults={"name": "MyDeal"})
        self.lasoo, _ = Marketplace.objects.get_or_create(code="lasoo", defaults={"name": "Lasoo"})
        self.store = Store.objects.create(
            user=self.user,
            name="Shemaya",
            region="AU",
            api_token="",
            marketplace=self.mydeal,
            management_mode="full_store",
        )
        self.other_mydeal = Store.objects.create(
            user=self.other,
            name="Shemaya",
            region="AU",
            api_token="",
            marketplace=self.mydeal,
            management_mode="full_store",
        )
        self.bunnings_name = Store.objects.create(
            user=self.user,
            name="Shemaya",
            region="AU",
            api_token="",
            marketplace=self.lasoo,
            management_mode="full_store",
        )

    def _listing(self, store, sku, *, price="9.00", stock=1, status=ListingStatus.UPLOADED_PRODUCTION, infinite=False):
        return StoreListing.objects.create(
            user=store.user,
            store=store,
            external_product_key=sku + "-P",
            external_variant_key=sku + "-B",
            sku=sku,
            title=sku,
            sale_price=Decimal(price),
            original_price=Decimal(price),
            sale_price_cents=int(Decimal(price) * 100),
            original_price_cents=int(Decimal(price) * 100),
            inventory=stock,
            infinite_quantity=infinite,
            status=status,
            inventory_sync_status=InventorySyncStatus.PENDING,
        )

    def _run(self, path, **kwargs):
        try:
            call_command(
                "sync_bigw_price_stock",
                email="bigw-semaya@example.com",
                store="Shemaya",
                file=path,
                **kwargs,
            )
        finally:
            os.unlink(path)

    @patch("listings.mydeal.products.MyDealClient")
    def test_pushes_the_file_price_and_stock(self, mock_client_cls):
        client = mock_client_cls.return_value
        client.update_price_quantity.return_value = MyDealResult(ok=True, message="Price/stock pushed to MyDeal.")
        listing = self._listing(self.store, "SKU-1", infinite=True)
        other = self._listing(self.other_mydeal, "SKU-1", price="3.00", stock=9)
        lasoo = self._listing(self.bunnings_name, "SKU-1", price="4.00", stock=8)
        untouched = self._listing(self.store, "SKU-KEEP", price="5.00", stock=2)
        path = _workbook([
            ("SKU-1", 19.95, 6),
        ])
        self._run(path)

        listing.refresh_from_db()
        other.refresh_from_db()
        lasoo.refresh_from_db()
        untouched.refresh_from_db()
        self.assertEqual(listing.sale_price, Decimal("19.95"))
        self.assertEqual(listing.original_price, Decimal("19.95"))
        self.assertEqual(listing.sale_price_cents, 1995)
        self.assertEqual(listing.original_price_cents, 1995)
        self.assertEqual(listing.inventory, 6)
        self.assertFalse(listing.infinite_quantity)
        self.assertEqual(listing.inventory_sync_status, InventorySyncStatus.SYNCED)
        self.assertEqual(listing.sku, "SKU-1")
        self.assertEqual(other.sale_price, Decimal("3.00"))
        self.assertEqual(other.inventory, 9)
        self.assertEqual(lasoo.sale_price, Decimal("4.00"))
        self.assertEqual(untouched.sale_price, Decimal("5.00"))
        self.assertEqual(untouched.inventory, 2)
        payload = client.update_price_quantity.call_args.args[0]
        buyable = payload[0]["BuyableProducts"][0]
        self.assertEqual(buyable["SKU"], "SKU-1")
        self.assertEqual(buyable["Price"], 19.95)
        self.assertEqual(buyable["Quantity"], 6)
        self.assertFalse(buyable["ProductUnlimited"])
        self.assertEqual(client.update_price_quantity.call_count, 1)

    @patch("listings.mydeal.products.MyDealClient")
    def test_final_price_header_and_zero_stock(self, mock_client_cls):
        client = mock_client_cls.return_value
        client.update_price_quantity.return_value = MyDealResult(ok=True, message="ok")
        listing = self._listing(self.store, "sku-2")
        path = _workbook(
            [("SKU-2", "$12.50", 0)],
            headers=["SKU", "Final Price", "Inventory"],
        )
        self._run(path)
        listing.refresh_from_db()
        self.assertEqual(listing.sale_price, Decimal("12.50"))
        self.assertEqual(listing.inventory, 0)
        buyable = client.update_price_quantity.call_args.args[0][0]["BuyableProducts"][0]
        self.assertEqual(buyable["Price"], 12.5)
        self.assertEqual(buyable["Quantity"], 0)

    @patch("listings.mydeal.products.MyDealClient")
    def test_excel_number_sku_matches(self, mock_client_cls):
        client = mock_client_cls.return_value
        client.update_price_quantity.return_value = MyDealResult(ok=True, message="ok")
        listing = self._listing(self.store, "1001")
        path = _workbook([(1001.0, 8, 3.0)])
        self._run(path)
        listing.refresh_from_db()
        self.assertEqual(listing.sale_price, Decimal("8.00"))
        self.assertEqual(listing.inventory, 3)
        self.assertEqual(listing.sku, "1001")
        client.update_price_quantity.assert_called_once()

    @patch("listings.mydeal.products.MyDealClient")
    def test_dry_run_does_not_write_or_push(self, mock_client_cls):
        listing = self._listing(self.store, "SKU-1")
        path = _workbook([("SKU-1", 19.95, 6)])
        self._run(path, dry_run=True)
        listing.refresh_from_db()
        self.assertEqual(listing.sale_price, Decimal("9.00"))
        self.assertEqual(listing.inventory, 1)
        mock_client_cls.assert_not_called()

    @patch("listings.mydeal.products.MyDealClient")
    def test_missing_sku_writes_nothing(self, mock_client_cls):
        good = self._listing(self.store, "SKU-1")
        path = _workbook([
            ("SKU-1", 19.95, 6),
            ("MISSING", 4, 1),
        ])
        with self.assertRaises(CommandError) as raised:
            self._run(path)
        self.assertIn("Nothing was changed", str(raised.exception))
        self.assertIn("MISSING", str(raised.exception))
        good.refresh_from_db()
        self.assertEqual(good.sale_price, Decimal("9.00"))
        self.assertEqual(good.inventory, 1)
        mock_client_cls.assert_not_called()

    @patch("listings.mydeal.products.MyDealClient")
    def test_unpublished_sku_writes_nothing(self, mock_client_cls):
        listing = self._listing(self.store, "SKU-1", status=ListingStatus.READY)
        path = _workbook([("SKU-1", 19.95, 6)])
        with self.assertRaises(CommandError) as raised:
            self._run(path)
        self.assertIn("not published", str(raised.exception))
        listing.refresh_from_db()
        self.assertEqual(listing.sale_price, Decimal("9.00"))
        mock_client_cls.assert_not_called()

    @patch("listings.mydeal.products.MyDealClient")
    def test_failed_push_rolls_back_the_saved_price(self, mock_client_cls):
        client = mock_client_cls.return_value
        client.update_price_quantity.return_value = MyDealResult(ok=False, message="MyDeal rejected the batch.")
        listing = self._listing(self.store, "SKU-1", infinite=True)
        path = _workbook([("SKU-1", 19.95, 6)])
        with self.assertRaises(CommandError) as raised:
            self._run(path)
        self.assertIn("Nothing was changed", str(raised.exception))
        self.assertIn("MyDeal rejected the batch.", str(raised.exception))
        listing.refresh_from_db()
        self.assertEqual(listing.sale_price, Decimal("9.00"))
        self.assertEqual(listing.inventory, 1)
        self.assertTrue(listing.infinite_quantity)
        self.assertEqual(listing.inventory_sync_status, InventorySyncStatus.PENDING)

    @patch("listings.mydeal.products.MyDealClient")
    def test_variant_key_is_not_the_sku(self, mock_client_cls):
        listing = self._listing(self.store, "REAL-SKU")
        listing.sku = ""
        listing.external_variant_key = "REAL-SKU"
        listing.save(update_fields=["sku", "external_variant_key"])
        path = _workbook([("REAL-SKU", 19.95, 6)])
        with self.assertRaises(CommandError) as raised:
            self._run(path)
        self.assertIn("was not found", str(raised.exception))
        listing.refresh_from_db()
        self.assertEqual(listing.sale_price, Decimal("9.00"))
        mock_client_cls.assert_not_called()

    @patch("listings.mydeal.products.MyDealClient")
    def test_two_published_listings_with_the_same_sku_write_nothing(self, mock_client_cls):
        first = self._listing(self.store, "SKU-1")
        second = StoreListing.objects.create(
            user=self.user,
            store=self.store,
            external_product_key="SKU-1-P2",
            external_variant_key="SKU-1-B2",
            sku="SKU-1",
            title="SKU-1",
            sale_price=Decimal("9.00"),
            original_price=Decimal("9.00"),
            inventory=1,
            status=ListingStatus.UPLOADED_PRODUCTION,
        )
        path = _workbook([("SKU-1", 19.95, 6)])
        with self.assertRaises(CommandError):
            self._run(path)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.sale_price, Decimal("9.00"))
        self.assertEqual(second.sale_price, Decimal("9.00"))
        mock_client_cls.assert_not_called()

    def test_price_with_extra_decimals_writes_nothing(self):
        listing = self._listing(self.store, "SKU-1")
        path = _workbook([("SKU-1", "19.999", 1)])
        with self.assertRaises(CommandError) as raised:
            self._run(path)
        self.assertIn("more than 2 decimal places", str(raised.exception))
        listing.refresh_from_db()
        self.assertEqual(listing.sale_price, Decimal("9.00"))

    def test_store_name_must_be_the_mydeal_store(self):
        listing = self._listing(self.store, "SKU-1")
        path = _workbook([("SKU-1", 19.95, 6)])
        try:
            with self.assertRaises(CommandError) as raised:
                call_command(
                    "sync_bigw_price_stock",
                    email="bigw-semaya@example.com",
                    store="Semaya",
                    file=path,
                )
        finally:
            os.unlink(path)
        self.assertIn("No MyDeal store named", str(raised.exception))
        self.assertIn("Shemaya", str(raised.exception))
        listing.refresh_from_db()
        self.assertEqual(listing.sale_price, Decimal("9.00"))
