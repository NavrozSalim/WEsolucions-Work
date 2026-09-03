"""Delete a SKU from Lasoo Connect (Variants_BulkDelete + Search verify).

Use this for orphaned Connect variants that are no longer in Hub Inventory.

Usage (inside the backend container on Main):

  python manage.py lasoo_delete_variant --store "Pretty & Practical" --sku HW-ZZ122-G2
  python manage.py lasoo_delete_variant --store "Pretty & Practical" --sku HW-ZZ122-G2 --yes

Without --yes this only searches (dry-run).
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from listings.errors import MarketplaceError
from listings.lasoo.client import LasooClient
from listings.lasoo.connect_delete import delete_connect_variants, lookup_present
from listings.models import StoreListing
from stores.models import Store


class Command(BaseCommand):
    help = "Delete a SKU from Lasoo Connect after confirming it with Variants_Search."

    def add_arguments(self, parser):
        parser.add_argument("--sku", required=True, help="SKU or external variant key")
        parser.add_argument(
            "--store",
            default="",
            help='Store name (exact, case-insensitive). Defaults to first Lasoo managed store.',
        )
        parser.add_argument(
            "--env",
            choices=["staging", "production", ""],
            default="",
            help="Override store Lasoo environment (default: use store setting).",
        )
        parser.add_argument(
            "--product-key",
            default="",
            help="Override externalProductKey (default: local listing product key or SKU).",
        )
        parser.add_argument(
            "--variant-key",
            default="",
            help="Override externalVariantKey (default: local listing variant key or SKU).",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Call BulkDelete. Without this flag, only Search is run.",
        )

    def handle(self, *args, **options):
        sku = (options["sku"] or "").strip()
        if not sku:
            raise CommandError("--sku is required")

        store_name = (options.get("store") or "").strip()
        env_override = (options.get("env") or "").strip() or None
        product_override = (options.get("product_key") or "").strip()
        variant_override = (options.get("variant_key") or "").strip()
        do_delete = bool(options.get("yes"))

        qs = (
            Store.objects.filter(management_mode="full_store")
            .filter(Q(marketplace__code__iexact="lasoo") | Q(marketplace__name__icontains="lasoo"))
            .select_related("marketplace")
            .order_by("name")
        )
        if store_name:
            qs = qs.filter(name__iexact=store_name)
        store = qs.first()
        if store is None:
            hint = f' named "{store_name}"' if store_name else ""
            raise CommandError(f"No Lasoo managed store{hint} found.")

        listing = (
            StoreListing.objects.filter(store=store)
            .filter(Q(sku=sku) | Q(external_variant_key=sku) | Q(external_product_key=sku))
            .first()
        )
        product_key = product_override or (
            (listing.external_product_key if listing else "") or sku
        )
        variant_key = variant_override or (
            (listing.external_variant_key if listing else "") or sku
        )
        item = {
            "product_key": str(product_key).strip(),
            "variant_key": str(variant_key).strip(),
            "sku": sku,
        }

        try:
            client = LasooClient(store, environment=env_override)
        except MarketplaceError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(f"Store: {store.name}")
        self.stdout.write(f"SKU:   {sku}")
        self.stdout.write(
            f"Keys:  product={item['product_key']} variant={item['variant_key']}"
        )
        self.stdout.write(f"Hub:   {'yes' if listing else 'no'}")
        self.stdout.write("")

        try:
            present = lookup_present(client, [item])
        except MarketplaceError as exc:
            raise CommandError(str(exc)) from exc

        if not present:
            self.stdout.write(self.style.SUCCESS("Result: NOT on Lasoo Connect"))
            return

        if not do_delete:
            self.stdout.write(self.style.WARNING("Result: FOUND on Lasoo Connect"))
            self.stdout.write("Dry-run only. Re-run with --yes to call BulkDelete.")
            return

        try:
            deleted = delete_connect_variants(client, [item])
        except MarketplaceError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(
            f"Result: deleted {deleted} variant(s) from Lasoo Connect"
        ))
        self.stdout.write(
            "Public lasoo.com.au pages may stay until Lasoo remaps/unpublishes."
        )
