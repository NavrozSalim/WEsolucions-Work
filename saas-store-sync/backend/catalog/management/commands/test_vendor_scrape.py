"""
Smoke-test vendor scrapers from the shell (no Celery).

Usage:
  python manage.py test_vendor_scrape --url "https://www.ebay.com/itm/1234567890"
  python manage.py test_vendor_scrape --url "https://www.ebay.com.au/itm/123" --region AU \\
    --save-html /tmp/ebay_item.html
"""
from django.core.management.base import BaseCommand

from scrapers import get_price_and_stock, close_amazon_session
from scrapers.ebay_scraper import SESSION_DEBUG_HTML_KEY


def _close_scrape_session(session: dict) -> None:
    close_amazon_session(session)


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
        parser.add_argument(
            "--save-html",
            type=str,
            default="",
            metavar="PATH",
            help="eBay only: write last HTML used for parsing to this path (debug/discount timing)",
        )

    def handle(self, *args, **options):
        url = (options["url"] or "").strip()
        region = (options["region"] or "USA").strip().upper()
        if region not in ("USA", "AU"):
            self.stderr.write(self.style.WARNING("Region should be USA or AU; using USA."))
            region = "USA"

        session = {}
        save_html = (options.get("save_html") or "").strip()
        if save_html:
            session[SESSION_DEBUG_HTML_KEY] = save_html

        self.stdout.write(f"URL: {url}\nRegion: {region}\n")
        try:
            result = get_price_and_stock(url, region, session)
        finally:
            _close_scrape_session(session)

        self.stdout.write(self.style.SUCCESS(f"Result: {result}"))
        if save_html:
            self.stdout.write(f"eBay debug HTML path (if written): {save_html}")
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
