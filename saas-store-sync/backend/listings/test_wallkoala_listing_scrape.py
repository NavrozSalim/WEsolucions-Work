"""Managed listing scrape: Wallkoala uses store Excel (SKU → price + inventory)."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from marketplace.models import Marketplace
from stores.models import Store

from . import listing_service
from . import scrape_progress as scrape_prog
from .models import InventorySyncStatus, ListingStatus, StoreListing


class ManagedListingWallkoalaScrapeTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="wallkoala_listing_scrape",
            email="wallkoala_listing_scrape@example.com",
            password="pw",
        )
        lasoo, _ = Marketplace.objects.get_or_create(code="lasoo", defaults={"name": "Lasoo"})
        self.store = Store.objects.create(
            user=self.user,
            name="Lasoo Wallkoala",
            region="AU",
            api_token="",
            marketplace=lasoo,
            management_mode="full_store",
            lasoo_environment="staging",
            lasoo_staging_auth_key="test-key",
        )

    def tearDown(self):
        scrape_prog.clear_scrape_progress(self.store.id)

    def _listing(self, sku, *, url="", vendor_id="", source="wallkoala"):
        return StoreListing.objects.create(
            user=self.user,
            store=self.store,
            external_product_key=sku,
            external_variant_key=sku,
            sku=sku,
            title=sku,
            vendor_url=url,
            vendor_id=vendor_id or sku,
            source_vendor_code=source,
            status=ListingStatus.READY,
            inventory_sync_status=InventorySyncStatus.PENDING,
        )

    def _feed(self, sku, price=10.5, stock=12):
        entry = {"price": price, "inventory": stock, "sku": sku}
        return {sku: entry, sku.lower(): entry}

    @patch("stores.wallkoala.load_store_wallkoala_feed")
    @patch("stores.nora.load_store_nora_stock_map", return_value=None)
    @patch("scrapers.close_amazon_session")
    @patch("scrapers.get_price_and_stock")
    def test_wallkoala_uses_excel_not_product_page(self, mock_price, _close, _nora, mock_feed):
        mock_feed.return_value = self._feed("WK022-ST-40X30CM", price=10.5, stock=12)
        listing = self._listing(
            "WK022-ST-40X30CM",
            url="https://example.com/should-not-scrape",
            source="wallkoala",
        )
        result = listing_service.scrape_listings(self.user, self.store)
        self.assertEqual(result["scraped"], 1)
        self.assertEqual(result["failed"], 0)
        mock_price.assert_not_called()
        listing.refresh_from_db()
        self.assertEqual(listing.inventory_sync_status, InventorySyncStatus.SCRAPED)
        self.assertEqual(float(listing.vendor_price), 10.5)
        self.assertEqual(listing.inventory, 12)
        self.assertEqual(listing.last_scrape_error, "")

    @patch("stores.wallkoala.load_store_wallkoala_feed")
    @patch("stores.nora.load_store_nora_stock_map", return_value=None)
    @patch("scrapers.close_amazon_session")
    @patch("scrapers.get_price_and_stock")
    def test_wallkoala_matches_without_vendor_url(self, mock_price, _close, _nora, mock_feed):
        mock_feed.return_value = self._feed("WK099", price=8.0, stock=3)
        listing = self._listing("WK099", url="", vendor_id="WK099")
        listing_service.scrape_listings(self.user, self.store)
        mock_price.assert_not_called()
        listing.refresh_from_db()
        self.assertEqual(listing.inventory_sync_status, InventorySyncStatus.SCRAPED)
        self.assertEqual(float(listing.vendor_price), 8.0)
        self.assertEqual(listing.inventory, 3)

    @patch("stores.wallkoala.load_store_wallkoala_feed")
    @patch("stores.nora.load_store_nora_stock_map", return_value=None)
    @patch("scrapers.close_amazon_session")
    @patch("scrapers.get_price_and_stock")
    def test_wallkoala_missing_sku_fails(self, mock_price, _close, _nora, mock_feed):
        mock_feed.return_value = self._feed("OTHER-SKU", price=10, stock=1)
        listing = self._listing("MISSING", url="")
        result = listing_service.scrape_listings(self.user, self.store)
        self.assertEqual(result["scraped"], 0)
        self.assertEqual(result["failed"], 1)
        mock_price.assert_not_called()
        listing.refresh_from_db()
        self.assertEqual(listing.inventory_sync_status, InventorySyncStatus.FAILED)
        self.assertIn("not in Wallkoala Excel", listing.last_scrape_error)

    @patch("stores.wallkoala.load_store_wallkoala_feed", return_value=None)
    @patch("stores.nora.load_store_nora_stock_map", return_value=None)
    @patch("scrapers.close_amazon_session")
    @patch("scrapers.get_price_and_stock")
    def test_wallkoala_no_file_fails(self, mock_price, _close, _nora, _feed):
        listing = self._listing("WK-1", url="")
        result = listing_service.scrape_listings(self.user, self.store)
        self.assertEqual(result["scraped"], 0)
        self.assertEqual(result["failed"], 1)
        listing.refresh_from_db()
        self.assertEqual(listing.inventory_sync_status, InventorySyncStatus.FAILED)
        self.assertIn("Upload a Wallkoala Excel", listing.last_scrape_error)

    @patch("stores.wallkoala.load_store_wallkoala_feed")
    @patch("stores.nora.load_store_nora_stock_map", return_value=None)
    @patch("scrapers.close_amazon_session")
    @patch("scrapers.get_price_and_stock")
    def test_mixed_batch_wallkoala_excel_ebay_url(self, mock_price, _close, _nora, mock_feed):
        mock_feed.return_value = self._feed("WK022-ST-40X30CM", price=10.5, stock=12)
        mock_price.return_value = {"price": 18.5, "stock": 7}
        wk = self._listing("WK022-ST-40X30CM", url="")
        ebay = self._listing(
            "EBAY-1",
            url="https://www.ebay.com.au/itm/1",
            vendor_id="",
            source="ebayau",
        )
        result = listing_service.scrape_listings(self.user, self.store)
        self.assertEqual(result["scraped"], 2)
        self.assertEqual(mock_price.call_count, 1)
        wk.refresh_from_db()
        ebay.refresh_from_db()
        self.assertEqual(float(wk.vendor_price), 10.5)
        self.assertEqual(wk.inventory, 12)
        self.assertEqual(float(ebay.vendor_price), 18.5)
        self.assertEqual(ebay.inventory, 7)
