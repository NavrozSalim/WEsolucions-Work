"""Tests for Tickets Management (local store + marketplace sync hooks)."""
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from marketplace.models import Marketplace
from stores.models import Store

from . import ticket_service
from .lasoo.client import LasooResult
from .models import SupportTicket, TicketMessageDirection, TicketStatus


class TicketServiceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="ticku", email="t@example.com", password="pw")
        lasoo, _ = Marketplace.objects.get_or_create(code="lasoo", defaults={"name": "Lasoo"})
        self.store = Store.objects.create(
            user=self.user,
            name="Ticket Store",
            region="AU",
            api_token="",
            marketplace=lasoo,
            management_mode="full_store",
            lasoo_environment="staging",
            lasoo_staging_auth_key="test-key",
        )

    def test_create_test_ticket_and_reply_local(self):
        created = ticket_service.create_test_ticket(self.user, self.store)
        self.assertTrue(created["ok"])
        ticket = SupportTicket.objects.get(id=created["ticket_id"])
        self.assertEqual(ticket.status, TicketStatus.OPEN)
        self.assertEqual(ticket.messages.count(), 1)

        with patch("listings.ticket_service.LasooClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.auth_key = "test-key"
            mock_client.send.return_value = LasooResult(
                ok=False,
                data={"message": "Fail.. Query does not exist. Check the name and version"},
                message="Fail.. Query does not exist. Check the name and version",
                status=500,
            )
            mock_cls.return_value = mock_client
            result = ticket_service.reply(ticket, body="Thanks, we will help shortly.")

        self.assertTrue(result["ok"])
        self.assertFalse(result["marketplace_ok"])
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, TicketStatus.ANSWERED)
        outbound = ticket.messages.filter(direction=TicketMessageDirection.OUTBOUND).first()
        self.assertIsNotNone(outbound)
        self.assertEqual(outbound.body, "Thanks, we will help shortly.")
        self.assertFalse(outbound.delivered_to_marketplace)

    @patch("listings.ticket_service.LasooClient")
    def test_fetch_upserts_marketplace_ticket(self, mock_cls):
        mock_client = MagicMock()
        mock_client.auth_key = "test-key"
        mock_client.send.return_value = LasooResult(
            ok=True,
            data={
                "success": True,
                "results": {
                    "success": True,
                    "tickets": [
                        {
                            "id": 501,
                            "subject": "Where is my order?",
                            "status": "open",
                            "customer": {
                                "firstName": "Sam",
                                "surname": "Buyer",
                                "emailAddress": "sam@example.com",
                            },
                            "invoiceId": 10001,
                            "messages": [
                                {
                                    "id": 9001,
                                    "body": "Any update?",
                                    "sentByType": "customer",
                                    "createdAt": "2026-07-14T10:00:00Z",
                                }
                            ],
                        }
                    ],
                },
            },
            message="Success.",
            status=200,
        )
        mock_cls.return_value = mock_client

        result = ticket_service.fetch(self.user, self.store)
        self.assertTrue(result["ok"])
        self.assertTrue(result["marketplace_ok"])
        self.assertEqual(result["fetched"], 1)
        ticket = SupportTicket.objects.get(external_ticket_key="501")
        self.assertEqual(ticket.subject, "Where is my order?")
        self.assertEqual(ticket.customer_email, "sam@example.com")
        self.assertEqual(ticket.messages.count(), 1)
