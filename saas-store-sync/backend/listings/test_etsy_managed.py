"""Tests for Etsy managed listing helpers."""
from __future__ import annotations

from django.test import SimpleTestCase

from listings.etsy import listings as etsy_listings


class EtsyListingHelpersTests(SimpleTestCase):
    def test_validate_requires_core_fields(self):
        errs = etsy_listings.validate_listing({})
        self.assertTrue(any('SKU' in e for e in errs))
        self.assertTrue(any('Title' in e for e in errs))
        self.assertTrue(any('Taxonomy' in e for e in errs))

    def test_build_create_form(self):
        form = etsy_listings.build_create_form(
            {
                'title': 'Pendant',
                'description': 'Nice',
                'sale_price': '12.50',
                'inventory': 2,
                'taxonomy_id': '123',
                'who_made': 'i_did',
                'when_made': 'made_to_order',
            },
            shipping_profile_id='99',
            readiness_state_id='55',
        )
        self.assertEqual(form['taxonomy_id'], 123)
        self.assertEqual(form['shipping_profile_id'], 99)
        self.assertEqual(form['price'], 12.5)

    def test_looks_like_etsy_listing_id(self):
        self.assertTrue(etsy_listings.looks_like_etsy_listing_id('192837465'))
        self.assertFalse(etsy_listings.looks_like_etsy_listing_id('SKU-1'))
