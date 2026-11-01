"""Rename Wallkoala Vendor IDs once: SKU + Old Vendor ID -> New Vendor ID.

Every row must match. A mismatch writes nothing, including rows that did match.
"""
import os
import tempfile

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from openpyxl import Workbook

from catalog.models import CatalogUpload, CatalogUploadRow, ProductMapping
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
    handle = tempfile.NamedTemporaryFile(prefix="wallkoala_ids_", suffix=".xlsx", delete=False)
    handle.close()
    book.save(handle.name)
    return handle.name


class RenameWallkoalaVendorIdsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="wallkoala_ids",
            email="wallkoala-ids@example.com",
            password="pw",
        )
        self.other = User.objects.create_user(
            username="other_wallkoala",
            email="other-wallkoala@example.com",
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
        self.wallkoala, _ = Vendor.objects.get_or_create(
            code="wallkoala", defaults={"name": "Wallkoala"},
        )
        self.nora, _ = Vendor.objects.get_or_create(code="noraau", defaults={"name": "Nora"})

    def _product(self, owner, vendor, sku, vendor_id=""):
        return Product.objects.create(
            owner=owner,
            vendor=vendor,
            vendor_sku=sku,
            inventory_vendor_id=vendor_id,
        )

    def _listing(self, store, sku, vendor_id="", source="wallkoala"):
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

    def _mapping(self, store, product, sku, marketplace_id):
        return ProductMapping.objects.create(
            store=store,
            product=product,
            marketplace_child_sku=sku,
            marketplace_id=marketplace_id,
            is_active=True,
        )

    def _catalog_row(self, store, product, sku, vendor_id, *, deleted=False, row_number=1):
        upload = CatalogUpload.objects.create(
            user=store.user,
            store=store,
            original_filename=f"{sku}-{row_number}.xlsx",
        )
        return CatalogUploadRow.objects.create(
            catalog_upload=upload,
            row_number=row_number,
            vendor_name_raw="Wallkoala",
            store_name_raw=store.name,
            marketplace_child_sku_raw=sku,
            vendor_id_raw=vendor_id,
            vendor=self.wallkoala,
            store=store,
            product=product,
            sync_status=(
                CatalogUploadRow.SyncStatus.DELETED
                if deleted
                else CatalogUploadRow.SyncStatus.ADDED
            ),
        )

    def _run(self, path, **kwargs):
        try:
            call_command(
                "rename_wallkoala_vendor_ids",
                email="wallkoala-ids@example.com",
                file=path,
                **kwargs,
            )
        finally:
            os.unlink(path)

    def test_a_row_for_another_vendor_or_a_missing_sku_writes_nothing(self):
        catalog = self._product(self.user, self.wallkoala, "WK-CAT", "OLD-CAT")
        self._mapping(self.store_a, catalog, "WK-CAT", "MID-A")
        catalog_row = self._catalog_row(self.store_a, catalog, "WK-CAT", "OLD-CAT")
        other_catalog = self._product(self.user, self.wallkoala, "WK-CAT-B", "OLD-B")
        self._mapping(self.store_b, other_catalog, "WK-CAT-B", "MID-B")
        listing_a = self._listing(self.store_a, "LASOO-A", "OLD-A")
        listing_b = self._listing(self.store_b, "LASOO-B", "OLD-B")
        untouched = self._listing(self.store_a, "LASOO-OTHER", "OLD-A")
        nora_listing = self._listing(self.store_a, "NORA-1", "OLD-NORA", source="noraau")
        other_listing = self._listing(self.other_store, "LASOO-A", "OLD-A")
        blank_product = self._product(self.user, self.wallkoala, "LASOO-A", "")
        self._mapping(self.store_a, blank_product, "LASOO-A", "MID-BLANK")
        path = _workbook({
            "Store A": [
                ("WK-CAT", "OLD-CAT", "NEW-CAT"),
                ("LASOO-A", "OLD-A", "NEW-A"),
                ("NORA-1", "OLD-NORA", "NEW-NORA"),
                ("MISSING", "OLD-X", "NEW-X"),
            ],
            "store b": [
                ("LASOO-B", "old-b", "NEW-B"),
            ],
        })
        with self.assertRaises(CommandError) as raised:
            self._run(path)
        self.assertIn("Nothing was changed", str(raised.exception))
        self.assertIn("NORA-1", str(raised.exception))
        self.assertIn("MISSING", str(raised.exception))

        catalog.refresh_from_db()
        other_catalog.refresh_from_db()
        catalog_row.refresh_from_db()
        listing_a.refresh_from_db()
        listing_b.refresh_from_db()
        untouched.refresh_from_db()
        nora_listing.refresh_from_db()
        other_listing.refresh_from_db()
        blank_product.refresh_from_db()
        self.assertEqual(catalog.inventory_vendor_id, "OLD-CAT")
        self.assertEqual(catalog.vendor_sku, "WK-CAT")
        self.assertEqual(catalog_row.vendor_id_raw, "OLD-CAT")
        self.assertEqual(other_catalog.inventory_vendor_id, "OLD-B")
        self.assertEqual(listing_a.vendor_id, "OLD-A")
        self.assertEqual(listing_a.sku, "LASOO-A")
        self.assertEqual(listing_b.vendor_id, "OLD-B")
        self.assertEqual(untouched.vendor_id, "OLD-A")
        self.assertEqual(nora_listing.vendor_id, "OLD-NORA")
        self.assertEqual(other_listing.vendor_id, "OLD-A")
        self.assertEqual(blank_product.inventory_vendor_id, "")
        self.assertEqual(blank_product.vendor_sku, "LASOO-A")

    def test_matching_rows_update_listing_catalog_and_upload_row(self):
        catalog = self._product(self.user, self.wallkoala, "WK-CAT", "OLD-CAT")
        self._mapping(self.store_a, catalog, "WK-CAT", "MID-A")
        catalog_row = self._catalog_row(self.store_a, catalog, "WK-CAT", "OLD-CAT")
        deleted = self._catalog_row(
            self.store_a, catalog, "WK-CAT", "STALE-CAT", deleted=True, row_number=2,
        )
        listing = self._listing(self.store_a, "LASOO-A", "wk-old")
        other_store_listing = self._listing(self.store_b, "LASOO-A", "wk-old")
        other_user_listing = self._listing(self.other_store, "LASOO-A", "wk-old")
        blank_product = self._product(self.user, self.wallkoala, "LASOO-A", "")
        self._mapping(self.store_a, blank_product, "LASOO-A", "MID-BLANK")
        path = _workbook({
            "Store A": [
                ("WK-CAT", "OLD-CAT", "NEW-CAT"),
                ("LASOO-A", "WK-OLD", "WK-NEW"),
            ],
        })
        self._run(path)

        catalog.refresh_from_db()
        catalog_row.refresh_from_db()
        deleted.refresh_from_db()
        listing.refresh_from_db()
        other_store_listing.refresh_from_db()
        other_user_listing.refresh_from_db()
        blank_product.refresh_from_db()
        self.assertEqual(catalog.inventory_vendor_id, "NEW-CAT")
        self.assertEqual(catalog.vendor_sku, "WK-CAT")
        self.assertEqual(catalog_row.vendor_id_raw, "NEW-CAT")
        self.assertEqual(deleted.vendor_id_raw, "STALE-CAT")
        self.assertEqual(listing.vendor_id, "WK-NEW")
        self.assertEqual(listing.sku, "LASOO-A")
        self.assertEqual(other_store_listing.vendor_id, "wk-old")
        self.assertEqual(other_user_listing.vendor_id, "wk-old")
        self.assertEqual(listing.source_vendor_code, "wallkoala")
        self.assertEqual(blank_product.inventory_vendor_id, "")
        self.assertEqual(blank_product.vendor_sku, "LASOO-A")

    def test_new_suffix_is_not_stripped_from_the_old_id(self):
        listing = self._listing(self.store_a, "LASOO-A", "WK-1")
        path = _workbook({
            "Store A": [("LASOO-A", "WK-1-New", "WK-NEW")],
        })
        with self.assertRaises(CommandError) as raised:
            self._run(path)
        self.assertIn("Nothing was changed", str(raised.exception))
        listing.refresh_from_db()
        self.assertEqual(listing.vendor_id, "WK-1")

    def test_one_bad_row_writes_nothing(self):
        good = self._listing(self.store_a, "LASOO-A", "OLD-A")
        bad = self._listing(self.store_a, "LASOO-B", "OLD-B")
        path = _workbook({
            "Store A": [
                ("LASOO-A", "OLD-A", "NEW-A"),
                ("LASOO-B", "NOT-OLD-B", "NEW-B"),
            ],
        })
        with self.assertRaises(CommandError):
            self._run(path)
        good.refresh_from_db()
        bad.refresh_from_db()
        self.assertEqual(good.vendor_id, "OLD-A")
        self.assertEqual(bad.vendor_id, "OLD-B")

    def test_catalog_vendor_id_conflict_blocks_the_listing(self):
        listing = self._listing(self.store_a, "LASOO-A", "OLD-A")
        product = self._product(self.user, self.wallkoala, "LASOO-A", "OTHER-A")
        self._mapping(self.store_a, product, "LASOO-A", "MID-A")
        path = _workbook({
            "Store A": [("LASOO-A", "OLD-A", "NEW-A")],
        })
        with self.assertRaises(CommandError) as raised:
            self._run(path)
        self.assertIn("catalog Vendor ID is OTHER-A", str(raised.exception))
        listing.refresh_from_db()
        product.refresh_from_db()
        self.assertEqual(listing.vendor_id, "OLD-A")
        self.assertEqual(product.inventory_vendor_id, "OTHER-A")

    def test_same_catalog_product_cannot_get_two_new_ids(self):
        product = self._product(self.user, self.wallkoala, "WK-1", "OLD-1")
        self._mapping(self.store_a, product, "SKU-A", "MID-A")
        self._mapping(self.store_b, product, "SKU-B", "MID-B")
        listing_a = self._listing(self.store_a, "SKU-A", "OLD-1")
        listing_b = self._listing(self.store_b, "SKU-B", "OLD-1")
        path = _workbook({
            "Store A": [("SKU-A", "OLD-1", "NEW-1")],
            "Store B": [("SKU-B", "OLD-1", "NEW-2")],
        })
        with self.assertRaises(CommandError):
            self._run(path)
        product.refresh_from_db()
        listing_a.refresh_from_db()
        listing_b.refresh_from_db()
        self.assertEqual(product.inventory_vendor_id, "OLD-1")
        self.assertEqual(listing_a.vendor_id, "OLD-1")
        self.assertEqual(listing_b.vendor_id, "OLD-1")

    def test_parent_sku_is_not_a_match(self):
        product = self._product(self.user, self.wallkoala, "SUP-1", "OLD-1")
        ProductMapping.objects.create(
            store=self.store_a,
            product=product,
            marketplace_child_sku="CHILD-1",
            marketplace_parent_sku="PARENT-1",
            marketplace_id="MID-PARENT",
            is_active=True,
        )
        path = _workbook({
            "Store A": [("PARENT-1", "OLD-1", "NEW-1")],
        })
        with self.assertRaises(CommandError) as raised:
            self._run(path)
        self.assertIn("was not found", str(raised.exception))
        product.refresh_from_db()
        self.assertEqual(product.inventory_vendor_id, "OLD-1")

    def test_inactive_mapping_is_not_updated(self):
        listing = self._listing(self.store_a, "LASOO-A", "OLD-A")
        product = self._product(self.user, self.wallkoala, "LASOO-A", "OLD-A")
        ProductMapping.objects.create(
            store=self.store_a,
            product=product,
            marketplace_child_sku="LASOO-A",
            marketplace_id="MID-OFF",
            is_active=False,
        )
        path = _workbook({
            "Store A": [("LASOO-A", "OLD-A", "NEW-A")],
        })
        self._run(path)
        listing.refresh_from_db()
        product.refresh_from_db()
        self.assertEqual(listing.vendor_id, "NEW-A")
        self.assertEqual(product.inventory_vendor_id, "OLD-A")

    def test_excel_number_sku_matches_the_text_sku(self):
        listing = self._listing(self.store_a, "1001", "OLD-1")
        path = _workbook({
            "Store A": [(1001.0, "OLD-1", "NEW-1")],
        })
        self._run(path)
        listing.refresh_from_db()
        self.assertEqual(listing.vendor_id, "NEW-1")
        self.assertEqual(listing.sku, "1001")

    def test_duplicate_new_ids_that_differ_by_case_write_nothing(self):
        listing = self._listing(self.store_a, "LASOO-A", "OLD-A")
        path = _workbook({
            "Store A": [
                ("LASOO-A", "OLD-A", "NEW-A"),
                ("LASOO-A", "old-a", "new-a"),
            ],
        })
        with self.assertRaises(CommandError):
            self._run(path)
        listing.refresh_from_db()
        self.assertEqual(listing.vendor_id, "OLD-A")

    def test_repeated_identical_row_updates_once(self):
        listing = self._listing(self.store_a, "LASOO-A", "OLD-A")
        path = _workbook({
            "Store A": [
                ("LASOO-A", "OLD-A", "NEW-A"),
                ("lasoo-a", "old-a", "NEW-A"),
            ],
        })
        self._run(path)
        listing.refresh_from_db()
        self.assertEqual(listing.vendor_id, "NEW-A")

    def test_second_run_leaves_the_new_id_in_place(self):
        listing = self._listing(self.store_a, "LASOO-A", "OLD-A")
        rows = {"Store A": [("LASOO-A", "OLD-A", "NEW-A")]}
        self._run(_workbook(rows))
        self._run(_workbook(rows))
        listing.refresh_from_db()
        self.assertEqual(listing.vendor_id, "NEW-A")

    def test_dry_run_does_not_write(self):
        listing = self._listing(self.store_a, "LASOO-A", "OLD-A")
        path = _workbook({
            "Store A": [("LASOO-A", "OLD-A", "NEW-A")],
        })
        self._run(path, dry_run=True)
        listing.refresh_from_db()
        self.assertEqual(listing.vendor_id, "OLD-A")

    def test_unknown_sheet_changes_nothing(self):
        listing = self._listing(self.store_a, "LASOO-A", "OLD-A")
        path = _workbook({
            "Store A": [("LASOO-A", "OLD-A", "NEW-A")],
            "Nope": [("LASOO-A", "OLD-A", "OTHER-A")],
        })
        with self.assertRaises(CommandError) as raised:
            self._run(path)
        self.assertIn("Nothing was changed", str(raised.exception))
        listing.refresh_from_db()
        self.assertEqual(listing.vendor_id, "OLD-A")

    def test_two_listings_with_the_same_sku_and_old_id_both_update(self):
        first = self._listing(self.store_a, "LASOO-A", "OLD-A")
        second = StoreListing.objects.create(
            user=self.user,
            store=self.store_a,
            external_product_key="LASOO-A-2",
            external_variant_key="LASOO-A-2",
            sku="LASOO-A",
            title="LASOO-A",
            vendor_id="OLD-A",
            source_vendor_code="wallkoala",
        )
        path = _workbook({
            "Store A": [("LASOO-A", "OLD-A", "NEW-A")],
        })
        self._run(path)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.vendor_id, "NEW-A")
        self.assertEqual(second.vendor_id, "NEW-A")
        self.assertEqual(first.sku, "LASOO-A")
        self.assertEqual(second.sku, "LASOO-A")

    def test_old_vendor_id_stored_on_vendor_sku_is_copied_onto_the_catalog_id(self):
        product = self._product(self.user, self.wallkoala, "OLD-CAT", "")
        self._mapping(self.store_a, product, "LASOO-A", "MID-SKU")
        listing = self._listing(self.store_a, "LASOO-A", "OLD-CAT")
        path = _workbook({
            "Store A": [("LASOO-A", "OLD-CAT", "NEW-CAT")],
        })
        self._run(path)
        product.refresh_from_db()
        listing.refresh_from_db()
        self.assertEqual(product.inventory_vendor_id, "NEW-CAT")
        self.assertEqual(product.vendor_sku, "OLD-CAT")
        self.assertEqual(listing.vendor_id, "NEW-CAT")

    def test_variant_key_is_not_used_as_the_sku(self):
        listing = self._listing(self.store_a, "LASOO-A", "OLD-A")
        listing.sku = ""
        listing.external_variant_key = "LASOO-A"
        listing.external_product_key = "LASOO-A"
        listing.save(update_fields=["sku", "external_variant_key", "external_product_key"])
        path = _workbook({
            "Store A": [("LASOO-A", "OLD-A", "NEW-A")],
        })
        with self.assertRaises(CommandError) as raised:
            self._run(path)
        self.assertIn("was not found", str(raised.exception))
        listing.refresh_from_db()
        self.assertEqual(listing.vendor_id, "OLD-A")
        self.assertEqual(listing.sku, "")

    def test_two_listings_with_the_same_sku_and_different_ids_write_nothing(self):
        first = self._listing(self.store_a, "LASOO-A", "OLD-A")
        second = StoreListing.objects.create(
            user=self.user,
            store=self.store_a,
            external_product_key="LASOO-A-2",
            external_variant_key="LASOO-A-2",
            sku="LASOO-A",
            title="LASOO-A",
            vendor_id="OTHER-A",
            source_vendor_code="wallkoala",
        )
        path = _workbook({
            "Store A": [("LASOO-A", "OLD-A", "NEW-A")],
        })
        with self.assertRaises(CommandError):
            self._run(path)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.vendor_id, "OLD-A")
        self.assertEqual(second.vendor_id, "OTHER-A")
