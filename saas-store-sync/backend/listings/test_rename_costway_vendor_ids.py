"""Rename Costway Vendor IDs per store sheet: SKU + Old Vendor ID -> New Vendor ID."""
import os
import tempfile

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from openpyxl import Workbook

from catalog.models import ProductMapping
from marketplace.models import Marketplace
from products.models import Product
from stores.models import Store
from vendor.models import Vendor

from .models import StoreListing


def _workbook(sheets) -> str:
    book = Workbook()
    first = True
    for name, rows in sheets.items():
        sheet = book.active if first else book.create_sheet(name)
        if first:
            sheet.title = name
            first = False
        sheet.append(["SKU", "Old Vendor ID", "New Vendor ID"])
        for row in rows:
            sheet.append(list(row))
    handle = tempfile.NamedTemporaryFile(prefix="costway_ids_", suffix=".xlsx", delete=False)
    handle.close()
    book.save(handle.name)
    return handle.name


class RenameCostwayVendorIdsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="afraz",
            email="afraaz.prettyandpractical@gmail.com",
            password="pw",
        )
        self.other = User.objects.create_user(
            username="other_costway",
            email="other-costway@example.com",
            password="pw",
        )
        lasoo, _ = Marketplace.objects.get_or_create(code="lasoo", defaults={"name": "Lasoo"})
        self.store_a = Store.objects.create(
            user=self.user, name="Store A", region="AU", api_token="", marketplace=lasoo,
        )
        self.store_b = Store.objects.create(
            user=self.user, name="Store B", region="AU", api_token="", marketplace=lasoo,
        )
        self.other_store = Store.objects.create(
            user=self.other, name="Store A", region="AU", api_token="", marketplace=lasoo,
        )
        self.costway, _ = Vendor.objects.get_or_create(code="costwayau", defaults={"name": "Costway"})
        self.nora, _ = Vendor.objects.get_or_create(code="noraau", defaults={"name": "Nora"})

    def _product(self, owner, vendor, sku, vendor_id=""):
        return Product.objects.create(
            owner=owner,
            vendor=vendor,
            vendor_sku=sku,
            inventory_vendor_id=vendor_id,
        )

    def _listing(self, store, sku, vendor_id="", source="costwayau"):
        return StoreListing.objects.create(
            user=store.user,
            store=store,
            external_product_key=sku,
            external_variant_key=sku + "-v",
            sku=sku,
            title=sku,
            vendor_id=vendor_id,
            source_vendor_code=source,
        )

    def test_each_sheet_updates_only_that_stores_matching_sku(self):
        catalog = self._product(self.user, self.costway, "TP10003", "TP10003")
        ProductMapping.objects.create(
            store=self.store_a,
            product=catalog,
            marketplace_child_sku="COW-73982054-TP10003-New",
            marketplace_id="MID-A",
            is_active=True,
        )
        other_catalog = self._product(self.user, self.costway, "TP10003-B", "TP10003")
        ProductMapping.objects.create(
            store=self.store_b,
            product=other_catalog,
            marketplace_child_sku="COW-73982054-TP10003-New",
            marketplace_id="MID-B",
            is_active=True,
        )
        listing_a = self._listing(self.store_a, "LASOO-A", "TP10004")
        listing_b = self._listing(self.store_b, "LASOO-B", "TP10004")
        untouched = self._listing(self.store_a, "LASOO-OTHER", "TP10004")
        nora_listing = self._listing(self.store_a, "NORA-1", "TP10003", source="noraau")
        other_listing = self._listing(self.other_store, "LASOO-A", "TP10004")
        path = _workbook({
            "Store A": [
                ("COW-73982054-TP10003-New", "TP10003", "73982054-TP10003"),
                ("LASOO-A", "TP10004", "111-TP10004"),
                ("NORA-1", "TP10003", "000-NORA"),
                ("MISSING", "TP10009", "333-MISSING"),
            ],
            "store b": [
                ("LASOO-B", "tp10004", "222-TP10004"),
            ],
        })
        try:
            call_command(
                "rename_costway_vendor_ids",
                email="afraaz.prettyandpractical@gmail.com",
                file=path,
            )
        finally:
            os.unlink(path)

        catalog.refresh_from_db()
        other_catalog.refresh_from_db()
        listing_a.refresh_from_db()
        listing_b.refresh_from_db()
        untouched.refresh_from_db()
        nora_listing.refresh_from_db()
        other_listing.refresh_from_db()
        self.assertEqual(catalog.inventory_vendor_id, "73982054-TP10003")
        self.assertEqual(catalog.vendor_sku, "TP10003")
        self.assertEqual(other_catalog.inventory_vendor_id, "TP10003")
        self.assertEqual(listing_a.vendor_id, "111-TP10004")
        self.assertEqual(listing_a.sku, "LASOO-A")
        self.assertEqual(listing_b.vendor_id, "222-TP10004")
        self.assertEqual(untouched.vendor_id, "TP10004")
        self.assertEqual(nora_listing.vendor_id, "TP10003")
        self.assertEqual(other_listing.vendor_id, "TP10004")

    def test_old_vendor_id_with_new_suffix_matches_the_saved_id(self):
        listing = self._listing(self.store_a, "COW-94723608-TY326396-New", "TY326396")
        path = _workbook({
            "Store A": [("COW-94723608-TY326396-New", "TY326396-New", "94723608-TY326396")],
        })
        try:
            call_command(
                "rename_costway_vendor_ids",
                email="afraaz.prettyandpractical@gmail.com",
                file=path,
            )
        finally:
            os.unlink(path)
        listing.refresh_from_db()
        self.assertEqual(listing.vendor_id, "94723608-TY326396")

    def test_wrong_old_vendor_id_is_left_unchanged(self):
        listing = self._listing(self.store_a, "LASOO-A", "TP10003")
        path = _workbook({
            "Store A": [("LASOO-A", "NOT-THE-OLD-ID", "73982054-TP10003")],
        })
        try:
            call_command(
                "rename_costway_vendor_ids",
                email="afraaz.prettyandpractical@gmail.com",
                file=path,
            )
        finally:
            os.unlink(path)
        listing.refresh_from_db()
        self.assertEqual(listing.vendor_id, "TP10003")

    def test_dry_run_does_not_write(self):
        listing = self._listing(self.store_a, "LASOO-A", "TP10003")
        path = _workbook({
            "Store A": [("LASOO-A", "TP10003", "73982054-TP10003")],
        })
        try:
            call_command(
                "rename_costway_vendor_ids",
                email="afraaz.prettyandpractical@gmail.com",
                file=path,
                dry_run=True,
            )
        finally:
            os.unlink(path)
        listing.refresh_from_db()
        self.assertEqual(listing.vendor_id, "TP10003")

    def test_unknown_sheet_changes_nothing(self):
        listing = self._listing(self.store_a, "LASOO-A", "TP10003")
        path = _workbook({
            "Store A": [("LASOO-A", "TP10003", "73982054-TP10003")],
            "Nope": [("LASOO-A", "TP10003", "999-TP10003")],
        })
        try:
            with self.assertRaises(CommandError):
                call_command(
                    "rename_costway_vendor_ids",
                    email="afraaz.prettyandpractical@gmail.com",
                    file=path,
                )
        finally:
            os.unlink(path)
        listing.refresh_from_db()
        self.assertEqual(listing.vendor_id, "TP10003")

    def test_same_catalog_product_cannot_get_two_new_ids(self):
        product = self._product(self.user, self.costway, "TP10003", "TP10003")
        ProductMapping.objects.create(
            store=self.store_a, product=product, marketplace_child_sku="SKU-A", is_active=True,
        )
        ProductMapping.objects.create(
            store=self.store_b, product=product, marketplace_child_sku="SKU-B", is_active=True,
        )
        path = _workbook({
            "Store A": [("SKU-A", "TP10003", "111-TP10003")],
            "Store B": [("SKU-B", "TP10003", "222-TP10003")],
        })
        try:
            with self.assertRaises(CommandError):
                call_command(
                    "rename_costway_vendor_ids",
                    email="afraaz.prettyandpractical@gmail.com",
                    file=path,
                )
        finally:
            os.unlink(path)
        product.refresh_from_db()
        self.assertEqual(product.inventory_vendor_id, "TP10003")
