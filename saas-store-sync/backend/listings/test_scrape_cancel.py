"""Cooperative cancel for managed-listing inventory scrapes (Lasoo / Nora)."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from marketplace.models import Marketplace
from stores.models import Store

from . import listing_service
from . import scrape_progress as scrape_prog
from .models import InventorySyncStatus, ListingStatus, StoreListing


class ManagedListingScrapeCancelTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="scrape_cancel",
            email="scrape_cancel@example.com",
            password="pw",
        )
        lasoo, _ = Marketplace.objects.get_or_create(code="lasoo", defaults={"name": "Lasoo"})
        self.store = Store.objects.create(
            user=self.user,
            name="Lasoo Cancel Store",
            region="AU",
            api_token="",
            marketplace=lasoo,
            management_mode="full_store",
            lasoo_environment="staging",
            lasoo_staging_auth_key="test-key",
        )

    def tearDown(self):
        scrape_prog.clear_scrape_progress(self.store.id)

    def _listing(self, sku):
        return StoreListing.objects.create(
            user=self.user,
            store=self.store,
            external_product_key=sku,
            external_variant_key=sku,
            sku=sku,
            title=sku,
            vendor_url=f"https://www.ebay.com.au/itm/{sku}",
            status=ListingStatus.READY,
            inventory_sync_status=InventorySyncStatus.PENDING,
        )

    def test_cancel_when_idle(self):
        result = listing_service.cancel_scrape(self.user, self.store)
        self.assertFalse(result["cancelled"])
        self.assertFalse(result["server_scrape_stopped"])

    def test_cancel_flags_active_scrape(self):
        rows = [self._listing("A"), self._listing("B")]
        scrape_prog.begin_scrape_progress(
            self.store.id,
            total=2,
            listing_ids=[row.id for row in rows],
        )
        result = listing_service.cancel_scrape(self.user, self.store)
        self.assertTrue(result["cancelled"])
        self.assertTrue(result["server_scrape_stopped"])
        self.assertTrue(scrape_prog.is_scrape_cancel_requested(self.store.id))

    @patch("stores.nora.load_store_nora_stock_map", return_value=None)
    @patch("scrapers.close_amazon_session")
    @patch("scrapers.get_price_and_stock")
    def test_scrape_listings_stops_after_cancel(self, mock_price, _close, _nora):
        first = self._listing("SKU-1")
        second = self._listing("SKU-2")
        third = self._listing("SKU-3")

        def _price(*args, **kwargs):
            scrape_prog.request_scrape_cancel(self.store.id)
            return {"price": 12.5, "stock": 4}

        mock_price.side_effect = _price
        result = listing_service.scrape_listings(self.user, self.store)
        self.assertTrue(result.get("cancelled"))
        self.assertEqual(result["scraped"], 1)
        self.assertEqual(mock_price.call_count, 1)

        first.refresh_from_db()
        second.refresh_from_db()
        third.refresh_from_db()
        statuses = [
            first.inventory_sync_status,
            second.inventory_sync_status,
            third.inventory_sync_status,
        ]
        self.assertEqual(statuses.count(InventorySyncStatus.SCRAPED), 1)
        self.assertEqual(statuses.count(InventorySyncStatus.PENDING), 2)

        prog = scrape_prog.get_scrape_progress(self.store.id)
        self.assertFalse(prog["active"])
        self.assertTrue(prog.get("cancelled"))
        self.assertEqual(prog.get("phase"), "cancelled")


@override_settings(DEBUG=True)
class ManagedListingScrapeCancelViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="scrape_cancel_api",
            email="scrape_cancel_api@example.com",
            password="pw",
        )
        lasoo, _ = Marketplace.objects.get_or_create(code="lasoo", defaults={"name": "Lasoo"})
        self.store = Store.objects.create(
            user=self.user,
            name="Lasoo Cancel API Store",
            region="AU",
            api_token="",
            marketplace=lasoo,
            management_mode="full_store",
            lasoo_environment="staging",
            lasoo_staging_auth_key="test-key",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = f"/api/v1/stores/{self.store.id}/listings/scrape/cancel/"

    def tearDown(self):
        scrape_prog.clear_scrape_progress(self.store.id)

    def test_cancel_endpoint_idle(self):
        res = self.client.post(self.url, {}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.data["cancelled"])

    def test_cancel_endpoint_active(self):
        listing = StoreListing.objects.create(
            user=self.user,
            store=self.store,
            external_product_key="API-1",
            external_variant_key="API-1",
            sku="API-1",
            vendor_url="https://www.ebay.com.au/itm/API-1",
            status=ListingStatus.READY,
        )
        scrape_prog.begin_scrape_progress(
            self.store.id,
            total=1,
            listing_ids=[listing.id],
        )
        res = self.client.post(self.url, {}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["cancelled"])
        self.assertTrue(res.data["server_scrape_stopped"])
        self.assertTrue(scrape_prog.is_scrape_cancel_requested(self.store.id))
