"""Push final BigW price and stock from a one-time Excel file.

BigW listings in this app are the MyDeal store. The file is not a scrape and
the prices are not run through margins. Each row is SKU, Price, and Inventory.
Price is the final amount sent to BigW. Inventory is the final stock.

The store is the MyDeal store named by ``--store`` (for example Shemaya).
A row updates the published listing on that store whose SKU matches. Nothing
is written, and nothing is sent to BigW, unless every row matches. Listings
are saved and pushed 40 at a time. A failed batch is not saved. Batches that
already succeeded stay on BigW.

Usage (inside the backend container):

  python manage.py sync_bigw_price_stock --email you@example.com --store "Shemaya" --file /tmp/bigw-price-stock.xlsx --dry-run
  python manage.py sync_bigw_price_stock --email you@example.com --store "Shemaya" --file /tmp/bigw-price-stock.xlsx
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections, transaction
from django.db.models import Q
from django.utils import timezone

from listings.errors import MarketplaceError
from listings.models import InventorySyncStatus, ListingStatus, StoreListing
from listings.mydeal.products import push_inventory
from scrapers.vevor_au_ingest import clean_id
from stores.models import Store
from users.org_scope import organization_user_ids

User = get_user_model()

_PUBLISHED = (
    ListingStatus.UPLOADED_STAGING,
    ListingStatus.UPLOADED_PRODUCTION,
)
# One database update of the whole file closed Postgres. Save and push this
# many listings at a time.
BATCH_SIZE = 40
_UPDATE_FIELDS = [
    "sale_price",
    "original_price",
    "sale_price_cents",
    "original_price_cents",
    "inventory",
    "infinite_quantity",
    "inventory_sync_status",
    "updated_at",
]
_STOCK_TOKENS = frozenset({"inventory", "stock", "qty", "quantity"})


def _token(value) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _label(value) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def _norm_sku(value) -> str:
    return clean_id(value).lower()


def _money(value) -> Decimal:
    if isinstance(value, bool) or value is None or value == "":
        raise ValueError("Price is required")
    if isinstance(value, int):
        amount = Decimal(value)
    elif isinstance(value, float):
        amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:
        text = str(value).strip().replace("$", "").replace(",", "")
        if not text:
            raise ValueError("Price is required")
        amount = Decimal(text)
        cents = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if cents != amount:
            raise ValueError(f"Price {text} has more than 2 decimal places")
        amount = cents
    if amount <= 0:
        raise ValueError("Price must be greater than 0")
    return amount.quantize(Decimal("0.01"))


def _stock(value) -> int:
    if isinstance(value, bool) or value is None or value == "":
        raise ValueError("Inventory is required")
    if isinstance(value, int):
        qty = value
    elif isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"Inventory {value} is not a whole number")
        qty = int(value)
    else:
        text = str(value).strip().replace(",", "")
        if not text:
            raise ValueError("Inventory is required")
        amount = Decimal(text)
        if amount != amount.to_integral_value():
            raise ValueError(f"Inventory {text} is not a whole number")
        qty = int(amount)
    if qty < 0:
        raise ValueError("Inventory cannot be negative")
    return qty


def _read_rows(path: str) -> list[tuple[str, Decimal, int]]:
    lower = path.lower()
    if not lower.endswith((".xlsx", ".xlsm")):
        raise CommandError("File must be an Excel workbook (.xlsx).")
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheets = []
        for sheet in workbook.worksheets:
            if sheet.sheet_state and sheet.sheet_state != "visible":
                continue
            sheets.append((sheet.title, [tuple(row) for row in sheet.iter_rows(values_only=True)]))
    finally:
        workbook.close()
    if not sheets:
        raise CommandError("Nothing was changed.\nThe workbook has no sheet.")
    if len(sheets) > 1:
        raise CommandError(
            "Nothing was changed.\nThe workbook has more than one sheet. Keep a single sheet."
        )
    _title, rows = sheets[0]
    header_at = None
    sku_i = price_i = final_i = stock_i = None
    for index, row in enumerate(rows[:15]):
        tokens = [_token(cell) for cell in row]
        if "sku" not in tokens:
            continue
        price_at = next((i for i, token in enumerate(tokens) if token == "price"), None)
        final_at = next((i for i, token in enumerate(tokens) if token in {"finalprice", "saleprice"}), None)
        stock_at = next((i for i, token in enumerate(tokens) if token in _STOCK_TOKENS), None)
        if price_at is None and final_at is None:
            continue
        if stock_at is None:
            continue
        header_at = index
        sku_i = tokens.index("sku")
        price_i = price_at
        final_i = final_at
        stock_i = stock_at
        break
    if header_at is None:
        raise CommandError(
            "Nothing was changed.\nThe sheet needs columns named SKU, Price, and Inventory."
        )
    chosen_price = final_i if final_i is not None else price_i

    parsed: list[tuple[str, Decimal, int]] = []
    problems: list[str] = []
    seen: dict[str, tuple[Decimal, int]] = {}
    for line_no, row in enumerate(rows[header_at + 1:], start=header_at + 2):
        sku = clean_id(row[sku_i]) if sku_i < len(row) else ""
        raw_price = row[chosen_price] if chosen_price < len(row) else None
        raw_stock = row[stock_i] if stock_i < len(row) else None
        if not sku and (raw_price is None or raw_price == "") and (raw_stock is None or raw_stock == ""):
            continue
        if not sku:
            problems.append(f"Line {line_no}: SKU is required")
            continue
        try:
            price = _money(raw_price)
        except (InvalidOperation, ValueError) as exc:
            problems.append(f"Line {line_no}: SKU {sku}: {exc}")
            continue
        try:
            qty = _stock(raw_stock)
        except (InvalidOperation, ValueError) as exc:
            problems.append(f"Line {line_no}: SKU {sku}: {exc}")
            continue
        key = _norm_sku(sku)
        previous = seen.get(key)
        if previous is not None and previous != (price, qty):
            problems.append(
                f"Line {line_no}: SKU {sku} is already {previous[0]} / {previous[1]}, not {price} / {qty}"
            )
            continue
        if previous is None:
            seen[key] = (price, qty)
            parsed.append((sku, price, qty))
    if problems:
        raise CommandError("Nothing was changed.\n" + "\n".join(problems))
    if not parsed:
        raise CommandError("Nothing was changed.\nThe workbook has no SKU rows.")
    return parsed


def _mydeal_stores(user):
    user_ids = list(organization_user_ids(user))
    if user.id not in user_ids:
        user_ids.append(user.id)
    return list(
        Store.objects.filter(user_id__in=user_ids, orphaned_at__isnull=True)
        .filter(Q(marketplace__code__iexact="mydeal") | Q(marketplace__name__icontains="mydeal"))
        .select_related("marketplace")
        .order_by("name")
    )


def _pick_store(stores, wanted: str) -> Store:
    want = _label(wanted)
    exact = [store for store in stores if _label(store.name) == want]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        names = ", ".join(store.name for store in exact)
        raise CommandError(
            "Nothing was changed.\n"
            f"Store {wanted!r} matches more than one MyDeal store: {names}"
        )
    names = ", ".join(store.name for store in stores) or "(none)"
    raise CommandError(
        "Nothing was changed.\n"
        f"No MyDeal store named {wanted!r} for this account. Stores: {names}"
    )


class Command(BaseCommand):
    help = "Set final BigW price and stock from an Excel file and push those rows to MyDeal."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True, help="Account email that owns the store")
        parser.add_argument("--store", required=True, help='MyDeal store name, for example "Shemaya"')
        parser.add_argument("--file", required=True, help="xlsx with columns SKU, Price, and Inventory")
        parser.add_argument("--dry-run", action="store_true", help="Print the plan and do not push")

    def handle(self, *args, **options):
        email = (options["email"] or "").strip()
        user = User.objects.filter(email__iexact=email).first()
        if user is None:
            raise CommandError(f"No user with email {email}")
        store = _pick_store(_mydeal_stores(user), options["store"])
        parsed = _read_rows(options["file"])

        listings_by_sku: dict[str, list[StoreListing]] = {}
        for listing in StoreListing.objects.filter(store=store).only(
            "id",
            "sku",
            "external_variant_key",
            "external_product_key",
            "sale_price",
            "original_price",
            "sale_price_cents",
            "original_price_cents",
            "inventory",
            "infinite_quantity",
            "status",
            "inventory_sync_status",
        ):
            key = _norm_sku(listing.sku)
            if key:
                listings_by_sku.setdefault(key, []).append(listing)

        problems: list[str] = []
        planned: list[tuple[StoreListing, str, Decimal, int]] = []
        for sku, price, qty in parsed:
            hits = listings_by_sku.get(_norm_sku(sku), [])
            published = [row for row in hits if row.status in _PUBLISHED]
            if len(published) > 1:
                problems.append(f"{store.name}: SKU {sku} matches {len(published)} published listings")
                continue
            if not published:
                if hits:
                    problems.append(f"{store.name}: SKU {sku} is not published on BigW")
                else:
                    problems.append(f"{store.name}: SKU {sku} was not found")
                continue
            planned.append((published[0], sku, price, qty))
        if problems:
            raise CommandError("Nothing was changed.\n" + "\n".join(problems))

        prefix = "Dry run. Nothing was written. " if options["dry_run"] else ""
        self.stdout.write(f"{prefix}{store.name}: {len(planned)} BigW listing(s).")
        for listing, sku, price, qty in planned:
            self.stdout.write(f"  SKU {sku}: price {price} stock {qty} (saved price {listing.sale_price}, stock {listing.inventory})")
        if options["dry_run"]:
            return

        now = timezone.now()
        listings = []
        for listing, _sku, price, qty in planned:
            cents = int((price * 100).quantize(Decimal("1")))
            listing.sale_price = price
            listing.original_price = price
            listing.sale_price_cents = cents
            listing.original_price_cents = cents
            listing.inventory = qty
            listing.infinite_quantity = False
            listing.inventory_sync_status = InventorySyncStatus.SYNCED
            listing.updated_at = now
            listings.append(listing)

        pushed = 0
        total = len(listings)
        try:
            for start in range(0, total, BATCH_SIZE):
                batch = listings[start:start + BATCH_SIZE]
                close_old_connections()
                try:
                    with transaction.atomic():
                        StoreListing.objects.bulk_update(
                            batch, _UPDATE_FIELDS, batch_size=BATCH_SIZE,
                        )
                        try:
                            result = push_inventory(batch, store)
                        except MarketplaceError as exc:
                            raise CommandError(str(exc)) from exc
                        if not result.get("ok"):
                            raise CommandError(
                                result.get("message") or "BigW price and stock push failed."
                            )
                except CommandError:
                    raise
                pushed += len(batch)
                self.stdout.write(f"Pushed {pushed} of {total}.")
        except CommandError as exc:
            if pushed:
                raise CommandError(
                    f"Stopped after {pushed} listing(s) were pushed to BigW. "
                    f"The failed batch was not saved.\n{exc}"
                ) from exc
            raise CommandError(f"Nothing was changed.\n{exc}") from exc
        self.stdout.write(f"Pushed {pushed} listing(s) to BigW.")
