"""Set Big W (MyDeal) stock to 0 for the Wallkoala SKUs listed in an Excel file.

The file has one sheet with a ``SKU`` column. Other columns are ignored.

Only the Wallkoala vendor on the chosen MyDeal store is changed. A SKU in the
file matches when the store has

  * a published listing (Created products) whose Vendor is Wallkoala, or
  * an active catalog mapping whose product vendor is Wallkoala.

A SKU that belongs to another vendor on this store is reported and left alone.
A SKU with no match is reported as unmatched.

What a real run does:

  1. Local stock 0: listings get inventory 0 (not unlimited) and catalog
     mappings get store stock 0.
  2. Big W stock 0:
     * API stores: the live MyDeal catalog is read and each matched SKU is
       sent with Quantity 0 through ``/products/quantityprice``. Every other
       variant of the same product is sent with its live quantity and price,
       so only the listed SKUs change.
     * Upload-template stores have no product API. A Wallkoala Stock/Status
       upload is queued for the inventory bot laptop (the same run as
       "Upload the file on the Marketplace"). The file it uploads now has
       stock 0 for these SKUs.
  3. One line is written to the store's Catalog Logs tab.

``--dry-run`` prints the same report and changes nothing. On an API store it
reads the live MyDeal catalog so the report shows which SKUs are already 0 on
Big W.

The next Wallkoala feed refresh or scrape sets stock from the vendor again.

Usage (inside the backend container on the main server):

  python manage.py zero_bigw_skus_from_file --store "Shemaya" --file /tmp/wallkoala-zero.xlsx --dry-run
  python manage.py zero_bigw_skus_from_file --store "Shemaya" --file /tmp/wallkoala-zero.xlsx

``--store`` is a store name or store id. Add ``--email`` to look only at the
stores of that account's organization when several stores share a name.
"""
from __future__ import annotations

import re
import uuid

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from catalog.models import ProductMapping
from listings.errors import MarketplaceError
from listings.models import ListingStatus, StoreListing
from scrapers.vevor_au_ingest import clean_id
from scrapers.wallkoala_ingest import is_wallkoala_vendor_code
from stores.models import Store
from users.org_scope import organization_user_ids

User = get_user_model()

VENDOR_LABEL = "Wallkoala"
LOG_ACTION = "bigw_zero_inventory_file"
_PUBLISHED = (
    ListingStatus.UPLOADED_STAGING,
    ListingStatus.UPLOADED_PRODUCTION,
)
_SAVE_BATCH = 200
_SHOW = 200


def _key(value) -> str:
    return clean_id(value).casefold()


def _label(value) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _header_token(value) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _read_skus(path: str) -> list[str]:
    """SKUs from the single visible sheet, in file order, without repeats."""
    if not str(path or "").lower().endswith((".xlsx", ".xlsm")):
        raise CommandError("File must be an Excel workbook (.xlsx).")
    from openpyxl import load_workbook

    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except FileNotFoundError as exc:
        raise CommandError(f"File not found: {path}") from exc
    try:
        sheets = []
        for sheet in workbook.worksheets:
            if sheet.sheet_state and sheet.sheet_state != "visible":
                continue
            sheets.append([tuple(row) for row in sheet.iter_rows(values_only=True)])
    finally:
        workbook.close()
    if not sheets:
        raise CommandError("Nothing was changed.\nThe workbook has no sheet.")
    if len(sheets) > 1:
        raise CommandError(
            "Nothing was changed.\nThe workbook has more than one sheet. Keep a single sheet."
        )
    rows = sheets[0]
    sku_at = header_at = None
    for index, row in enumerate(rows[:15]):
        tokens = [_header_token(cell) for cell in row]
        if "sku" in tokens:
            header_at = index
            sku_at = tokens.index("sku")
            break
    if header_at is None:
        raise CommandError("Nothing was changed.\nThe sheet needs a column named SKU.")
    skus: list[str] = []
    seen: set[str] = set()
    for row in rows[header_at + 1:]:
        sku = clean_id(row[sku_at]) if sku_at < len(row) else ""
        key = sku.casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        skus.append(sku)
    if not skus:
        raise CommandError("Nothing was changed.\nThe workbook has no SKU rows.")
    return skus


def _mydeal_q() -> Q:
    return Q(marketplace__code__iexact="mydeal") | Q(marketplace__name__icontains="mydeal")


def _store_line(store: Store) -> str:
    owner = getattr(store.user, "email", "") or "(no owner)"
    method = (store.mydeal_setup_method or "upload").strip().lower()
    return f"{store.name} | id {store.id} | owner {owner} | setup {method}"


def _pick_store(wanted: str, email: str) -> Store:
    base = Store.objects.filter(orphaned_at__isnull=True).select_related("marketplace", "user")
    raw = (wanted or "").strip()
    try:
        store_id = uuid.UUID(raw)
    except ValueError:
        store_id = None
    if store_id is not None:
        store = base.filter(id=store_id).first()
        if store is None:
            raise CommandError(f"Nothing was changed.\nNo store with id {raw}.")
        if not base.filter(id=store_id).filter(_mydeal_q()).exists():
            raise CommandError(
                f"Nothing was changed.\nStore {store.name} ({raw}) is not a MyDeal / Big W store."
            )
        return store

    stores = base.filter(_mydeal_q())
    if email:
        user = User.objects.filter(email__iexact=email).first()
        if user is None:
            raise CommandError(f"Nothing was changed.\nNo user with email {email}.")
        user_ids = list(organization_user_ids(user))
        if user.id not in user_ids:
            user_ids.append(user.id)
        stores = stores.filter(user_id__in=user_ids)
    stores = list(stores.order_by("name"))
    exact = [store for store in stores if _label(store.name) == _label(raw)]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        lines = "\n".join(f"  {_store_line(store)}" for store in exact)
        raise CommandError(
            "Nothing was changed.\n"
            f"More than one MyDeal / Big W store is named {raw!r}. "
            f"Run again with --store <id> (or --email):\n{lines}"
        )
    lines = "\n".join(f"  {_store_line(store)}" for store in stores) or "  (none)"
    raise CommandError(
        "Nothing was changed.\n"
        f"No MyDeal / Big W store is named {raw!r}. MyDeal / Big W stores:\n{lines}"
    )


def _uses_api(store: Store) -> bool:
    from listings.mydeal.unmapped_delete import uses_api

    return uses_api(store)


def _live_entries(store: Store) -> list[dict]:
    """Every buyable on the store's MyDeal seller account."""
    from listings.mydeal.unmapped_delete import _catalog_entries

    return _catalog_entries(store)


def _live_index(entries: list[dict]) -> dict[str, dict]:
    from listings.mydeal.unmapped_delete import _entry_keys

    out: dict[str, dict] = {}
    for entry in entries:
        for key in _entry_keys(entry):
            out.setdefault(key, entry)
    return out


def _push_zero_api(store: Store, entries: list[dict], zero_keys: set[str]) -> tuple[int, list[str]]:
    """Send Quantity 0 for ``zero_keys``. Returns (product groups sent ok, errors)."""
    from listings.mydeal.client import MyDealClient
    from listings.mydeal.unmapped_delete import _QUANTITY_BATCH, _quantity_groups

    groups = _quantity_groups(entries, zero_keys)
    if not groups:
        return 0, []
    client = MyDealClient(store)
    ok_groups = 0
    errors: list[str] = []
    for start in range(0, len(groups), _QUANTITY_BATCH):
        batch = groups[start:start + _QUANTITY_BATCH]
        result = client.update_quantity_price(batch)
        if result.ok:
            ok_groups += len(batch)
            continue
        skus = [
            buyable.get("SKU")
            for group in batch
            for buyable in group.get("BuyableProducts") or []
            if _key(buyable.get("SKU")) in zero_keys
        ]
        errors.append(
            f"Batch of {len(batch)} product(s) failed ({', '.join(map(str, skus[:10]))}"
            f"{'...' if len(skus) > 10 else ''}): {result.message or 'MyDeal push failed.'}"
        )
    return ok_groups, errors


class Command(BaseCommand):
    help = "Set Big W (MyDeal) stock to 0 for the Wallkoala SKUs in an Excel file."

    def add_arguments(self, parser):
        parser.add_argument("--store", required=True, help='MyDeal / Big W store name or id, for example "Shemaya"')
        parser.add_argument("--file", required=True, help="xlsx with a SKU column")
        parser.add_argument("--email", default="", help="Only look at stores of this account's organization")
        parser.add_argument("--dry-run", action="store_true", help="Print the report and change nothing")

    def _list(self, title: str, skus: list[str]) -> None:
        self.stdout.write(f"{title}: {len(skus)}")
        for sku in skus[:_SHOW]:
            self.stdout.write(f"  {sku}")
        if len(skus) > _SHOW:
            self.stdout.write(f"  ... and {len(skus) - _SHOW} more")

    def handle(self, *args, **options):
        dry_run = bool(options["dry_run"])
        store = _pick_store(options["store"], (options.get("email") or "").strip())
        skus = _read_skus(options["file"])
        api = _uses_api(store)

        # Local rows on this store, split into Wallkoala and other vendors.
        listings: dict[str, list[StoreListing]] = {}
        other_vendor: dict[str, str] = {}
        for listing in StoreListing.objects.filter(store=store, status__in=_PUBLISHED).only(
            "id", "sku", "source_vendor_code", "inventory", "infinite_quantity",
        ):
            key = _key(listing.sku)
            if not key:
                continue
            if is_wallkoala_vendor_code(listing.source_vendor_code):
                listings.setdefault(key, []).append(listing)
            else:
                other_vendor.setdefault(key, listing.source_vendor_code or "(no vendor)")

        from catalog.mydeal_templates import mapping_sku_keys

        mappings: dict[str, list[ProductMapping]] = {}
        wallkoala_vendor = None
        for pm in (
            ProductMapping.objects.filter(store=store, is_active=True)
            .select_related("product", "product__vendor")
            .only(
                "id", "store_stock", "marketplace_child_sku", "marketplace_parent_sku",
                "product__vendor_sku", "product__vendor__id", "product__vendor__code",
                "product__vendor__name",
            )
        ):
            vendor = getattr(pm.product, "vendor", None) if pm.product_id else None
            is_wk = vendor is not None and is_wallkoala_vendor_code(vendor.code)
            for raw in mapping_sku_keys(pm):
                key = _key(raw)
                if not key:
                    continue
                if is_wk:
                    wallkoala_vendor = wallkoala_vendor or vendor
                    rows = mappings.setdefault(key, [])
                    if pm not in rows:
                        rows.append(pm)
                else:
                    other_vendor.setdefault(key, (vendor.name if vendor else "") or "(no vendor)")

        matched: list[str] = []
        unmatched: list[str] = []
        other: list[str] = []
        for sku in skus:
            key = _key(sku)
            if key in listings or key in mappings:
                matched.append(sku)
            elif key in other_vendor:
                other.append(f"{sku} (vendor {other_vendor[key]})")
            else:
                unmatched.append(sku)

        def _local_zero(key: str) -> bool:
            rows_l = listings.get(key, [])
            rows_m = mappings.get(key, [])
            return all(
                int(row.inventory or 0) == 0 and not row.infinite_quantity for row in rows_l
            ) and all(row.store_stock == 0 for row in rows_m)

        # Big W side.
        entries: list[dict] = []
        live: dict[str, dict] = {}
        not_live: list[str] = []
        template_keys: set[str] = set()
        if api and matched:
            try:
                entries = _live_entries(store)
            except MarketplaceError as exc:
                raise CommandError(f"Nothing was changed.\nCould not read the MyDeal catalog: {exc}") from exc
            live = _live_index(entries)
            not_live = [sku for sku in matched if _key(sku) not in live]
        elif matched:
            from catalog.mydeal_templates import inventory_template_sku_keys

            template_keys = {key.casefold() for key in inventory_template_sku_keys(store)}
            not_live = [sku for sku in matched if _key(sku) not in template_keys]

        already_zero: list[str] = []
        to_change: list[str] = []
        push_keys: set[str] = set()
        for sku in matched:
            key = _key(sku)
            local_zero = _local_zero(key)
            if api:
                entry = live.get(key)
                live_zero = entry is None or (
                    int(entry.get("quantity") or 0) == 0 and not entry.get("unlimited")
                )
                if entry is not None and not live_zero:
                    push_keys.add(key)
                if local_zero and live_zero:
                    already_zero.append(sku)
                    continue
            elif local_zero:
                already_zero.append(sku)
                continue
            to_change.append(sku)

        prefix = "DRY RUN. Nothing was changed.\n" if dry_run else ""
        self.stdout.write(f"{prefix}Store: {_store_line(store)}")
        self.stdout.write(f"Vendor: {VENDOR_LABEL}")
        self.stdout.write(
            "Big W push: "
            + ("MyDeal API (live catalog)" if api else "upload templates (queued for the inventory bot laptop)")
        )
        self.stdout.write(f"SKUs in file: {len(skus)}")
        self._list("Matched (Wallkoala on this store)", matched)
        self._list("Already 0 (local" + (" and on Big W)" if api else ")"), already_zero)
        self._list("Will be set to 0", to_change)
        if api:
            self._list("Matched but not found on Big W (local stock only)", not_live)
            self.stdout.write(f"SKUs to send to Big W with stock 0: {len(push_keys)}")
        else:
            self._list("Matched but not on the MyDeal inventory template (cannot be uploaded)", not_live)
            self.stdout.write(
                f"SKUs that the queued Wallkoala Stock/Status upload will send with stock 0: "
                f"{len(matched) - len(not_live)}"
            )
        self._list("Other vendor on this store (not changed)", other)
        self._list("Unmatched (not on this store)", unmatched)

        if not matched:
            raise CommandError("Nothing was changed.\nNo SKU in the file is a Wallkoala SKU on this store.")
        if dry_run:
            return

        # 1. Local stock 0.
        now = timezone.now()
        change_keys = {_key(sku) for sku in to_change}
        listing_rows = [
            row for key in change_keys for row in listings.get(key, [])
            if int(row.inventory or 0) != 0 or row.infinite_quantity
        ]
        mapping_rows = {
            row.id: row for key in change_keys for row in mappings.get(key, [])
            if row.store_stock != 0
        }
        for row in listing_rows:
            row.inventory = 0
            row.infinite_quantity = False
            row.updated_at = now
        for row in mapping_rows.values():
            row.store_stock = 0
        with transaction.atomic():
            if listing_rows:
                StoreListing.objects.bulk_update(
                    listing_rows, ["inventory", "infinite_quantity", "updated_at"], batch_size=_SAVE_BATCH,
                )
            if mapping_rows:
                ProductMapping.objects.bulk_update(
                    list(mapping_rows.values()), ["store_stock"], batch_size=_SAVE_BATCH,
                )
        self.stdout.write(
            f"Local stock set to 0: {len(listing_rows)} listing(s), {len(mapping_rows)} catalog mapping(s)."
        )

        # 2. Big W stock 0.
        push_note = ""
        errors: list[str] = []
        if api:
            if push_keys:
                ok_groups, errors = _push_zero_api(store, entries, push_keys)
                push_note = f"Big W: {ok_groups} product group(s) sent with stock 0 for {len(push_keys)} SKU(s)."
            else:
                push_note = "Big W: every matched SKU was already 0. Nothing sent."
        else:
            from catalog import mydeal_upload_runs as upload_runs

            on_template = [sku for sku in matched if _key(sku) in template_keys]
            if wallkoala_vendor is None:
                push_note = (
                    "Big W: no Wallkoala catalog mapping on this store, so no upload was queued. "
                    "Push from Created products."
                )
            elif not on_template:
                push_note = "Big W: no matched SKU is on the MyDeal inventory template, so no upload was queued."
            else:
                # Queued even when local stock was already 0: the live Big W
                # stock of an upload-template store cannot be read here.
                try:
                    run = upload_runs.start_run(
                        store, None, [str(wallkoala_vendor.id)], ["inventory"],
                    )
                    push_note = (
                        f"Big W: Wallkoala Stock/Status upload queued for the inventory bot laptop (run {run.id})."
                    )
                except upload_runs.UploadRunError as exc:
                    errors.append(f"Upload not queued: {exc}")
                    push_note = (
                        "Big W: upload not queued. Use Upload the file on the Marketplace for "
                        "Wallkoala, Stock/Status, once the current upload ends."
                    )
        self.stdout.write(push_note)
        for error in errors:
            self.stdout.write(self.style.ERROR(error))

        from catalog.activity_log import append_catalog_log

        message = (
            f"Big W stock 0 from file: {len(to_change)} Wallkoala SKU(s) set to 0, "
            f"{len(already_zero)} already 0, {len(other)} other vendor, {len(unmatched)} unmatched. "
            f"{push_note}"
        )
        if errors:
            message += " Errors: " + " | ".join(errors)
        append_catalog_log(
            store.id,
            message,
            action_type=LOG_ACTION,
            metadata={
                "file_skus": len(skus),
                "set_to_zero": to_change,
                "already_zero": len(already_zero),
                "unmatched": unmatched,
                "other_vendor": other,
                "not_on_bigw": not_live,
                "errors": errors,
            },
        )
        if errors:
            raise CommandError(
                "Local stock is 0, but some Big W updates failed. See the errors above."
            )
        self.stdout.write(self.style.SUCCESS("Done."))
