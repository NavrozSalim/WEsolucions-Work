"""
Smoke-test vendor scrapers from the shell (no Celery).

Usage:
  python manage.py test_vendor_scrape --url "https://www.ebay.com/itm/1234567890"
  python manage.py test_vendor_scrape --url "https://www.amazon.com/dp/B0XXXXXX" --region USA
"""
from django.core.management.base import BaseCommand

from scrapers import get_price_and_stock, close_amazon_session


class Command(BaseCommand):
    help = "Call get_price_and_stock for one URL (eBay, Amazon) and print the result."

    def add_arguments(self, parser):
        parser.add_argument("--url", type=str, required=True, help="Product page URL")
        parser.add_argument(
            "--region",
            type=str,
            default="USA",
            help="Store region: USA or AU (default USA)",
        )

    def handle(self, *args, **options):
        url = (options["url"] or "").strip()
        region = (options["region"] or "USA").strip().upper()
        if region not in ("USA", "AU"):
            self.stderr.write(self.style.WARNING("Region should be USA or AU; using USA."))
            region = "USA"

        session = {}
        self.stdout.write(f"URL: {url}\nRegion: {region}\n")
        try:
            result = get_price_and_stock(url, region, session)
        finally:
            close_amazon_session(session)

        self.stdout.write(self.style.SUCCESS(f"Result: {result}"))
        price = result.get("price")
        if price is None:
            self.stdout.write(
                self.style.ERROR(
                    "No price returned. Common causes: blocked/captcha, wrong URL, "
                    "unsupported domain, or page layout changed."
                )
            )
        else:
            stock = result.get("stock")
            if stock is None:
                stock = result.get("inventory")
            self.stdout.write(self.style.SUCCESS(f"OK — price={price!r}, stock={stock!r}"))
