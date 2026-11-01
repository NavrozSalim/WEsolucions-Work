"""Set Wallkoala Vendor IDs from a workbook that has one sheet per store.

Each sheet is named for the store. Columns are SKU, Old Vendor ID, and New
Vendor ID. A row updates the Wallkoala listing on that store whose SKU matches,
and only when the saved Vendor ID is still the Old Vendor ID. The same row
also updates the catalog Vendor ID (product inventory Vendor ID and catalog
upload Vendor ID) when that saved value is the Old Vendor ID.

Matching ignores letter case, surrounding spaces, and a trailing ``.0`` that
Excel adds to whole numbers. It does not ignore any other difference. A
trailing ``-New`` is part of the ID. The value stored is the New Vendor ID
text from the file.

Nothing is written unless every row matches. A dry run prints the same plan
and does not write.

Usage (inside the backend container on the main server):

  python manage.py rename_wallkoala_vendor_ids --email you@example.com --file /root/wallkoala-vendor-ids.xlsx --dry-run
  python manage.py rename_wallkoala_vendor_ids --email you@example.com --file /root/wallkoala-vendor-ids.xlsx
"""
from __future__ import annotations

import re
from collections import defaultdict

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from catalog.models import CatalogUploadRow, ProductMapping
from listings.models import StoreListing
from products.models import Product
from scrapers.vevor_au_ingest import clean_id
from scrapers.wallkoala_ingest import is_wallkoala_vendor_code
from stores.models import Store
from users.org_scope import organization_user_ids

User = get_user_model()
_VENDOR_ID_MAX = 255


def _header_token(value) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _norm(value) -> str:
    return clean_id(value).lower()


def _ids_match(saved, expected) -> bool:
    left = _norm(saved)
    right = _norm(expected)
    return bool(left) and left == right


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
        if previous is not None and clean_id(previous) != new_id:
            problems.append(
                f"{sheet_name} line {line_no}: SKU {sku} / {old_id} is already mapped to {previous}, not {new_id}"
            )
            continue
        seen[key] = new_id
        if previous is None:
            parsed.append((sku, old_id, new_id))
    if problems:
        raise CommandError("Nothing was changed.\n" + "\n".join(problems))
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


def _is_wallkoala_product(product) -> bool:
    code = getattr(getattr(product, "vendor", None), "code", "") or ""
    return is_wallkoala_vendor_code(code)


def _row_is_wallkoala(row) -> bool:
    code = getattr(getattr(row, "vendor", None), "code", "") or ""
    if is_wallkoala_vendor_code(code):
        return True
    product = getattr(row, "product", None)
    if product is not None and _is_wallkoala_product(product):
        return True
    return is_wallkoala_vendor_code(getattr(row, "vendor_name_raw", ""))


def _listing_is_wallkoala(listing) -> bool:
    return is_wallkoala_vendor_code(getattr(listing, "source_vendor_code", None))


def _listing_sku_keys(listing) -> list[str]:
    key = _norm(listing.sku)
    return [key] if key else []


def _mapping_sku_keys(mapping) -> list[str]:
    product = mapping.product
    values = [
        mapping.marketplace_child_sku,
        getattr(product, "vendor_sku", "") if product is not None else "",
    ]
    keys = []
    for value in values:
        key = _norm(value)
        if key and key not in keys:
            keys.append(key)
    return keys


def _catalog_row_sku_keys(row) -> list[str]:
    values = [row.marketplace_child_sku_raw, row.vendor_sku_raw]
    keys = []
    for value in values:
        key = _norm(value)
        if key and key not in keys:
            keys.append(key)
    return keys


def _dedupe(rows):
    seen = set()
    out = []
    for row in rows:
        if row.id in seen:
            continue
        seen.add(row.id)
        out.append(row)
    return out


def _plan_saved_id(saved, old_id, new_id) -> str:
    """Return update, keep, or conflict for a stored Vendor ID."""
    if _ids_match(saved, old_id) or _ids_match(saved, new_id):
        if clean_id(saved) == clean_id(new_id):
            return "keep"
        return "update"
    return "conflict"


def _product_plan(product, old_id, new_id) -> str:
    """How a catalog product's Vendor ID relates to this file row.

    ``ignore`` means this product is Wallkoala but its Vendor ID is not stored
    on the product (the listing SKU is not the Vendor ID). Those products are
    left unchanged and do not block a listing whose Vendor ID does match.
    """
    saved = clean_id(product.inventory_vendor_id)
    if saved:
        return _plan_saved_id(saved, old_id, new_id)
    sku = clean_id(product.vendor_sku)
    if _ids_match(sku, old_id):
        return "update"
    if _ids_match(sku, new_id):
        return "keep"
    return "ignore"


def _apply_ids(model, ids: list, field: str, new_id: str) -> None:
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        model.objects.filter(id__in=chunk).update(**{field: new_id})


class Command(BaseCommand):
    help = "Set Wallkoala Vendor ID from a workbook with one sheet per store. Writes nothing if any row does not match."

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            required=True,
            help="Account email that owns the stores",
        )
        parser.add_argument(
            "--file",
            required=True,
            help="xlsx with one sheet per store and columns SKU, Old Vendor ID, New Vendor ID",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the plan and write nothing",
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
                    "Nothing was changed.\n"
                    f"Sheet {sheet_name!r} does not match a store for {user.email}. Stores: {names}"
                )
            if len(matched) > 1:
                names = ", ".join(store.name for store in matched)
                raise CommandError(
                    "Nothing was changed.\n"
                    f"Sheet {sheet_name!r} matches more than one store: {names}"
                )
            sheets.append((matched[0], parsed))
        if not sheets:
            raise CommandError("Nothing was changed.\nThe workbook has no SKU rows.")

        store_ids = [store.id for store, _rows in sheets]
        listings_by_store = defaultdict(lambda: defaultdict(list))
        for listing in StoreListing.objects.filter(store_id__in=store_ids).only(
            "id",
            "store_id",
            "vendor_id",
            "sku",
            "source_vendor_code",
            "external_variant_key",
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

        rows_by_store = defaultdict(lambda: defaultdict(list))
        store_id_set = set(store_ids)
        catalog_rows = (
            CatalogUploadRow.objects.filter(
                Q(store_id__in=store_ids) | Q(catalog_upload__store_id__in=store_ids)
            )
            .exclude(sync_status=CatalogUploadRow.SyncStatus.DELETED)
            .select_related("vendor", "product", "product__vendor", "catalog_upload")
        )
        for row in catalog_rows:
            if row.store_id in store_id_set:
                store_id = row.store_id
            elif row.catalog_upload.store_id in store_id_set:
                store_id = row.catalog_upload.store_id
            else:
                continue
            for key in _catalog_row_sku_keys(row):
                rows_by_store[store_id][key].append(row)

        product_new: dict = {}
        listing_new: dict = {}
        row_new: dict = {}
        problems: list[str] = []
        changes: list[str] = []
        per_store = []

        for store, parsed in sheets:
            catalog_count = 0
            listing_count = 0
            row_count = 0
            for sku, old_id, new_id in parsed:
                sku_key = _norm(sku)
                listing_hits = listings_by_store[store.id].get(sku_key, [])
                mapping_hits = mappings_by_store[store.id].get(sku_key, [])
                row_hits = rows_by_store[store.id].get(sku_key, [])
                wall_listings = _dedupe(row for row in listing_hits if _listing_is_wallkoala(row))
                wall_products = _dedupe(
                    mapping.product
                    for mapping in mapping_hits
                    if mapping.product_id and _is_wallkoala_product(mapping.product)
                )
                wall_rows = _dedupe(row for row in row_hits if _row_is_wallkoala(row))
                if not wall_listings and not wall_products and not wall_rows:
                    if listing_hits or mapping_hits or row_hits:
                        problems.append(f"{store.name}: SKU {sku} is not a Wallkoala product")
                    else:
                        problems.append(f"{store.name}: SKU {sku} was not found")
                    continue

                row_problems = []
                row_ok = False

                for listing in wall_listings:
                    decision = _plan_saved_id(listing.vendor_id, old_id, new_id)
                    if decision == "conflict":
                        saved = clean_id(listing.vendor_id) or "(blank)"
                        row_problems.append(
                            f"{store.name}: SKU {sku} listing Vendor ID is {saved}, "
                            f"file Old Vendor ID is {old_id}"
                        )
                        continue
                    row_ok = True
                    if decision != "update":
                        continue
                    previous = listing_new.get(listing.id)
                    if previous is not None and clean_id(previous) != new_id:
                        row_problems.append(
                            f"{store.name}: listing SKU {listing.sku} would be set to both "
                            f"{previous} and {new_id}"
                        )
                        continue
                    if previous is None:
                        listing_new[listing.id] = new_id
                        listing_count += 1
                        saved = clean_id(listing.vendor_id) or "(blank)"
                        changes.append(
                            f"{store.name}: listing SKU {sku} Vendor ID {saved} -> {new_id}"
                        )

                for product in wall_products:
                    decision = _product_plan(product, old_id, new_id)
                    if decision == "ignore":
                        continue
                    if decision == "conflict":
                        saved = clean_id(product.inventory_vendor_id) or "(blank)"
                        row_problems.append(
                            f"{store.name}: SKU {sku} catalog Vendor ID is {saved}, "
                            f"file Old Vendor ID is {old_id}"
                        )
                        continue
                    row_ok = True
                    if decision != "update":
                        continue
                    previous = product_new.get(product.id)
                    if previous is not None and clean_id(previous[0]) != new_id:
                        row_problems.append(
                            f"Catalog SKU {sku} would be set to {previous[0]} from {previous[1]} "
                            f"and {new_id} from {store.name}"
                        )
                        continue
                    if previous is None:
                        product_new[product.id] = (new_id, store.name)
                        catalog_count += 1
                        saved = clean_id(product.inventory_vendor_id) or clean_id(product.vendor_sku) or "(blank)"
                        changes.append(
                            f"{store.name}: catalog SKU {sku} Vendor ID {saved} -> {new_id}"
                        )

                for catalog_row in wall_rows:
                    saved = clean_id(catalog_row.vendor_id_raw)
                    if not saved:
                        continue
                    decision = _plan_saved_id(saved, old_id, new_id)
                    if decision == "conflict":
                        row_problems.append(
                            f"{store.name}: SKU {sku} catalog file row {catalog_row.row_number} "
                            f"Vendor ID is {saved}, file Old Vendor ID is {old_id}"
                        )
                        continue
                    row_ok = True
                    if decision != "update":
                        continue
                    previous = row_new.get(catalog_row.id)
                    if previous is not None and clean_id(previous) != new_id:
                        row_problems.append(
                            f"{store.name}: catalog file row {catalog_row.row_number} for SKU {sku} "
                            f"would be set to both {previous} and {new_id}"
                        )
                        continue
                    if previous is None:
                        row_new[catalog_row.id] = new_id
                        row_count += 1
                        changes.append(
                            f"{store.name}: catalog file row {catalog_row.row_number} "
                            f"SKU {sku} Vendor ID {saved} -> {new_id}"
                        )

                if row_problems:
                    problems.extend(row_problems)
                elif not row_ok:
                    problems.append(
                        f"{store.name}: SKU {sku} has no Wallkoala Vendor ID equal to {old_id}"
                    )
            per_store.append((store.name, catalog_count, listing_count, row_count))

        if problems:
            raise CommandError("Nothing was changed.\n" + "\n".join(problems))

        prefix = "Dry run. Nothing was written. " if options["dry_run"] else ""
        for store_name, catalog_count, listing_count, row_count in per_store:
            self.stdout.write(
                f"{prefix}{store_name}: {listing_count} listing Vendor IDs, "
                f"{catalog_count} catalog Vendor IDs, {row_count} catalog file rows."
            )
        for line in changes:
            self.stdout.write(f"  {line}")
        if not changes:
            self.stdout.write(f"{prefix}Every Vendor ID was already the new value.")

        if options["dry_run"]:
            return

        by_product = defaultdict(list)
        by_listing = defaultdict(list)
        by_row = defaultdict(list)
        for product_id, (new_id, _store_name) in product_new.items():
            by_product[new_id].append(product_id)
        for listing_id, new_id in listing_new.items():
            by_listing[new_id].append(listing_id)
        for row_id, new_id in row_new.items():
            by_row[new_id].append(row_id)
        with transaction.atomic():
            for new_id, ids in by_product.items():
                _apply_ids(Product, ids, "inventory_vendor_id", new_id)
            for new_id, ids in by_listing.items():
                _apply_ids(StoreListing, ids, "vendor_id", new_id)
            for new_id, ids in by_row.items():
                _apply_ids(CatalogUploadRow, ids, "vendor_id_raw", new_id)
