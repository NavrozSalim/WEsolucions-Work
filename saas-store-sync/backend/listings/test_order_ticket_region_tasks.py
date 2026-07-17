"""Region filtering for orders/tickets Celery sync helpers."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from marketplace.models import Marketplace
from stores.models import Store

from listings.tasks import (
    REGION_AU,
    REGION_USA,
    sync_store_orders,
    sync_store_tickets,
)


class OrderTicketRegionSyncTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="ordtick",
            email="ordtick@example.com",
            password="pw",
        )
        lasoo, _ = Marketplace.objects.get_or_create(code="lasoo", defaults={"name": "Lasoo"})
        reverb, _ = Marketplace.objects.get_or_create(code="reverb", defaults={"name": "Reverb"})

        self.us_lasoo = Store.objects.create(
            user=self.user,
            name="US Lasoo",
            region=REGION_USA,
            api_token="",
            marketplace=lasoo,
            management_mode="full_store",
            lasoo_environment="staging",
            lasoo_staging_auth_key="k",
        )
        self.au_lasoo = Store.objects.create(
            user=self.user,
            name="AU Lasoo",
            region=REGION_AU,
            api_token="",
            marketplace=lasoo,
            management_mode="full_store",
            lasoo_environment="staging",
            lasoo_staging_auth_key="k",
        )
        self.us_reverb = Store.objects.create(
            user=self.user,
            name="US Reverb",
            region=REGION_USA,
            api_token="tok",
            marketplace=reverb,
            management_mode="full_store",
        )
        # Inventory-only must be ignored.
        Store.objects.create(
            user=self.user,
            name="US inventory only",
            region=REGION_USA,
            api_token="",
            marketplace=lasoo,
            management_mode="inventory_only",
        )

    @patch("listings.tasks.ticket_service.fetch")
    def test_ticket_sync_filters_usa_region(self, mock_fetch):
        mock_fetch.return_value = {"ok": True, "fetched": 1, "marketplace_supported": True}
        result = sync_store_tickets(region=REGION_USA)
        self.assertEqual(result["region"], REGION_USA)
        self.assertEqual(result["stores"], 2)
        self.assertEqual(result["fetched"], 2)
        called_ids = {call.args[1].id for call in mock_fetch.call_args_list}
        self.assertEqual(called_ids, {self.us_lasoo.id, self.us_reverb.id})

    @patch("listings.tasks.ticket_service.fetch")
    def test_ticket_sync_filters_au_region(self, mock_fetch):
        mock_fetch.return_value = {"ok": True, "fetched": 3, "marketplace_supported": True}
        result = sync_store_tickets(region=REGION_AU)
        self.assertEqual(result["stores"], 1)
        self.assertEqual(result["fetched"], 3)
        self.assertEqual(mock_fetch.call_args_list[0].args[1].id, self.au_lasoo.id)

    @patch("listings.tasks.order_service.fetch")
    def test_order_sync_filters_usa_region(self, mock_fetch):
        mock_fetch.return_value = {"ok": True, "fetched": 2}
        result = sync_store_orders(region=REGION_USA)
        self.assertEqual(result["stores"], 2)
        self.assertEqual(result["fetched"], 4)
        called_ids = {call.args[1].id for call in mock_fetch.call_args_list}
        self.assertEqual(called_ids, {self.us_lasoo.id, self.us_reverb.id})

    @patch("listings.tasks.order_service.fetch")
    def test_order_sync_reverb_only_legacy(self, mock_fetch):
        mock_fetch.return_value = {"ok": True, "fetched": 1}
        result = sync_store_orders(region=None, marketplace_codes=["reverb"])
        self.assertEqual(result["stores"], 1)
        self.assertEqual(mock_fetch.call_args_list[0].args[1].id, self.us_reverb.id)
