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


class ReverbTicketServiceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="rvtick", email="rvt@example.com", password="pw")
        reverb, _ = Marketplace.objects.get_or_create(code="reverb", defaults={"name": "Reverb"})
        self.store = Store.objects.create(
            user=self.user,
            name="Reverb Ticket Store",
            region="USA",
            api_token="reverb-token-1234567890",
            marketplace=reverb,
            management_mode="full_store",
        )

    def test_upsert_conversation_with_messages(self):
        from listings.reverb import tickets as reverb_tickets

        raw = {
            "id": "conv-42",
            "subject": "About my pedal",
            "unread": True,
            "other_user": {"name": "Buyer Bob", "email": "bob@example.com"},
            "order_number": "RV-99",
            "messages": [
                {
                    "id": "m1",
                    "body": "Is this still available?",
                    "created_at": "2026-07-14T10:00:00Z",
                    "from_me": False,
                    "author": {"name": "Buyer Bob"},
                },
                {
                    "id": "m2",
                    "body": "Yes it is!",
                    "created_at": "2026-07-14T11:00:00Z",
                    "from_me": True,
                    "author": {"name": "Seller"},
                },
            ],
        }
        ticket = reverb_tickets.upsert_conversation(self.user, self.store, raw)
        self.assertIsNotNone(ticket)
        self.assertEqual(ticket.external_ticket_key, "conv-42")
        self.assertEqual(ticket.customer_name, "Buyer Bob")
        self.assertEqual(ticket.related_order_key, "RV-99")
        self.assertEqual(ticket.messages.count(), 2)
        inbound = ticket.messages.filter(direction=TicketMessageDirection.INBOUND).first()
        self.assertEqual(inbound.body, "Is this still available?")

    def test_upsert_uses_other_party_and_links_order_by_listing(self):
        """Production Reverb payloads use other_party + listing.id (no order_number)."""
        from listings.models import MarketplaceOrder
        from listings.reverb import tickets as reverb_tickets

        MarketplaceOrder.objects.create(
            store=self.store,
            user=self.user,
            external_order_key="25430831",
            invoice_number="25430831",
            customer_info_json={"name": "Peter Conrey", "firstName": "Peter", "lastName": "Conrey"},
            line_items_json=[{
                "sku": "AMH-CABLE-1",
                "externalProductKey": "94922669",
                "lineItemId": "94922669",
                "title": "Speakon Cable",
                "_raw": {"product_id": "94922669", "sku": "AMH-CABLE-1"},
            }],
            status="paid",
            environment="production",
        )
        raw = {
            "_links": {"self": {"href": "https://api.reverb.com/api/my/conversations/24499999"}},
            "other_party": {"name": "Peter Conrey", "uuid": "abc-123"},
            "listing": {
                "id": 94922669,
                "sku": "AMH-CABLE-1",
                "title": "Speakon Cable",
            },
            "read": True,
            "messages": [
                {
                    "id": "m1",
                    "body": "Where is my order?",
                    "created_at": "2026-04-14T10:00:00Z",
                    "authored": False,
                    "author": {"name": "Peter Conrey"},
                },
                {
                    "id": "m2",
                    "body": "Shipped today",
                    "created_at": "2026-04-14T11:00:00Z",
                    "authored": True,
                    "author": {"name": "Shop"},
                },
            ],
        }
        ticket = reverb_tickets.upsert_conversation(self.user, self.store, raw)
        self.assertIsNotNone(ticket)
        self.assertEqual(ticket.external_ticket_key, "24499999")
        self.assertEqual(ticket.customer_name, "Peter Conrey")
        self.assertEqual(ticket.related_order_key, "25430831")
        outbound = ticket.messages.filter(direction=TicketMessageDirection.OUTBOUND).first()
        self.assertEqual(outbound.body, "Shipped today")

    @patch("listings.reverb.tickets.get_adapter")
    def test_fetch_reverb_conversations(self, mock_get_adapter):
        adapter = MagicMock()
        adapter.iter_conversations.return_value = iter([
            {"id": "c1", "subject": "Hello", "other_user": {"name": "Ann"}},
        ])
        adapter.get_conversation.return_value = {
            "id": "c1",
            "subject": "Hello",
            "other_user": {"name": "Ann"},
            "messages": [
                {
                    "id": "msg1",
                    "body": "Hi there",
                    "created_at": "2026-07-14T12:00:00Z",
                    "from_me": False,
                }
            ],
        }
        mock_get_adapter.return_value = adapter

        result = ticket_service.fetch(self.user, self.store)
        self.assertTrue(result["ok"])
        self.assertTrue(result["marketplace_ok"])
        self.assertEqual(result["fetched"], 1)
        ticket = SupportTicket.objects.get(external_ticket_key="c1")
        self.assertEqual(ticket.subject, "Hello")
        self.assertEqual(ticket.messages.count(), 1)

    @patch("listings.reverb.tickets.get_adapter")
    def test_reply_reverb_posts_message(self, mock_get_adapter):
        from listings.reverb import tickets as reverb_tickets

        ticket = reverb_tickets.create_test_ticket(self.user, self.store)
        ticket_obj = SupportTicket.objects.get(id=ticket["ticket_id"])
        # Use a real-looking external key for the reply path
        ticket_obj.external_ticket_key = "conv-reply-1"
        ticket_obj.save(update_fields=["external_ticket_key"])

        adapter = MagicMock()
        adapter.reply_to_conversation.return_value = {"id": "out-1", "body": "Thanks"}
        adapter.mark_conversation_read.return_value = True
        mock_get_adapter.return_value = adapter

        result = ticket_service.reply(ticket_obj, body="Thanks for your message")
        self.assertTrue(result["ok"])
        self.assertTrue(result["marketplace_ok"])
        adapter.reply_to_conversation.assert_called_once_with(
            "conv-reply-1", "Thanks for your message"
        )
        outbound = ticket_obj.messages.filter(direction=TicketMessageDirection.OUTBOUND).first()
        self.assertIsNotNone(outbound)
        self.assertTrue(outbound.delivered_to_marketplace)
