"""Created-products publish progress (banner survives reload)."""
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from marketplace.models import Marketplace
from stores.models import Store

from . import listing_service
from . import publish_progress as pub_prog
from .errors import MarketplaceError
from .models import ListingStatus, StoreListing


class PublishProgressCacheTests(TestCase):
    def setUp(self):
        self.store_id = "11111111-1111-1111-1111-111111111111"

    def tearDown(self):
        pub_prog.clear_publish_progress(self.store_id)

    def test_begin_then_get_is_active(self):
        begun = pub_prog.begin_publish_progress(
            self.store_id,
            job_id="job-1",
            queued=24,
            message="Creating 24 listing(s) on MyDeal.",
        )
        self.assertTrue(begun["active"])
        live = pub_prog.get_publish_progress(self.store_id)
        self.assertTrue(live["active"])
        self.assertEqual(live["job_id"], "job-1")
        self.assertEqual(live["queued"], 24)

    def test_finish_clears_active(self):
        pub_prog.begin_publish_progress(self.store_id, job_id="job-1", queued=2)
        done = pub_prog.finish_publish_progress(
            self.store_id,
            job_id="job-1",
            message="Published 2 listing(s).",
        )
        self.assertFalse(done["active"])
        self.assertEqual(pub_prog.get_publish_progress(self.store_id)["message"], "Published 2 listing(s).")

    def test_finish_ignores_other_job(self):
        pub_prog.begin_publish_progress(self.store_id, job_id="job-new", queued=1)
        pub_prog.finish_publish_progress(self.store_id, job_id="job-old", message="stale")
        live = pub_prog.get_publish_progress(self.store_id)
        self.assertTrue(live["active"])
        self.assertEqual(live["job_id"], "job-new")


class MyDealPublishProgressServiceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="pub_prog",
            email="pub_prog@example.com",
            password="pw",
        )
        mydeal, _ = Marketplace.objects.get_or_create(code="mydeal", defaults={"name": "MyDeal"})
        self.store = Store.objects.create(
            user=self.user,
            name="MyDeal Publish Progress",
            region="AU",
            api_token="tok",
            marketplace=mydeal,
            management_mode="full_store",
        )
        self.listing = StoreListing.objects.create(
            user=self.user,
            store=self.store,
            external_product_key="PD1",
            external_variant_key="PD1",
            sku="PD1",
            title="Progress listing",
            status=ListingStatus.READY,
        )

    def tearDown(self):
        pub_prog.clear_publish_progress(self.store.id)

    @patch("listings.tasks.publish_store_listings.apply_async")
    @patch("listings.listing_service._collect_publishable")
    @patch("listings.mydeal.products.requeue_unconfirmed_uploads")
    @patch("listings.mydeal.products.confirm_false_failed_uploads", return_value=0)
    def test_start_publish_records_progress(self, _confirm, _requeue, mock_collect, mock_apply):
        mock_apply.return_value = MagicMock(id="celery-pub-1")
        mock_collect.return_value = [self.listing]
        result = listing_service.start_publish_async(self.user, self.store)
        self.assertTrue(result["async"])
        self.assertEqual(result["job_id"], "celery-pub-1")
        self.assertEqual(result["queued"], 1)
        live = pub_prog.get_publish_progress(self.store.id)
        self.assertTrue(live["active"])
        self.assertEqual(live["job_id"], "celery-pub-1")
        self.assertIn("leave this page", result["message"])

    @patch("celery.result.AsyncResult")
    @patch("listings.tasks.publish_store_listings.apply_async")
    @patch("listings.listing_service._collect_publishable")
    @patch("listings.mydeal.products.requeue_unconfirmed_uploads")
    @patch("listings.mydeal.products.confirm_false_failed_uploads", return_value=0)
    def test_second_publish_blocked_while_active(
        self, _confirm, _requeue, mock_collect, mock_apply, mock_async,
    ):
        mock_async.return_value.ready.return_value = False
        mock_apply.return_value = MagicMock(id="celery-pub-1")
        mock_collect.return_value = [self.listing]
        listing_service.start_publish_async(self.user, self.store)
        mock_apply.reset_mock()
        with self.assertRaises(MarketplaceError) as ctx:
            listing_service.start_publish_async(self.user, self.store)
        self.assertIn("already running", str(ctx.exception))
        mock_apply.assert_not_called()


class MyDealPublishProgressApiTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="pub_prog_api",
            email="pub_prog_api@example.com",
            password="pw",
        )
        mydeal, _ = Marketplace.objects.get_or_create(code="mydeal", defaults={"name": "MyDeal"})
        self.store = Store.objects.create(
            user=self.user,
            name="MyDeal Publish API",
            region="AU",
            api_token="tok",
            marketplace=mydeal,
            management_mode="full_store",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def tearDown(self):
        pub_prog.clear_publish_progress(self.store.id)

    def test_progress_endpoint_idle(self):
        res = self.client.get(f"/api/v1/stores/{self.store.id}/listings/publish/progress/")
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.data["active"])
        self.assertEqual(res.data["job_id"], "")

    def test_progress_endpoint_active(self):
        pub_prog.begin_publish_progress(
            self.store.id,
            job_id="job-live",
            queued=249,
            message="Creating 249 listing(s) on MyDeal.",
        )
        with patch("celery.result.AsyncResult") as mock_async:
            mock_async.return_value.ready.return_value = False
            res = self.client.get(f"/api/v1/stores/{self.store.id}/listings/publish/progress/")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["active"])
        self.assertEqual(res.data["job_id"], "job-live")
        self.assertEqual(res.data["queued"], 249)

    @patch("listings.mydeal.products.requeue_unconfirmed_uploads")
    @patch("listings.mydeal.products.confirm_false_failed_uploads", return_value=0)
    def test_created_list_includes_publish_job(self, _confirm, _requeue):
        StoreListing.objects.create(
            user=self.user,
            store=self.store,
            external_product_key="PD1",
            external_variant_key="PD1",
            sku="PD1",
            title="Row",
            status=ListingStatus.READY,
        )
        pub_prog.begin_publish_progress(self.store.id, job_id="job-list", queued=1)
        with patch("celery.result.AsyncResult") as mock_async:
            mock_async.return_value.ready.return_value = False
            res = self.client.get(
                f"/api/v1/stores/{self.store.id}/listings/",
                {"view": "created", "page": 1, "page_size": 10},
            )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["publish_job"]["active"])
        self.assertEqual(res.data["publish_job"]["job_id"], "job-list")
