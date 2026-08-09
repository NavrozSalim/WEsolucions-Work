from django.test import TestCase

from users.email_utils import validate_real_email
from users.permissions_defs import price_for_seats, seat_plan_options


class SeatPricingTests(TestCase):
    def test_free_tier(self):
        self.assertEqual(price_for_seats(5), 0)

    def test_packs(self):
        self.assertEqual(price_for_seats(10), 10)
        self.assertEqual(price_for_seats(15), 20)
        self.assertEqual(price_for_seats(20), 30)

    def test_invalid(self):
        with self.assertRaises(ValueError):
            price_for_seats(7)

    def test_options_include_free_and_paid(self):
        plans = seat_plan_options(15)
        self.assertEqual(plans[0]['seats'], 5)
        self.assertEqual(plans[0]['price_usd'], 0)
        self.assertEqual(plans[1]['price_usd'], 10)
        self.assertEqual(plans[2]['price_usd'], 20)


class EmailValidationTests(TestCase):
    def test_rejects_disposable(self):
        self.assertIsNotNone(validate_real_email('a@mailinator.com'))
        self.assertIsNotNone(validate_real_email('x@example.com'))

    def test_accepts_normal(self):
        self.assertIsNone(validate_real_email('ops@sellerpilothub.com'))
