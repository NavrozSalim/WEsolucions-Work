"""Managed listing scrape: Vevor uses the XLSX feed; Nora stays hybrid; eBay scrapes URLs."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from marketplace.models import Marketplace
from stores.models import Store

from . import listing_service
from . import scrape_progress as scrape_prog
from .models import InventorySyncStatus, ListingStatus, StoreListing


class ManagedListingVendorScrapeTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="vevor_listing_scrape",
            email="vevor_listing_scrape@example.com",
            password="pw",
        )
        lasoo, _ = Marketplace.objects.get_or_create(code="lasoo", defaults={"name": "Lasoo"})
        self.store = Store.objects.create(
            user=self.user,
            name="Lasoo Mixed Vendors",
            region="AU",
            api_token="",
            marketplace=lasoo,
            management_mode="full_store",
            lasoo_environment="staging",
            lasoo_staging_auth_key="test-key",
        )

    def tearDown(self):
        scrape_prog.clear_scrape_progress(self.store.id)

    def _listing(self, sku, *, url="", vendor_id="", source=""):
        return StoreListing.objects.create(
            user=self.user,
            store=self.store,
            external_product_key=sku,
            external_variant_key=sku,
            sku=sku,
            title=sku,
            vendor_url=url,
            vendor_id=vendor_id,
            source_vendor_code=source,
            status=ListingStatus.READY,
            inventory_sync_status=InventorySyncStatus.PENDING,
        )

    def _feed(self, sku, price=40.5, stock=9, link=""):
        entry = {"Posted Price": price, "Posted Inventory": stock}
        if link:
            entry["Product Link"] = link
        compact = "".join(ch for ch in sku.lower() if ch.isalnum())
        by_url = {}
        if link:
            from scrapers.vevor_au_ingest import normalize_vevor_product_url
            by_url[normalize_vevor_product_url(link)] = entry
        return {
            "lookup": {sku: entry},
            "lookup_compact": {compact: entry} if compact else {},
            "lookup_by_url": by_url,
            "feed_rows": 1,
        }

    @patch("stores.nora.load_store_nora_stock_map", return_value=None)
    @patch("scrapers.close_amazon_session")
    @patch("scrapers.get_price_and_stock")
    @patch("scrapers.vevor_au_ingest.load_vevor_feed_lookups")
    def test_vevor_uses_feed_not_product_page(self, mock_feed, mock_price, _close, _nora):
        mock_feed.return_value = self._feed("VEVOR-SKU-1", price=40.5, stock=9)
        listing = self._listing(
            "VEVOR-SKU-1",
            url="https://www.vevor.com.au/winch.html",
            source="vevorau",
        )
        result = listing_service.scrape_listings(self.user, self.store)
        self.assertEqual(result["scraped"], 1)
        self.assertEqual(result["failed"], 0)
        mock_price.assert_not_called()
        listing.refresh_from_db()
        self.assertEqual(listing.inventory_sync_status, InventorySyncStatus.SCRAPED)
        self.assertEqual(float(listing.vendor_price), 40.5)
        self.assertEqual(listing.inventory, 9)
        self.assertEqual(listing.last_scrape_error, "")

    @patch("stores.nora.load_store_nora_stock_map", return_value=None)
    @patch("scrapers.close_amazon_session")
    @patch("scrapers.get_price_and_stock")
    @patch("scrapers.vevor_au_ingest.load_vevor_feed_lookups")
    def test_vevor_matches_product_link_when_listing_sku_differs(
        self, mock_feed, mock_price, _close, _nora,
    ):
        link = "https://www.vevor.com.au/winch.html"
        mock_feed.return_value = self._feed("00PSIX5NJR2YV2MH3V0", price=178.9, stock=11, link=link)
        listing = self._listing(
            "LASOO-OWN-SKU",
            url=link + "?utm=1",
            source="vevor",
        )
        listing_service.scrape_listings(self.user, self.store)
        mock_price.assert_not_called()
        listing.refresh_from_db()
        self.assertEqual(listing.inventory_sync_status, InventorySyncStatus.SCRAPED)
        self.assertEqual(float(listing.vendor_price), 178.9)
        self.assertEqual(listing.inventory, 11)

    @patch("stores.nora.load_store_nora_stock_map", return_value=None)
    @patch("scrapers.close_amazon_session")
    @patch("scrapers.get_price_and_stock")
    @patch("scrapers.vevor_au_ingest.load_vevor_feed_lookups")
    def test_vevor_missing_sku_fails(self, mock_feed, mock_price, _close, _nora):
        mock_feed.return_value = self._feed("OTHER-SKU", price=10, stock=1)
        listing = self._listing(
            "MISSING",
            url="https://www.vevor.com.au/missing.html",
            source="vevorau",
        )
        result = listing_service.scrape_listings(self.user, self.store)
        self.assertEqual(result["scraped"], 0)
        self.assertEqual(result["failed"], 1)
        mock_price.assert_not_called()
        listing.refresh_from_db()
        self.assertEqual(listing.inventory_sync_status, InventorySyncStatus.FAILED)
        self.assertIn("not in Vevor AU XLSX feed", listing.last_scrape_error)

    @patch("stores.nora.get_nora_inventory_settings", return_value=None)
    @patch("stores.nora.load_store_nora_stock_map", return_value={"NORA-1": 15})
    @patch("scrapers.close_amazon_session")
    @patch("scrapers.get_price_and_stock")
    @patch("scrapers.vevor_au_ingest.load_vevor_feed_lookups")
    def test_nora_price_from_link_stock_from_file(
        self, mock_feed, mock_price, _close, _nora_map, _nora_inv,
    ):
        mock_price.return_value = {"price": 22.0, "stock": 99}
        listing = self._listing(
            "NORA-LISTING",
            url="https://www.ebay.com.au/itm/123",
            vendor_id="NORA-1",
            source="noraau",
        )
        listing_service.scrape_listings(self.user, self.store)
        mock_feed.assert_not_called()
        mock_price.assert_called_once()
        listing.refresh_from_db()
        self.assertEqual(listing.inventory_sync_status, InventorySyncStatus.SCRAPED)
        self.assertEqual(float(listing.vendor_price), 22.0)
        self.assertEqual(listing.inventory, 15)

    @patch("stores.nora.load_store_nora_stock_map", return_value=None)
    @patch("scrapers.close_amazon_session")
    @patch("scrapers.get_price_and_stock")
    @patch("scrapers.vevor_au_ingest.load_vevor_feed_lookups")
    def test_mixed_batch_vevor_feed_ebay_url(self, mock_feed, mock_price, _close, _nora):
        mock_feed.return_value = self._feed("VEVOR-SKU-1", price=40.0, stock=3)
        mock_price.return_value = {"price": 18.5, "stock": 7}
        vevor = self._listing(
            "VEVOR-SKU-1",
            url="https://www.vevor.com.au/a.html",
            source="vevorau",
        )
        ebay = self._listing(
            "EBAY-1",
            url="https://www.ebay.com.au/itm/1",
            source="ebayau",
        )
        result = listing_service.scrape_listings(self.user, self.store)
        self.assertEqual(result["scraped"], 2)
        self.assertEqual(mock_price.call_count, 1)
        mock_feed.assert_called_once()
        vevor.refresh_from_db()
        ebay.refresh_from_db()
        self.assertEqual(float(vevor.vendor_price), 40.0)
        self.assertEqual(vevor.inventory, 3)
        self.assertEqual(float(ebay.vendor_price), 18.5)
        self.assertEqual(ebay.inventory, 7)

    @patch("stores.nora.load_store_nora_stock_map", return_value=None)
    @patch("scrapers.close_amazon_session")
    @patch("scrapers.get_price_and_stock")
    @patch("scrapers.vevor_au_ingest.load_vevor_feed_lookups")
    def test_vevor_feed_error_does_not_block_ebay(self, mock_feed, mock_price, _close, _nora):
        mock_feed.side_effect = RuntimeError("S3 timeout")
        mock_price.return_value = {"price": 18.5, "stock": 7}
        vevor = self._listing(
            "VEVOR-SKU-1",
            url="https://www.vevor.com.au/a.html",
            source="vevorau",
        )
        ebay = self._listing(
            "EBAY-1",
            url="https://www.ebay.com.au/itm/1",
            source="ebayau",
        )
        result = listing_service.scrape_listings(self.user, self.store)
        self.assertEqual(result["scraped"], 1)
        self.assertEqual(result["failed"], 1)
        vevor.refresh_from_db()
        ebay.refresh_from_db()
        self.assertEqual(vevor.inventory_sync_status, InventorySyncStatus.FAILED)
        self.assertIn("S3 timeout", vevor.last_scrape_error)
        self.assertEqual(ebay.inventory_sync_status, InventorySyncStatus.SCRAPED)
        self.assertEqual(float(ebay.vendor_price), 18.5)
