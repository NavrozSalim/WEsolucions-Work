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
        self.assertFalse(result.get("active", True))
        self.assertTrue(scrape_prog.is_scrape_cancel_requested(self.store.id))
        prog = scrape_prog.get_scrape_progress(self.store.id)
        self.assertFalse(prog["active"])
        self.assertTrue(prog.get("cancelled"))
        self.assertEqual(prog.get("phase"), "cancelled")

    def test_progress_write_does_not_clear_cancel(self):
        row = self._listing("RACE-1")
        scrape_prog.begin_scrape_progress(
            self.store.id,
            total=1,
            listing_ids=[row.id],
        )
        scrape_prog.request_scrape_cancel(self.store.id)
        scrape_prog.set_scrape_progress(
            self.store.id,
            processed=1,
            phase="running",
            current_sku="RACE-1",
            message="Scraping 1 of 1…",
        )
        self.assertTrue(scrape_prog.is_scrape_cancel_requested(self.store.id))
        self.assertTrue(scrape_prog.is_scrape_cancel_requested(str(self.store.id)))
        self.assertTrue(scrape_prog.get_scrape_progress(self.store.id)["cancel_requested"])

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

    def test_enrich_closes_banner_when_stop_requested(self):
        rows = [self._listing("PEND-1"), self._listing("PEND-2")]
        scrape_prog.begin_scrape_progress(
            self.store.id,
            total=2,
            listing_ids=[row.id for row in rows],
        )
        scrape_prog.request_scrape_cancel(self.store.id)
        live = scrape_prog.enrich_progress_from_listings(self.store.id)
        self.assertFalse(live["active"])
        self.assertTrue(live.get("cancelled"))
        self.assertEqual(live.get("phase"), "cancelled")
        self.assertTrue(scrape_prog.is_scrape_cancel_requested(self.store.id))

    def test_stale_finish_does_not_clobber_new_generation(self):
        row = self._listing("GEN-1")
        first = scrape_prog.begin_scrape_progress(
            self.store.id,
            total=1,
            listing_ids=[row.id],
        )
        old_gen = first["generation"]
        self.assertTrue(old_gen)
        scrape_prog.finish_scrape_progress(
            self.store.id,
            cancelled=True,
            job_generation=old_gen,
        )
        second = scrape_prog.begin_scrape_progress(
            self.store.id,
            total=1,
            listing_ids=[row.id],
        )
        new_gen = second["generation"]
        self.assertNotEqual(old_gen, new_gen)
        self.assertTrue(second["active"])
        scrape_prog.set_scrape_progress(
            self.store.id,
            job_generation=old_gen,
            processed=1,
            current_sku="STALE",
            message="leftover thread",
        )
        scrape_prog.finish_scrape_progress(
            self.store.id,
            scraped=1,
            job_generation=old_gen,
        )
        live = scrape_prog.get_scrape_progress(self.store.id)
        self.assertTrue(live["active"])
        self.assertEqual(live["generation"], new_gen)
        self.assertNotEqual(live.get("current_sku"), "STALE")

    @patch("threading.Thread")
    @patch("stores.nora.load_store_nora_stock_map", return_value=None)
    def test_start_scrape_allowed_after_cancel(self, _nora, mock_thread):
        mock_thread.return_value.start = lambda: None
        row = self._listing("START-1")
        scrape_prog.begin_scrape_progress(
            self.store.id,
            total=1,
            listing_ids=[row.id],
        )
        listing_service.cancel_scrape(self.user, self.store)
        result = listing_service.start_scrape_async(self.user, self.store)
        self.assertTrue(result.get("started"))
        self.assertFalse(result.get("already_running"))

    @patch("threading.Thread")
    @patch("stores.nora.load_store_nora_stock_map", return_value=None)
    def test_store_wide_start_requeues_scraped_and_synced(self, _nora, mock_thread):
        mock_thread.return_value.start = lambda: None
        pending = self._listing("P")
        scraped = self._listing("S")
        scraped.inventory_sync_status = InventorySyncStatus.SCRAPED
        scraped.save(update_fields=["inventory_sync_status"])
        synced = self._listing("Y")
        synced.inventory_sync_status = InventorySyncStatus.SYNCED
        synced.save(update_fields=["inventory_sync_status"])
        failed = self._listing("F")
        failed.inventory_sync_status = InventorySyncStatus.FAILED
        failed.save(update_fields=["inventory_sync_status"])

        result = listing_service.start_scrape_async(self.user, self.store)

        self.assertTrue(result.get("started"))
        self.assertEqual(result["total"], 4)
        mock_thread.assert_called()
        for row in (pending, scraped, synced, failed):
            row.refresh_from_db()
            self.assertEqual(row.inventory_sync_status, InventorySyncStatus.PENDING)

    @patch("threading.Thread")
    @patch("stores.nora.load_store_nora_stock_map", return_value=None)
    def test_store_wide_start_supersedes_leftover_active_job(self, _nora, mock_thread):
        mock_thread.return_value.start = lambda: None
        leftover = self._listing("LEFT")
        scraped = self._listing("DONE")
        scraped.inventory_sync_status = InventorySyncStatus.SCRAPED
        scraped.save(update_fields=["inventory_sync_status"])
        first = scrape_prog.begin_scrape_progress(
            self.store.id,
            total=1,
            listing_ids=[leftover.id],
        )
        old_gen = first["generation"]

        result = listing_service.start_scrape_async(self.user, self.store)

        self.assertTrue(result.get("started"))
        self.assertFalse(result.get("already_running"))
        self.assertEqual(result["total"], 2)
        live = scrape_prog.get_scrape_progress(self.store.id)
        self.assertTrue(live["active"])
        self.assertNotEqual(live["generation"], old_gen)
        scraped.refresh_from_db()
        leftover.refresh_from_db()
        self.assertEqual(scraped.inventory_sync_status, InventorySyncStatus.PENDING)
        self.assertEqual(leftover.inventory_sync_status, InventorySyncStatus.PENDING)

    @patch("threading.Thread")
    @patch("stores.nora.load_store_nora_stock_map", return_value=None)
    def test_row_scrape_does_not_kill_active_store_scrape(self, _nora, mock_thread):
        mock_thread.return_value.start = lambda: None
        a = self._listing("A")
        b = self._listing("B")
        scrape_prog.begin_scrape_progress(
            self.store.id,
            total=2,
            listing_ids=[a.id, b.id],
        )

        result = listing_service.start_scrape_async(
            self.user, self.store, listing_ids=[str(b.id)]
        )

        self.assertTrue(result.get("already_running"))
        self.assertFalse(result.get("started"))
        mock_thread.assert_not_called()
        b.refresh_from_db()
        self.assertEqual(b.inventory_sync_status, InventorySyncStatus.PENDING)

    @patch("threading.Thread")
    @patch("stores.nora.load_store_nora_stock_map", return_value=None)
    def test_row_scrape_when_idle_only_resets_that_listing(self, _nora, mock_thread):
        mock_thread.return_value.start = lambda: None
        a = self._listing("ONLY")
        a.inventory_sync_status = InventorySyncStatus.SCRAPED
        a.save(update_fields=["inventory_sync_status"])
        b = self._listing("LEAVE")
        b.inventory_sync_status = InventorySyncStatus.SCRAPED
        b.save(update_fields=["inventory_sync_status"])

        result = listing_service.start_scrape_async(
            self.user, self.store, listing_ids=[str(a.id)]
        )

        self.assertTrue(result.get("started"))
        self.assertEqual(result["total"], 1)
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual(a.inventory_sync_status, InventorySyncStatus.PENDING)
        self.assertEqual(b.inventory_sync_status, InventorySyncStatus.SCRAPED)


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
        prog = scrape_prog.get_scrape_progress(self.store.id)
        self.assertFalse(prog["active"])
        self.assertEqual(prog.get("phase"), "cancelled")
