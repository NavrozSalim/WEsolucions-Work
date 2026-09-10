"""Managed listing scrape: Costway uses the AU CSV feed (SKU → Price + QTY)."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from marketplace.models import Marketplace
from stores.models import Store

from . import listing_service
from . import scrape_progress as scrape_prog
from .models import InventorySyncStatus, ListingStatus, StoreListing


class ManagedListingCostwayScrapeTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="costway_listing_scrape",
            email="costway_listing_scrape@example.com",
            password="pw",
        )
        lasoo, _ = Marketplace.objects.get_or_create(code="lasoo", defaults={"name": "Lasoo"})
        self.store = Store.objects.create(
            user=self.user,
            name="Lasoo Costway",
            region="AU",
            api_token="",
            marketplace=lasoo,
            management_mode="full_store",
            lasoo_environment="staging",
            lasoo_staging_auth_key="test-key",
        )

    def tearDown(self):
        scrape_prog.clear_scrape_progress(self.store.id)

    def _listing(self, sku, *, url="", vendor_id="", source="costwayau"):
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

    def _feed(self, sku, price=109.95, stock=5, link=""):
        entry = {"Posted Price": price, "Posted Inventory": stock}
        if link:
            entry["Product Link"] = link
        compact = "".join(ch for ch in sku.lower() if ch.isalnum())
        by_url = {}
        if link:
            from scrapers.costway_au_ingest import normalize_costway_product_url
            by_url[normalize_costway_product_url(link)] = entry
        return {
            "lookup": {sku: entry},
            "lookup_compact": {compact: entry} if compact else {},
            "lookup_by_url": by_url,
            "feed_rows": 1,
        }

    @patch("stores.nora.load_store_nora_stock_map", return_value=None)
    @patch("scrapers.close_amazon_session")
    @patch("scrapers.get_price_and_stock")
    @patch("scrapers.costway_au_ingest.load_costway_feed_lookups")
    def test_costway_uses_feed_not_product_page(self, mock_feed, mock_price, _close, _nora):
        mock_feed.return_value = self._feed("TP10003", price=109.95, stock=5)
        listing = self._listing(
            "TP10003",
            url="http://au.costway.com/tp10003.html",
            source="costwayau",
        )
        result = listing_service.scrape_listings(self.user, self.store)
        self.assertEqual(result["scraped"], 1)
        self.assertEqual(result["failed"], 0)
        mock_price.assert_not_called()
        listing.refresh_from_db()
        self.assertEqual(listing.inventory_sync_status, InventorySyncStatus.SCRAPED)
        self.assertEqual(float(listing.vendor_price), 109.95)
        self.assertEqual(listing.inventory, 5)
        self.assertEqual(listing.last_scrape_error, "")

    @patch("stores.nora.load_store_nora_stock_map", return_value=None)
    @patch("scrapers.close_amazon_session")
    @patch("scrapers.get_price_and_stock")
    @patch("scrapers.costway_au_ingest.load_costway_feed_lookups")
    def test_costway_matches_sku_without_vendor_url(self, mock_feed, mock_price, _close, _nora):
        mock_feed.return_value = self._feed("TW10004C", price=262.95, stock=407)
        listing = self._listing("TW10004C", url="", source="costwayau")
        listing_service.scrape_listings(self.user, self.store)
        mock_price.assert_not_called()
        listing.refresh_from_db()
        self.assertEqual(listing.inventory_sync_status, InventorySyncStatus.SCRAPED)
        self.assertEqual(float(listing.vendor_price), 262.95)
        self.assertEqual(listing.inventory, 407)

    @patch("stores.nora.load_store_nora_stock_map", return_value=None)
    @patch("scrapers.close_amazon_session")
    @patch("scrapers.get_price_and_stock")
    @patch("scrapers.costway_au_ingest.load_costway_feed_lookups")
    def test_costway_matches_product_link_when_listing_sku_differs(
        self, mock_feed, mock_price, _close, _nora,
    ):
        link = "http://au.costway.com/tp10003.html"
        mock_feed.return_value = self._feed("TP10003", price=109.95, stock=5, link=link)
        listing = self._listing(
            "LASOO-OWN-SKU",
            url=link + "?utm=1",
            source="costway",
        )
        listing_service.scrape_listings(self.user, self.store)
        mock_price.assert_not_called()
        listing.refresh_from_db()
        self.assertEqual(listing.inventory_sync_status, InventorySyncStatus.SCRAPED)
        self.assertEqual(float(listing.vendor_price), 109.95)
        self.assertEqual(listing.inventory, 5)

    @patch("stores.nora.load_store_nora_stock_map", return_value=None)
    @patch("scrapers.close_amazon_session")
    @patch("scrapers.get_price_and_stock")
    @patch("scrapers.costway_au_ingest.load_costway_feed_lookups")
    def test_costway_missing_sku_fails(self, mock_feed, mock_price, _close, _nora):
        mock_feed.return_value = self._feed("OTHER-SKU", price=10, stock=1)
        listing = self._listing("MISSING", url="http://au.costway.com/missing.html")
        result = listing_service.scrape_listings(self.user, self.store)
        self.assertEqual(result["scraped"], 0)
        self.assertEqual(result["failed"], 1)
        mock_price.assert_not_called()
        listing.refresh_from_db()
        self.assertEqual(listing.inventory_sync_status, InventorySyncStatus.FAILED)
        self.assertIn("not in Costway AU CSV feed", listing.last_scrape_error)

    @patch("stores.nora.load_store_nora_stock_map", return_value=None)
    @patch("scrapers.close_amazon_session")
    @patch("scrapers.get_price_and_stock")
    @patch("scrapers.costway_au_ingest.load_costway_feed_lookups")
    def test_mixed_batch_costway_feed_ebay_url(self, mock_feed, mock_price, _close, _nora):
        mock_feed.return_value = self._feed("TP10003", price=109.95, stock=5)
        mock_price.return_value = {"price": 18.5, "stock": 7}
        costway = self._listing("TP10003", url="http://au.costway.com/a.html")
        ebay = self._listing(
            "EBAY-1",
            url="https://www.ebay.com.au/itm/1",
            source="ebayau",
        )
        result = listing_service.scrape_listings(self.user, self.store)
        self.assertEqual(result["scraped"], 2)
        self.assertEqual(mock_price.call_count, 1)
        mock_feed.assert_called_once()
        costway.refresh_from_db()
        ebay.refresh_from_db()
        self.assertEqual(float(costway.vendor_price), 109.95)
        self.assertEqual(costway.inventory, 5)
        self.assertEqual(float(ebay.vendor_price), 18.5)
        self.assertEqual(ebay.inventory, 7)

    @patch("stores.nora.load_store_nora_stock_map", return_value=None)
    @patch("scrapers.close_amazon_session")
    @patch("scrapers.get_price_and_stock")
    @patch("scrapers.costway_au_ingest.load_costway_feed_lookups")
    def test_costway_feed_error_does_not_block_ebay(self, mock_feed, mock_price, _close, _nora):
        mock_feed.side_effect = RuntimeError("geo-restricted to Australian IPs")
        mock_price.return_value = {"price": 18.5, "stock": 7}
        costway = self._listing("TP10003", url="http://au.costway.com/a.html")
        ebay = self._listing(
            "EBAY-1",
            url="https://www.ebay.com.au/itm/1",
            source="ebayau",
        )
        result = listing_service.scrape_listings(self.user, self.store)
        self.assertEqual(result["scraped"], 1)
        self.assertEqual(result["failed"], 1)
        costway.refresh_from_db()
        ebay.refresh_from_db()
        self.assertEqual(costway.inventory_sync_status, InventorySyncStatus.FAILED)
        self.assertIn("geo-restricted", costway.last_scrape_error)
        self.assertEqual(ebay.inventory_sync_status, InventorySyncStatus.SCRAPED)
        self.assertEqual(float(ebay.vendor_price), 18.5)

    @patch("stores.nora.load_store_nora_stock_map", return_value=None)
    @patch("scrapers.close_amazon_session")
    @patch("scrapers.get_price_and_stock")
    @patch("scrapers.vevor_au_ingest.load_vevor_feed_lookups")
    @patch("scrapers.costway_au_ingest.load_costway_feed_lookups")
    def test_costco_url_is_not_treated_as_costway(
        self, mock_cw, mock_vevor, mock_price, _close, _nora,
    ):
        mock_price.return_value = {"price": 20.0, "stock": 2, "error_code": "costco_ingest_only"}
        listing = self._listing(
            "COS-1",
            url="https://www.costco.com.au/p/1",
            source="costcoau",
        )
        listing_service.scrape_listings(self.user, self.store)
        mock_cw.assert_not_called()
        mock_vevor.assert_not_called()
        mock_price.assert_called_once()
