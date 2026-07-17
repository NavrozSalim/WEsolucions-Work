"""Check whether a SKU / variant exists on Lasoo (Variants_Search).

Usage (inside the backend container on Main):

  python manage.py lasoo_check_variant --sku ABC-123
  python manage.py lasoo_check_variant --sku ABC-123 --store "Lasoo - P&P"
  python manage.py lasoo_check_variant --sku ABC-123 --env production
  python manage.py lasoo_check_variant --sku ABC-123 --product-key AAA --variant-key BBB

Exit codes:
  0 = found on Lasoo
  1 = not found (API ok, empty / no match)
  2 = configuration / API error
"""
from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from listings.errors import MarketplaceError
from listings.lasoo.client import LasooClient
from listings.lasoo.queries import build_payload
from listings.models import StoreListing
from stores.models import Store


def _collect_result_rows(body) -> list:
    """Best-effort extract of variant rows from a Lasoo Search response."""
    if not isinstance(body, dict):
        return []
    candidates = []
    results = body.get("results")
    if isinstance(results, dict):
        for key in ("variants", "items", "records", "data", "rows", "results"):
            val = results.get(key)
            if isinstance(val, list):
                candidates.append(val)
        # Some Lasoo shapes nest again under results.body / results.data
        for nest_key in ("body", "data"):
            nested = results.get(nest_key)
            if isinstance(nested, dict):
                for key in ("variants", "items", "records", "rows"):
                    val = nested.get(key)
                    if isinstance(val, list):
                        candidates.append(val)
            elif isinstance(nested, list):
                candidates.append(nested)
    elif isinstance(results, list):
        candidates.append(results)

    for key in ("variants", "items", "data", "records"):
        val = body.get(key)
        if isinstance(val, list):
            candidates.append(val)

    for rows in candidates:
        if rows:
            return rows
    return candidates[0] if candidates else []


def _row_keys(row: dict) -> tuple[str, str]:
    product = str(
        row.get("externalProductKey")
        or row.get("ExternalProductKey")
        or row.get("productKey")
        or row.get("product_key")
        or ""
    ).strip()
    variant = str(
        row.get("externalVariantKey")
        or row.get("ExternalVariantKey")
        or row.get("variantKey")
        or row.get("variant_key")
        or row.get("sku")
        or ""
    ).strip()
    return product, variant


class Command(BaseCommand):
    help = "Check if a listing SKU/variant exists on Lasoo via Variants_Search."

    def add_arguments(self, parser):
        parser.add_argument("--sku", required=True, help="SKU or external variant key to look up")
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
            "--json",
            action="store_true",
            help="Print the raw Lasoo JSON response.",
        )

    def handle(self, *args, **options):
        sku = (options["sku"] or "").strip()
        if not sku:
            raise CommandError("--sku is required")

        store_name = (options.get("store") or "").strip()
        env_override = (options.get("env") or "").strip() or None
        product_override = (options.get("product_key") or "").strip()
        variant_override = (options.get("variant_key") or "").strip()
        dump_json = bool(options.get("json"))

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
        product_key = str(product_key).strip()
        variant_key = str(variant_key).strip()

        try:
            client = LasooClient(store, environment=env_override)
        except MarketplaceError as exc:
            raise CommandError(str(exc)) from exc

        payload = build_payload(
            "variants_search",
            data={
                "externalProductKey": product_key,
                "externalVariantKey": variant_key,
                "take": 25,
                "page": 1,
                "returnDataObject": False,
                "dataMappingErrors": False,
                "returnMappingInfo": False,
            },
            auth=client.auth_key,
        )

        self.stdout.write(
            f"store={store.name} env={client.environment} base={client.base_url}\n"
            f"sku={sku} product_key={product_key} variant_key={variant_key}\n"
            f"local_listing={'yes' if listing else 'no'}"
            + (f" status={listing.status}" if listing else "")
        )

        result = client.send("variants_search", payload)
        if not result.ok:
            self.stderr.write(
                self.style.ERROR(
                    f"Lasoo API error: status={result.status} message={result.message}"
                )
            )
            if dump_json:
                self.stdout.write(json.dumps(result.data, indent=2, default=str))
            raise SystemExit(2)

        rows = _collect_result_rows(result.data)
        matched = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            p, v = _row_keys(row)
            if v == variant_key or p == product_key or v == sku or p == sku:
                matched.append(row)

        # If Lasoo returned any rows for this filtered search, treat as found
        # even when key field names differ.
        found = bool(matched) or bool(rows)

        if dump_json:
            self.stdout.write(json.dumps(result.data, indent=2, default=str))

        if found:
            self.stdout.write(
                self.style.SUCCESS(
                    f"FOUND on Lasoo — {len(matched) or len(rows)} row(s) "
                    f"(variant_key={variant_key})"
                )
            )
            for row in (matched or rows)[:5]:
                if isinstance(row, dict):
                    p, v = _row_keys(row)
                    self.stdout.write(f"  - product={p or '?'} variant={v or '?'}")
            raise SystemExit(0)

        self.stdout.write(
            self.style.WARNING(
                f"NOT FOUND on Lasoo for product_key={product_key} variant_key={variant_key}"
            )
        )
        raise SystemExit(1)
