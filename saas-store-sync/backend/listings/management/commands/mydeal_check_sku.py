"""Show what MyDeal returns for one SKU (single-product and catalog list).

Usage (inside the backend container):

  python manage.py mydeal_check_sku --sku 9629364224227-49613365772515
  python manage.py mydeal_check_sku --sku ABC-1 --store "Shemaya"
  python manage.py mydeal_check_sku --sku ABC-1 --json

Exit codes:
  0 = found on MyDeal
  1 = not found
  2 = configuration / API error
"""
from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from listings import marketplace_lookup as ml
from listings.errors import MarketplaceError
from listings.mydeal.client import MyDealClient
from stores.models import Store


class Command(BaseCommand):
    help = "Look up one SKU on MyDeal and print each request and its result."

    def add_arguments(self, parser):
        parser.add_argument("--sku", required=True, help="SKU or variant code to look up")
        parser.add_argument(
            "--store",
            default="",
            help="Store name (case-insensitive). Defaults to the first MyDeal managed store.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Print the first catalog page returned by GET /products.",
        )

    def handle(self, *args, **options):
        sku = (options["sku"] or "").strip()
        if not sku:
            raise CommandError("--sku is required")
        store_name = (options.get("store") or "").strip()

        qs = (
            Store.objects.filter(management_mode="full_store")
            .filter(Q(marketplace__code__iexact="mydeal") | Q(marketplace__name__icontains="mydeal"))
            .select_related("marketplace")
            .order_by("name")
        )
        if store_name:
            qs = qs.filter(name__iexact=store_name)
        store = qs.first()
        if store is None:
            hint = f' named "{store_name}"' if store_name else ""
            raise CommandError(f"No MyDeal managed store{hint} found.")

        try:
            client = MyDealClient(store)
        except MarketplaceError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(f"Store: {store.name}  env={client.environment}")
        self.stdout.write(f"SKU:   {sku}")
        self.stdout.write("")

        keys = [sku, *ml._mydeal_group_key_candidates(sku), *ml._mydeal_related_keys(store, sku)]
        seen: list[str] = []
        for key in keys:
            if key and key not in seen:
                seen.append(key)

        self.stdout.write("GET /products/{key} (single product):")
        for key in seen:
            for by in ("sku", "id"):
                result = client.get_product(key, by=by)
                groups = ml._mydeal_groups(result.data) if result.ok else []
                label = "FOUND" if groups else ("not found" if ml._mydeal_is_not_found(result) else "error")
                self.stdout.write(
                    f"  {key} ?{by}= -> {label}  "
                    f"status={result.status} response={result.response_status or '-'} "
                    f"message={(result.message or '')[:160]}"
                )
                if groups:
                    self.stdout.write(self.style.SUCCESS("Result: FOUND on MyDeal (single product)"))
                    raise SystemExit(0)

        self.stdout.write("")
        self.stdout.write("GET /products (catalog list):")
        first_page = None
        total_groups = 0
        total_buyables = 0
        for listing_status in ml._MYDEAL_LIST_STATUSES:
            page = 1
            status_groups = 0
            while page <= ml._MYDEAL_LIST_MAX_PAGES:
                result = client.list_products(
                    page=page,
                    limit=ml._MYDEAL_LIST_PAGE_LIMIT,
                    listing_status=listing_status,
                    fields=ml._MYDEAL_LIST_FIELDS,
                )
                if first_page is None:
                    first_page = result.data
                if not result.ok:
                    self.stdout.write(
                        f"  {listing_status} page {page}: status={result.status} "
                        f"message={(result.message or '')[:200]}"
                    )
                    if not ml._mydeal_is_not_found(result):
                        raise SystemExit(2)
                    break
                groups = ml._mydeal_groups(result.data)
                if not groups:
                    break
                status_groups += len(groups)
                for group in groups:
                    buyables = ml._ci_value(group, "BuyableProducts") or []
                    total_buyables += len(buyables)
                    index: dict[str, dict] = {}
                    ml._mydeal_index_group(index, group)
                    needles = {k.casefold() for k in seen}
                    if needles & set(index):
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"Result: FOUND in catalog list ({listing_status} page {page}) "
                                f"ProductSKU={ml._ci_get(group, 'ProductSKU')} "
                                f"ExternalProductId={ml._ci_get(group, 'ExternalProductId', 'ExternalProductID')}"
                            )
                        )
                        if options.get("json"):
                            self.stdout.write(json.dumps(group, indent=2, default=str))
                        raise SystemExit(0)
                if len(groups) < ml._MYDEAL_LIST_PAGE_LIMIT:
                    break
                page += 1
            total_groups += status_groups
            self.stdout.write(f"  {listing_status}: {status_groups} product group(s) scanned")

        self.stdout.write(f"  buyable rows scanned: {total_buyables}")
        if options.get("json") and first_page is not None:
            self.stdout.write("")
            self.stdout.write("First catalog page:")
            self.stdout.write(json.dumps(first_page, indent=2, default=str)[:20000])
        self.stdout.write(self.style.WARNING("Result: NOT FOUND on MyDeal"))
        if total_groups and not total_buyables:
            self.stdout.write(
                "MyDeal returned product groups without BuyableProducts. "
                "Re-run with --json to see the field names it sends back."
            )
        raise SystemExit(1)
