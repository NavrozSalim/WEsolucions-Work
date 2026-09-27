"""Price and stock bands are loaded once and still applied per SKU."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from marketplace.models import Marketplace
from stores.models import (
    Store,
    StoreInventoryRangeMultiplier,
    StorePriceRange,
    StorePriceRangeMargin,
    StoreVendorInventorySettings,
    StoreVendorPriceSettings,
)
from sync.tasks import (
    _apply_inventory,
    _apply_pricing,
    _build_store_vendor_pricing_inventory_caches,
    _get_inventory_for_vendor_from_cache,
    _get_pricing_for_vendor_from_cache,
)
from vendor.models import Vendor

User = get_user_model()


class PricingRangeCacheTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='range_cache', email='range_cache@example.com', password='pass12345',
        )
        self.marketplace = Marketplace.objects.create(code='kogan_ranges', name='Kogan Ranges')
        self.store = Store.objects.create(
            user=self.user,
            name='Range Store',
            region='AU',
            api_token='tok-ranges',
            marketplace=self.marketplace,
        )
        self.costway = Vendor.objects.create(code='costway_ranges', name='Costway Ranges')
        self.vevor = Vendor.objects.create(code='vevor_ranges', name='Vevor Ranges')
        self.low = StorePriceRange.objects.create(from_value=Decimal('0'), to_value=Decimal('15'))
        self.high = StorePriceRange.objects.create(from_value=Decimal('15'), to_value=Decimal('50'))

        self.costway_price = self._price_settings(self.costway, low_margin='10', high_margin='20')
        self.vevor_price = self._price_settings(self.vevor, low_margin='50', high_margin='50')
        self.costway_inv = self._inventory_settings(self.costway, low_multiplier='2', high_fixed=4)
        self.vevor_inv = self._inventory_settings(self.vevor, low_multiplier='1', high_fixed=9)

    def _price_settings(self, vendor, *, low_margin, high_margin):
        settings_row = StoreVendorPriceSettings.objects.create(
            store=self.store,
            vendor=vendor,
            purchase_tax_percentage=Decimal('0'),
            marketplace_fees_percentage=Decimal('0'),
            rounding_option='none',
        )
        StorePriceRangeMargin.objects.create(
            price_settings=settings_row,
            price_range=self.low,
            margin_type='percentage',
            margin_percentage=Decimal(low_margin),
        )
        StorePriceRangeMargin.objects.create(
            price_settings=settings_row,
            price_range=self.high,
            margin_type='percentage',
            margin_percentage=Decimal(high_margin),
        )
        return settings_row

    def _inventory_settings(self, vendor, *, low_multiplier, high_fixed):
        settings_row = StoreVendorInventorySettings.objects.create(
            store=self.store,
            vendor=vendor,
            zero_if_low=True,
        )
        StoreInventoryRangeMultiplier.objects.create(
            inventory_settings=settings_row,
            from_value=Decimal('0'),
            to_value=Decimal('5'),
            range_type='multiplier',
            multiplier=Decimal(low_multiplier),
        )
        StoreInventoryRangeMultiplier.objects.create(
            inventory_settings=settings_row,
            from_value=Decimal('6'),
            to_value=Decimal('100'),
            range_type='fixed',
            fixed_value=high_fixed,
        )
        return settings_row

    def test_cached_ranges_match_per_sku_and_do_not_query(self):
        price_by_vid, price_fb, inv_by_vid, inv_fb = (
            _build_store_vendor_pricing_inventory_caches(self.store)
        )
        costway_price = _get_pricing_for_vendor_from_cache(
            self.costway.id, price_by_vid, price_fb,
        )
        vevor_price = _get_pricing_for_vendor_from_cache(
            self.vevor.id, price_by_vid, price_fb,
        )
        costway_inv = _get_inventory_for_vendor_from_cache(
            self.costway.id, inv_by_vid, inv_fb,
        )
        vevor_inv = _get_inventory_for_vendor_from_cache(
            self.vevor.id, inv_by_vid, inv_fb,
        )

        with self.assertNumQueries(0):
            low_cost = _apply_pricing(Decimal('12'), costway_price)
            boundary = _apply_pricing(Decimal('15'), costway_price)
            high_cost = _apply_pricing(Decimal('40'), costway_price)
            other_vendor = _apply_pricing(Decimal('12'), vevor_price)
            low_stock = _apply_inventory(3, costway_inv)
            band_edge = _apply_inventory(5, costway_inv)
            high_stock = _apply_inventory(10, costway_inv)
            other_stock = _apply_inventory(10, vevor_inv)

        # 12 is in 0–15 at 10%. 15 starts the 20% band. 40 stays in that band.
        self.assertEqual(low_cost, Decimal('13.33'))
        self.assertEqual(boundary, Decimal('18.75'))
        self.assertEqual(high_cost, Decimal('50.00'))
        # Same $12 cost, different vendor rule.
        self.assertEqual(other_vendor, Decimal('24.00'))
        self.assertEqual(low_stock, 6)
        self.assertEqual(band_edge, 10)
        self.assertEqual(high_stock, 4)
        self.assertEqual(other_stock, 9)

        fresh_price = StoreVendorPriceSettings.objects.get(pk=self.costway_price.pk)
        fresh_inv = StoreVendorInventorySettings.objects.get(pk=self.costway_inv.pk)
        self.assertEqual(_apply_pricing(Decimal('12'), fresh_price), low_cost)
        self.assertEqual(_apply_pricing(Decimal('15'), fresh_price), boundary)
        self.assertEqual(_apply_pricing(Decimal('40'), fresh_price), high_cost)
        self.assertEqual(_apply_inventory(3, fresh_inv), low_stock)
        self.assertEqual(_apply_inventory(10, fresh_inv), high_stock)
