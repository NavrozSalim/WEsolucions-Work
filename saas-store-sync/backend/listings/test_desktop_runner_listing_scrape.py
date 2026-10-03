"""Managed Inventory management: HEB / Costco (no proxies) go through the desktop runner."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from catalog.ingest_views import _collect_vendor_urls
from catalog.models import HebScrapeJob
from marketplace.models import Marketplace
from stores.models import Store

from . import listing_service
from . import scrape_progress as scrape_prog
from .desktop_ingest import apply_runner_result_to_listings
from .models import InventorySyncStatus, ListingStatus, StoreListing


class DesktopRunnerListingScrapeTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="desktop_listing", email="desktop_listing@example.com", password="pw",
        )
        self.other = User.objects.create_user(
            username="desktop_other", email="desktop_other@example.com", password="pw",
        )
        lasoo, _ = Marketplace.objects.get_or_create(code="lasoo", defaults={"name": "Lasoo"})
        self.store = Store.objects.create(
            user=self.user,
            name="Lasoo Desktop",
            region="AU",
            api_token="",
            marketplace=lasoo,
            management_mode="full_store",
            lasoo_environment="staging",
            lasoo_staging_auth_key="test-key",
        )

    def tearDown(self):
        scrape_prog.clear_scrape_progress(self.store.id)

    def _listing(self, sku, url, source):
        return StoreListing.objects.create(
            user=self.user,
            store=self.store,
            external_product_key=sku,
            external_variant_key=sku,
            sku=sku,
            title=sku,
            vendor_url=url,
            source_vendor_code=source,
            status=ListingStatus.UPLOADED_STAGING,
            inventory_sync_status=InventorySyncStatus.PENDING,
        )

    @patch("catalog.tasks._costco_au_runs_on_server", return_value=False)
    @patch("stores.nora.load_store_nora_stock_map", return_value=None)
    @patch("scrapers.close_amazon_session")
    @patch("scrapers.get_price_and_stock")
    def test_heb_and_costco_stay_pending_and_queue_runner_jobs(
        self, mock_price, _close, _nora, _proxies,
    ):
        heb = self._listing("HEB-1", "https://www.heb.com/product-detail/x/1", "heb")
        costco = self._listing("COS-1", "https://www.costco.com.au/p/1", "costcoau")

        result = listing_service.scrape_listings(self.user, self.store)

        mock_price.assert_not_called()
        self.assertEqual(result["scraped"], 0)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["desktop_queued"], {"heb": 1, "costco": 1})
        heb.refresh_from_db()
        costco.refresh_from_db()
        self.assertEqual(heb.inventory_sync_status, InventorySyncStatus.PENDING)
        self.assertEqual(costco.inventory_sync_status, InventorySyncStatus.PENDING)
        jobs = HebScrapeJob.objects.filter(store=self.store, status=HebScrapeJob.Status.PENDING)
        self.assertEqual(sorted(jobs.values_list("vendor_code", flat=True)), ["costco", "heb"])

        listing_service.scrape_listings(self.user, self.store)
        self.assertEqual(
            HebScrapeJob.objects.filter(store=self.store, vendor_code="heb").count(), 1,
        )

    def test_runner_url_list_includes_pending_listings(self):
        url = "https://www.costco.com.au/p/1"
        self._listing("COS-1", url, "costcoau")
        urls = _collect_vendor_urls(
            str(self.store.id), vendor="costco",
            restrict_to_user_id=self.user.id, pending_only=True,
        )
        self.assertEqual(urls, [url])

    def test_runner_result_marks_listing_scraped(self):
        url = "https://www.costco.com.au/p/1"
        listing = self._listing("COS-1", url, "costcoau")

        self.assertEqual(
            apply_runner_result_to_listings(
                url, Decimal("20.00"), 4, None,
                url_host_contains="costco.", restrict_to_user_id=self.other.id,
            ),
            0,
        )
        updated = apply_runner_result_to_listings(
            url, Decimal("20.00"), 4, None,
            url_host_contains="costco.", restrict_to_user_id=self.user.id,
        )
        self.assertEqual(updated, 1)
        listing.refresh_from_db()
        self.assertEqual(listing.inventory_sync_status, InventorySyncStatus.SCRAPED)
        self.assertEqual(listing.vendor_price, Decimal("20.00"))
        self.assertEqual(listing.inventory, 4)
        self.assertIsNotNone(listing.last_scrape_at)

    def test_runner_error_marks_listing_failed(self):
        url = "https://www.heb.com/product-detail/x/1"
        listing = self._listing("HEB-1", url, "heb")
        apply_runner_result_to_listings(
            url, None, None, "heb_blocked",
            url_host_contains="heb.com", restrict_to_user_id=self.user.id,
        )
        listing.refresh_from_db()
        self.assertEqual(listing.inventory_sync_status, InventorySyncStatus.FAILED)
        self.assertEqual(listing.last_scrape_error, "heb_blocked")
