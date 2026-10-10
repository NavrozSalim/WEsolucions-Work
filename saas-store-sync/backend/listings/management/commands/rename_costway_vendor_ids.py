"""Set Costway Vendor IDs from a workbook that has one sheet per store.

Each sheet is named for the store. Columns are SKU, Old Vendor ID, and New
Vendor ID. A row updates the Costway product on that store whose SKU matches,
and only when the saved Vendor ID is still the Old Vendor ID. A trailing
``-New`` on the Old Vendor ID is ignored, so ``PV10568-New`` matches a saved
Vendor ID of ``PV10568``. The SKU column is still compared exactly.

Usage (inside the backend container on the main server):

  python manage.py rename_costway_vendor_ids --email afraaz.prettyandpractical@gmail.com --file /root/costway-vendor-ids.xlsx
  python manage.py rename_costway_vendor_ids --email afraaz.prettyandpractical@gmail.com --file /root/costway-vendor-ids.xlsx --dry-run
"""
from __future__ import annotations

import re
from collections import defaultdict

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from catalog.models import ProductMapping
from listings.models import StoreListing
from products.models import Product
from scrapers.costway_au_ingest import (
    clean_id,
    is_costway_feed_listing,
    is_costway_product_url,
    is_costway_vendor_code,
)
from stores.models import Store
from users.org_scope import organization_user_ids

User = get_user_model()
_VENDOR_ID_MAX = 255


def _header_token(value) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _norm(value) -> str:
    return clean_id(value).lower()


def _old_vendor_matches(saved, file_old) -> bool:
    """True when the saved Vendor ID is the file's Old Vendor ID.

    Pretty & Practical stores the bare code (``PV10568``) while the sheet
    appends ``-New`` (``PV10568-New``). That suffix is not part of the saved id.
    """
    saved_key = _norm(saved)
    file_key = _norm(file_old)
    if saved_key == file_key:
        return True
    return file_key.endswith("-new") and saved_key == file_key[:-4]


def _label(value) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def _read_workbook(path: str) -> list[tuple[str, list[tuple]]]:
    lower = path.lower()
    if not lower.endswith((".xlsx", ".xlsm")):
        raise CommandError("File must be an Excel workbook (.xlsx) with one sheet per store.")
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheets = []
        for sheet in workbook.worksheets:
            if sheet.sheet_state and sheet.sheet_state != "visible":
                continue
            sheets.append((sheet.title, [tuple(row) for row in sheet.iter_rows(values_only=True)]))
        return sheets
    finally:
        workbook.close()


def _rows_from_sheet(sheet_name: str, rows: list[tuple]) -> list[tuple[str, str, str]]:
    header_at = None
    sku_idx = old_idx = new_idx = None
    for index, row in enumerate(rows[:15]):
        tokens = [_header_token(cell) for cell in row]
        if "sku" in tokens and "oldvendorid" in tokens and "newvendorid" in tokens:
            header_at = index
            sku_idx = tokens.index("sku")
            old_idx = tokens.index("oldvendorid")
            new_idx = tokens.index("newvendorid")
            break
    if header_at is None:
        raise CommandError(
            f"Sheet {sheet_name!r} needs columns named SKU, Old Vendor ID, and New Vendor ID."
        )

    parsed: list[tuple[str, str, str]] = []
    problems: list[str] = []
    seen: dict[tuple[str, str], str] = {}
    for line_no, row in enumerate(rows[header_at + 1:], start=header_at + 2):
        sku = clean_id(row[sku_idx]) if sku_idx < len(row) else ""
        old_id = clean_id(row[old_idx]) if old_idx < len(row) else ""
        new_id = clean_id(row[new_idx]) if new_idx < len(row) else ""
        if not sku and not old_id and not new_id:
            continue
        if not sku or not old_id or not new_id:
            problems.append(
                f"{sheet_name} line {line_no}: SKU, Old Vendor ID, and New Vendor ID are required"
            )
            continue
        if len(new_id) > _VENDOR_ID_MAX:
            problems.append(
                f"{sheet_name} line {line_no}: New Vendor ID is longer than {_VENDOR_ID_MAX} characters"
            )
            continue
        key = (_norm(sku), _norm(old_id))
        previous = seen.get(key)
        if previous and previous.lower() != new_id.lower():
            problems.append(
                f"{sheet_name} line {line_no}: SKU {sku} / {old_id} is already mapped to {previous}, not {new_id}"
            )
            continue
        seen[key] = new_id
        parsed.append((sku, old_id, new_id))
    if problems:
        raise CommandError("Fix the spreadsheet before updating:\n" + "\n".join(problems))
    return parsed


def _sheet_matches_store(store, sheet_name: str) -> bool:
    sheet = _label(sheet_name)
    labels = [_label(store.name)]
    if store.kogan_tab_name:
        labels.append(_label(store.kogan_tab_name))
    if sheet in labels:
        return True
    raw = str(sheet_name or "").strip()
    if len(raw) >= 31:
        return any(label.startswith(sheet) for label in labels if label)
    return False


def _is_costway_product(product) -> bool:
    code = getattr(getattr(product, "vendor", None), "code", "") or ""
    if is_costway_vendor_code(code):
        return True
    return is_costway_product_url(getattr(product, "vendor_url", ""))


def _listing_is_costway(listing) -> bool:
    return is_costway_feed_listing(
        source_vendor_code=listing.source_vendor_code,
        vendor_url=listing.vendor_url,
        vendor_id=listing.vendor_id,
        sku=listing.sku,
        variant_key=listing.external_variant_key,
        product_key=listing.external_product_key,
    )


def _listing_sku_keys(listing) -> list[str]:
    values = [listing.sku]
    if not clean_id(listing.sku):
        values.append(listing.external_variant_key)
    keys = []
    for value in values:
        key = _norm(value)
        if key and key not in keys:
            keys.append(key)
    return keys


def _mapping_sku_keys(mapping) -> list[str]:
    product = mapping.product
    values = [
        mapping.marketplace_child_sku,
        mapping.marketplace_parent_sku,
        getattr(product, "vendor_sku", "") if product is not None else "",
    ]
    keys = []
    for value in values:
        key = _norm(value)
        if key and key not in keys:
            keys.append(key)
    return keys


def _saved_vendor_id(product) -> str:
    saved = clean_id(product.inventory_vendor_id)
    if saved:
        return saved
    return clean_id(product.vendor_sku)


def _apply_ids(model, ids: list, field: str, new_id: str) -> int:
    updated = 0
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        updated += model.objects.filter(id__in=chunk).update(**{field: new_id})
    return updated


class Command(BaseCommand):
    help = "Set Costway Vendor ID from a workbook with one sheet per store."

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            required=True,
            help="Account email, for example afraaz.prettyandpractical@gmail.com",
        )
        parser.add_argument(
            "--file",
            required=True,
            help="xlsx with one sheet per store and columns SKU, Old Vendor ID, New Vendor ID",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report matches without writing",
        )

    def handle(self, *args, **options):
        email = (options["email"] or "").strip()
        user = User.objects.filter(email__iexact=email).first()
        if user is None:
            raise CommandError(f"No user with email {email}")
        user_ids = list(organization_user_ids(user))
        if user.id not in user_ids:
            user_ids.append(user.id)

        stores = list(
            Store.objects.filter(user_id__in=user_ids, orphaned_at__isnull=True).only(
                "id", "name", "kogan_tab_name",
            )
        )
        sheets = []
        for sheet_name, rows in _read_workbook(options["file"]):
            parsed = _rows_from_sheet(sheet_name, rows)
            if not parsed:
                continue
            matched = [store for store in stores if _sheet_matches_store(store, sheet_name)]
            if not matched:
                names = ", ".join(sorted(store.name for store in stores)) or "(none)"
                raise CommandError(
                    f"Sheet {sheet_name!r} does not match a store for {user.email}. Stores: {names}"
                )
            if len(matched) > 1:
                names = ", ".join(store.name for store in matched)
                raise CommandError(f"Sheet {sheet_name!r} matches more than one store: {names}")
            sheets.append((matched[0], parsed))
        if not sheets:
            raise CommandError("The workbook has no SKU rows.")

        store_ids = [store.id for store, _rows in sheets]
        listings_by_store = defaultdict(lambda: defaultdict(list))
        for listing in StoreListing.objects.filter(store_id__in=store_ids).only(
            "id",
            "store_id",
            "vendor_id",
            "sku",
            "source_vendor_code",
            "vendor_url",
            "external_variant_key",
            "external_product_key",
        ):
            for key in _listing_sku_keys(listing):
                listings_by_store[listing.store_id][key].append(listing)

        mappings_by_store = defaultdict(lambda: defaultdict(list))
        for mapping in (
            ProductMapping.objects.filter(store_id__in=store_ids, is_active=True)
            .select_related("product", "product__vendor")
        ):
            for key in _mapping_sku_keys(mapping):
                mappings_by_store[mapping.store_id][key].append(mapping)

        product_new: dict = {}
        listing_new: dict = {}
        missing = []
        mismatched = []
        skipped_vendor = []
        per_store = []

        for store, parsed in sheets:
            catalog_count = 0
            listing_count = 0
            for sku, old_id, new_id in parsed:
                sku_key = _norm(sku)
                listing_hits = listings_by_store[store.id].get(sku_key, [])
                mapping_hits = mappings_by_store[store.id].get(sku_key, [])
                costway_listings = [row for row in listing_hits if _listing_is_costway(row)]
                costway_mappings = [
                    row for row in mapping_hits
                    if row.product_id and _is_costway_product(row.product)
                ]
                matched_listings = [
                    row for row in costway_listings if _old_vendor_matches(row.vendor_id, old_id)
                ]
                matched_mappings = [
                    row for row in costway_mappings
                    if _old_vendor_matches(_saved_vendor_id(row.product), old_id)
                ]
                if not matched_listings and not matched_mappings:
                    if costway_listings or costway_mappings:
                        saved = sorted({
                            clean_id(row.vendor_id) or "(blank)"
                            for row in costway_listings
                        } | {
                            _saved_vendor_id(row.product) or "(blank)"
                            for row in costway_mappings
                        })
                        mismatched.append(
                            f"{store.name}: SKU {sku} has Vendor ID {', '.join(saved)}, file has {old_id}"
                        )
                    elif listing_hits or mapping_hits:
                        skipped_vendor.append(f"{store.name}: SKU {sku} is not a Costway product")
                    else:
                        missing.append(f"{store.name}: SKU {sku}")
                    continue

                for listing in matched_listings:
                    if _norm(listing.vendor_id) == _norm(new_id):
                        continue
                    previous = listing_new.get(listing.id)
                    if previous and previous.lower() != new_id.lower():
                        raise CommandError(
                            f"SKU {listing.sku} on {store.name} would be set to both {previous} and {new_id}."
                        )
                    listing_new[listing.id] = new_id
                    listing_count += 1
                for mapping in matched_mappings:
                    product = mapping.product
                    if _norm(product.inventory_vendor_id) == _norm(new_id):
                        continue
                    previous = product_new.get(product.id)
                    if previous and previous[0].lower() != new_id.lower():
                        raise CommandError(
                            f"Catalog SKU {sku} would be set to {previous[0]} from {previous[1]} "
                            f"and {new_id} from {store.name}. Nothing was changed."
                        )
                    if product.id not in product_new:
                        catalog_count += 1
                    product_new[product.id] = (new_id, store.name)
            per_store.append((store.name, catalog_count, listing_count))

        if not options["dry_run"]:
            by_product = defaultdict(list)
            by_listing = defaultdict(list)
            for product_id, (new_id, _store_name) in product_new.items():
                by_product[new_id].append(product_id)
            for listing_id, new_id in listing_new.items():
                by_listing[new_id].append(listing_id)
            with transaction.atomic():
                for new_id, ids in by_product.items():
                    _apply_ids(Product, ids, "inventory_vendor_id", new_id)
                for new_id, ids in by_listing.items():
                    _apply_ids(StoreListing, ids, "vendor_id", new_id)

        prefix = "Dry run. " if options["dry_run"] else ""
        for store_name, catalog_count, listing_count in per_store:
            self.stdout.write(
                f"{prefix}{store_name}: {catalog_count} catalog Vendor IDs, "
                f"{listing_count} listing Vendor IDs."
            )
        if mismatched:
            self.stdout.write(f"Old Vendor ID does not match ({len(mismatched)}):")
            for line in mismatched:
                self.stdout.write(f"  {line}")
        if skipped_vendor:
            self.stdout.write(f"Not Costway ({len(skipped_vendor)}):")
            for line in skipped_vendor:
                self.stdout.write(f"  {line}")
        if missing:
            self.stdout.write(f"Not found ({len(missing)}):")
            for line in missing:
                self.stdout.write(f"  {line}")
