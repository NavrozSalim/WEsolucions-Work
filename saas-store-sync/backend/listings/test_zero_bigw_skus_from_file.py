"""Big W (MyDeal) stock 0 for the Wallkoala SKUs in an Excel file."""
import os
import tempfile
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from openpyxl import Workbook

from catalog.models import CatalogActivityLog, MydealTemplateRow, MydealUploadRun, ProductMapping
from marketplace.models import Marketplace
from products.models import Product
from stores.models import Store
from vendor.models import Vendor

from .models import InventorySyncStatus, ListingStatus, StoreListing
from .mydeal.client import MyDealResult

CMD = "listings.management.commands.zero_bigw_skus_from_file"


def _workbook(skus, header="SKU") -> str:
    book = Workbook()
    sheet = book.active
    sheet.append([header])
    for sku in skus:
        sheet.append([sku])
    handle = tempfile.NamedTemporaryFile(prefix="bigw_zero_", suffix=".xlsx", delete=False)
    handle.close()
    book.save(handle.name)
    return handle.name


def _entry(product, sku, qty, *, price=20.0, unlimited=False):
    return {
        "sku": product,
        "external_product_id": product,
        "buyable_sku": sku,
        "external_buyable_id": sku,
        "price": price,
        "rrp": price,
        "quantity": qty,
        "unlimited": unlimited,
        "group_keys": {product.casefold()},
    }


class ZeroBigWSkusFromFileTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="bigw_zero", email="bigw-zero@example.com", password="pw",
        )
        self.other_user = User.objects.create_user(
            username="bigw_zero_other", email="bigw-zero-other@example.com", password="pw",
        )
        self.mydeal, _ = Marketplace.objects.get_or_create(code="mydeal", defaults={"name": "MyDeal"})
        self.lasoo, _ = Marketplace.objects.get_or_create(code="lasoo", defaults={"name": "Lasoo"})
        self.store = Store.objects.create(
            user=self.user, name="Shemaya", region="AU", api_token="",
            marketplace=self.mydeal, management_mode="full_store", mydeal_setup_method="api",
        )
        # Same name on another marketplace: never picked.
        Store.objects.create(
            user=self.user, name="Shemaya", region="AU", api_token="", marketplace=self.lasoo,
        )
        self.wallkoala, _ = Vendor.objects.get_or_create(code="wallkoala", defaults={"name": "Wallkoala"})
        self.nora, _ = Vendor.objects.get_or_create(code="noraau", defaults={"name": "Nora"})

    def _listing(self, store, sku, *, stock=5, source="wallkoala", infinite=False,
                 status=ListingStatus.UPLOADED_PRODUCTION, product=None):
        return StoreListing.objects.create(
            user=store.user,
            store=store,
            external_product_key=product or sku,
            external_variant_key=sku,
            sku=sku,
            title=sku,
            sale_price=Decimal("20.00"),
            original_price=Decimal("20.00"),
            inventory=stock,
            infinite_quantity=infinite,
            status=status,
            source_vendor_code=source,
            inventory_sync_status=InventorySyncStatus.SYNCED,
        )

    def _mapping(self, store, sku, *, vendor=None, stock=4):
        product = Product.objects.create(owner=store.user, vendor=vendor or self.wallkoala, vendor_sku=sku)
        return ProductMapping.objects.create(
            store=store, product=product, marketplace_child_sku=sku, is_active=True,
            store_price=Decimal("20.00"), store_stock=stock,
        )

    def _run(self, skus, *, entries=None, **kwargs):
        path = _workbook(skus)
        kwargs.setdefault("store", "Shemaya")
        kwargs.setdefault("stdout", StringIO())
        try:
            with patch(f"{CMD}._live_entries", return_value=entries or []):
                call_command("zero_bigw_skus_from_file", file=path, **kwargs)
        finally:
            os.unlink(path)

    @patch("listings.mydeal.client.MyDealClient")
    def test_dry_run_reports_and_changes_nothing(self, client_cls):
        listing = self._listing(self.store, "BR120S")
        mapping = self._mapping(self.store, "PD46-60X120")
        self._listing(self.store, "NORA-1", source="noraau")
        self._listing(self.store, "ZERO-1", stock=0)
        entries = [
            _entry("BR120S", "BR120S", 5),
            _entry("PD46-60X120", "PD46-60X120", 4),
            _entry("ZERO-1", "ZERO-1", 0),
        ]

        out = StringIO()
        self._run(
            ["BR120S", "pd46-60x120", "NORA-1", "ZERO-1", "MISSING-1", "BR120S"],
            entries=entries, dry_run=True, stdout=out,
        )
        text = out.getvalue()
        self.assertIn("DRY RUN", text)
        self.assertIn("SKUs in file: 5", text)
        self.assertIn("Matched (Wallkoala on this store): 3", text)
        self.assertIn("Already 0 (local and on Big W): 1", text)
        self.assertIn("Will be set to 0: 2", text)
        self.assertIn("SKUs to send to Big W with stock 0: 2", text)
        self.assertIn("Other vendor on this store (not changed): 1", text)
        self.assertIn("NORA-1 (vendor noraau)", text)
        self.assertIn("Unmatched (not on this store): 1", text)
        self.assertIn("MISSING-1", text)
        client_cls.return_value.update_quantity_price.assert_not_called()
        listing.refresh_from_db()
        mapping.refresh_from_db()
        self.assertEqual(listing.inventory, 5)
        self.assertEqual(mapping.store_stock, 4)
        self.assertFalse(CatalogActivityLog.objects.filter(store=self.store).exists())

    @patch("listings.mydeal.client.MyDealClient")
    def test_api_store_zeroes_local_and_pushes_only_listed_variants(self, client_cls):
        client = client_cls.return_value
        client.update_quantity_price.return_value = MyDealResult(ok=True, message="ok")
        target = self._listing(self.store, "BR016-IRON120X141CM", product="BR016-IRON", infinite=True)
        sibling = self._listing(self.store, "BR016-IRON90X100CM", product="BR016-IRON", stock=7)
        mapping = self._mapping(self.store, "PD44-60X120")
        nora = self._listing(self.store, "NORA-1", source="noraau")
        other_store = Store.objects.create(
            user=self.other_user, name="Other BigW", region="AU", api_token="",
            marketplace=self.mydeal, mydeal_setup_method="api",
        )
        elsewhere = self._listing(other_store, "PD44-60X120")
        entries = [
            _entry("BR016-IRON", "BR016-IRON120X141CM", 0, unlimited=True),
            _entry("BR016-IRON", "BR016-IRON90X100CM", 7, price=30.0),
            _entry("PD44-60X120", "PD44-60X120", 4),
            _entry("NORA-1", "NORA-1", 5),
        ]
        self._run(["BR016-IRON120X141CM", "PD44-60X120", "NORA-1"], entries=entries)

        target.refresh_from_db()
        sibling.refresh_from_db()
        mapping.refresh_from_db()
        nora.refresh_from_db()
        elsewhere.refresh_from_db()
        self.assertEqual(target.inventory, 0)
        self.assertFalse(target.infinite_quantity)
        self.assertEqual(sibling.inventory, 7)
        self.assertEqual(mapping.store_stock, 0)
        self.assertEqual(nora.inventory, 5)
        self.assertEqual(elsewhere.inventory, 5)

        client.update_quantity_price.assert_called_once()
        groups = client.update_quantity_price.call_args.args[0]
        by_sku = {b["SKU"]: b for g in groups for b in g["BuyableProducts"]}
        self.assertEqual(set(by_sku), {"BR016-IRON120X141CM", "BR016-IRON90X100CM", "PD44-60X120"})
        self.assertEqual(by_sku["BR016-IRON120X141CM"]["Quantity"], 0)
        self.assertFalse(by_sku["BR016-IRON120X141CM"]["ProductUnlimited"])
        self.assertEqual(by_sku["BR016-IRON90X100CM"]["Quantity"], 7)
        self.assertEqual(by_sku["BR016-IRON90X100CM"]["Price"], 30.0)
        self.assertEqual(by_sku["PD44-60X120"]["Quantity"], 0)

        log = CatalogActivityLog.objects.get(store=self.store)
        self.assertEqual(log.action_type, "bigw_zero_inventory_file")
        self.assertIn("2 Wallkoala SKU(s) set to 0", log.message)
        self.assertIn("1 other vendor", log.message)

    @patch("listings.mydeal.client.MyDealClient")
    def test_api_store_skips_push_when_already_zero_on_bigw(self, client_cls):
        listing = self._listing(self.store, "BR120L", stock=3)
        self._run(["BR120L"], entries=[_entry("BR120L", "BR120L", 0)])
        listing.refresh_from_db()
        self.assertEqual(listing.inventory, 0)
        client_cls.return_value.update_quantity_price.assert_not_called()

    @patch("listings.mydeal.client.MyDealClient")
    def test_failed_push_raises_after_local_zero_and_logs(self, client_cls):
        client_cls.return_value.update_quantity_price.return_value = MyDealResult(ok=False, message="Token expired")
        listing = self._listing(self.store, "BR120S")
        with self.assertRaises(CommandError) as ctx:
            self._run(["BR120S"], entries=[_entry("BR120S", "BR120S", 5)])
        self.assertIn("some Big W updates failed", str(ctx.exception))
        listing.refresh_from_db()
        self.assertEqual(listing.inventory, 0)
        log = CatalogActivityLog.objects.get(store=self.store)
        self.assertIn("Token expired", log.message)

    def test_upload_store_zeroes_mapping_and_queues_inventory_upload(self):
        self.store.mydeal_setup_method = "upload"
        self.store.save(update_fields=["mydeal_setup_method"])
        mapping = self._mapping(self.store, "PD48-60X120")
        nora = self._mapping(self.store, "NORA-2", vendor=self.nora, stock=6)
        MydealTemplateRow.objects.create(
            store=self.store, kind=MydealTemplateRow.Kind.INVENTORY, row_number=1, sku="PD48-60X120",
        )
        MydealTemplateRow.objects.create(
            store=self.store, kind=MydealTemplateRow.Kind.INVENTORY, row_number=2, sku="NORA-2",
        )
        with patch(f"{CMD}._live_entries") as live:
            self._run(["PD48-60X120", "NORA-2"])
            live.assert_not_called()
        mapping.refresh_from_db()
        nora.refresh_from_db()
        self.assertEqual(mapping.store_stock, 0)
        self.assertEqual(nora.store_stock, 6)
        run = MydealUploadRun.objects.get(store=self.store)
        self.assertEqual(run.kinds, ["inventory"])
        self.assertEqual([v["id"] for v in run.vendors], [str(self.wallkoala.id)])

    def test_same_store_name_twice_needs_an_id(self):
        twin = Store.objects.create(
            user=self.other_user, name="Shemaya", region="AU", api_token="",
            marketplace=self.mydeal, mydeal_setup_method="api",
        )
        listing = self._listing(twin, "BR120S")
        with self.assertRaises(CommandError) as ctx:
            self._run(["BR120S"], dry_run=True)
        self.assertIn("More than one", str(ctx.exception))
        self.assertIn(str(twin.id), str(ctx.exception))
        self.assertIn("bigw-zero-other@example.com", str(ctx.exception))

        with patch("listings.mydeal.client.MyDealClient") as client_cls:
            client_cls.return_value.update_quantity_price.return_value = MyDealResult(ok=True)
            self._run(["BR120S"], store=str(twin.id), entries=[_entry("BR120S", "BR120S", 5)])
        listing.refresh_from_db()
        self.assertEqual(listing.inventory, 0)

    def test_email_narrows_the_store_name(self):
        Store.objects.create(
            user=self.other_user, name="Shemaya", region="AU", api_token="",
            marketplace=self.mydeal, mydeal_setup_method="api",
        )
        listing = self._listing(self.store, "BR120S")

        out = StringIO()
        self._run(["BR120S"], email="bigw-zero@example.com", dry_run=True, stdout=out)
        self.assertIn(str(self.store.id), out.getvalue())
        listing.refresh_from_db()
        self.assertEqual(listing.inventory, 5)

    def test_unknown_store_lists_mydeal_stores(self):
        with self.assertRaises(CommandError) as ctx:
            self._run(["BR120S"], store="Semaya", dry_run=True)
        self.assertIn("No MyDeal / Big W store is named 'Semaya'", str(ctx.exception))
        self.assertIn("Shemaya", str(ctx.exception))

    def test_no_wallkoala_match_changes_nothing(self):
        nora = self._listing(self.store, "NORA-1", source="noraau")
        with self.assertRaises(CommandError) as ctx:
            self._run(["NORA-1", "MISSING"])
        self.assertIn("No SKU in the file is a Wallkoala SKU", str(ctx.exception))
        nora.refresh_from_db()
        self.assertEqual(nora.inventory, 5)
        self.assertFalse(CatalogActivityLog.objects.filter(store=self.store).exists())

    def test_file_needs_a_sku_column(self):
        path = _workbook(["BR120S"], header="Product")
        try:
            with self.assertRaises(CommandError) as ctx:
                call_command("zero_bigw_skus_from_file", store="Shemaya", file=path, dry_run=True)
        finally:
            os.unlink(path)
        self.assertIn("column named SKU", str(ctx.exception))
