"""Unit tests for eBay parser helpers (run: python -m unittest scrapers.test_ebay_parser -v)."""
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

from scrapers.core import parse_price_text
from scrapers.ebay_common import (
    EbayParser,
    _ebay_bin_hydrate_max_seconds,
    _effective_ebay_region,
    _normalize_url,
    _strip_price_suffix,
)


class TestNormalizeUrl(unittest.TestCase):
    def test_au_hostname_wins_over_usa_region(self):
        u = "https://www.ebay.com.au/itm/Some-Title/123456789012"
        self.assertEqual(
            _normalize_url(u, "USA"),
            "https://www.ebay.com.au/itm/123456789012",
        )

    def test_au_region_promotes_com_to_au(self):
        u = "https://www.ebay.com/itm/123456789012"
        self.assertEqual(
            _normalize_url(u, "AU"),
            "https://www.ebay.com.au/itm/123456789012",
        )

    def test_au_preserves_query_for_variation(self):
        u = "https://www.ebay.com.au/itm/Some-Title/123456789012?var=440888889999"
        self.assertEqual(
            _normalize_url(u, "USA"),
            "https://www.ebay.com.au/itm/123456789012?var=440888889999",
        )


class TestEffectiveEbayRegion(unittest.TestCase):
    def test_normalized_au_url_forces_au(self):
        self.assertEqual(
            _effective_ebay_region("USA", "https://www.ebay.com.au/itm/1"),
            "AU",
        )


class TestBinHydrateDefaults(unittest.TestCase):
    def test_au_default_poll_budget(self):
        with patch.dict(os.environ, {"EBAY_BIN_HYDRATE_MAX_SEC": ""}):
            self.assertEqual(_ebay_bin_hydrate_max_seconds("AU", "https://www.ebay.com/itm/1"), 6.0)

    def test_au_hostname_poll_budget_even_if_region_usa(self):
        with patch.dict(os.environ, {"EBAY_BIN_HYDRATE_MAX_SEC": ""}):
            self.assertEqual(
                _ebay_bin_hydrate_max_seconds("USA", "https://www.ebay.com.au/itm/1"),
                6.0,
            )

    def test_non_au_default_no_extra_poll(self):
        with patch.dict(os.environ, {"EBAY_BIN_HYDRATE_MAX_SEC": ""}):
            self.assertEqual(
                _ebay_bin_hydrate_max_seconds("USA", "https://www.ebay.com/itm/1"),
                0.0,
            )


class TestEbayBuyNowDisplayPrice(unittest.TestCase):
    """Prefer visible sale price over struck 'was' amount in primary BIN block."""

    def test_was_now_picks_lower_non_strike(self):
        html = """<html><body>
        <section data-testid="x-item-price">
          <div data-testid="x-price-primary">
            <span class="ux-textspans ux-textspans--STRIKETHROUGH">AU $21.60</span>
            <span class="ux-textspans ux-textspans--BOLD">AU $18.00</span>
          </div>
        </section>
        </body></html>"""
        soup = BeautifulSoup(html, "lxml")
        self.assertEqual(EbayParser.extract_price(soup, html), 18.0)

    def test_plain_ux_textspans_au_headline(self):
        html = """<html><body>
        <section data-testid="x-item-price">
          <div data-testid="x-price-primary">
            <span class="ux-textspans">AU $35.00</span>
          </div>
        </section>
        </body></html>"""
        soup = BeautifulSoup(html, "lxml")
        self.assertEqual(EbayParser.extract_price(soup, html), 35.0)

    def test_itemprop_price_does_not_force_auction_skipping_headline(self):
        """Schema.org price exists on many BIN PDPs; listing must stay buy_now."""
        html = """<html><body>
        <section data-testid="x-item-price">
          <div data-testid="x-price-primary">
            <span class="ux-textspans">AU $35.00</span>
          </div>
        </section>
        <meta itemprop="price" content="21.60"/>
        </body></html>"""
        soup = BeautifulSoup(html, "lxml")
        self.assertEqual(EbayParser.detect_listing_type(soup, html), "buy_now")
        self.assertEqual(EbayParser.extract_price(soup, html), 35.0)

    def test_loose_ux_textspans_without_x_price_primary_wrapper(self):
        html = """<html><body>
        <section data-testid="x-item-price">
          <div class="some-price-row">
            <span class="ux-textspans">AU $35.00</span>
          </div>
        </section>
        </body></html>"""
        soup = BeautifulSoup(html, "lxml")
        self.assertEqual(EbayParser.extract_price(soup, html), 35.0)

    def test_skips_sponsored_primary_uses_main_buy_box(self):
        """Sponsored lane can mirror price widgets; prefer main item price."""
        html = """<html><body>
        <div class="str-sponsored">
          <div data-testid="x-price-primary">
            <span class="ux-textspans">AU $56.40</span>
          </div>
        </div>
        <section data-testid="x-item-price">
          <div data-testid="x-price-primary">
            <span class="ux-textspans">AU $47.00</span>
          </div>
        </section>
        </body></html>"""
        soup = BeautifulSoup(html, "lxml")
        self.assertEqual(EbayParser.extract_price(soup, html), 47.0)

    def test_inline_style_strikethrough_skipped(self):
        html = """<html><body>
        <section data-testid="x-item-price">
          <div data-testid="x-price-primary">
            <span class="ux-textspans" style="text-decoration: line-through">AU $56.40</span>
            <span class="ux-textspans">AU $47.00</span>
          </div>
        </section>
        </body></html>"""
        soup = BeautifulSoup(html, "lxml")
        self.assertEqual(EbayParser.extract_price(soup, html), 47.0)

    def test_multiple_x_price_primary_min_wins(self):
        """eBay can emit more than one x-price-primary; first may show reference, second headline."""
        html = """<html><body>
        <div data-testid="x-price-primary">
          <span class="ux-textspans">AU $8.40</span>
        </div>
        <section data-testid="x-item-price">
          <div data-testid="x-price-primary">
            <span class="ux-textspans">AU $7.00</span>
          </div>
        </section>
        </body></html>"""
        soup = BeautifulSoup(html, "lxml")
        self.assertEqual(EbayParser.extract_price(soup, html), 7.0)

    def test_primary_reference_plus_bin_sale_uses_lower(self):
        """MSRP-style amount in primary; discounted headline in x-bin-price."""
        html = """<html><body>
        <section data-testid="x-item-price">
          <div data-testid="x-price-primary">
            <span class="ux-textspans">AU $8.40</span>
          </div>
          <div data-testid="x-bin-price">
            <span class="ux-textspans">AU $7.00</span>
          </div>
        </section>
        </body></html>"""
        soup = BeautifulSoup(html, "lxml")
        self.assertEqual(EbayParser.extract_price(soup, html), 7.0)

    def test_sale_price_outside_primary_bin_wrappers(self):
        """Discount line can sit in another row under x-item-price (not x-bin-price)."""
        html = """<html><body>
        <section data-testid="x-item-price">
          <div data-testid="x-price-primary">
            <span class="ux-textspans">AU $8.40</span>
          </div>
          <div class="x-some-promo-row">
            <span class="ux-textspans">AU $7.00</span>
          </div>
        </section>
        </body></html>"""
        soup = BeautifulSoup(html, "lxml")
        self.assertEqual(EbayParser.extract_price(soup, html), 7.0)

    def test_item_model_json_supplements_dom_when_discount_only_in_preload(self):
        """Seller promo BIN is often only in the item model JSON while primary still shows pre-discount."""
        html = """<html><head>
        <link rel="canonical" href="https://www.ebay.com.au/itm/121819241814"/>
        </head><body>
        <script>
        window.__preload = {"itemId":"121819241814","x":{"buyItNowPrice":{"value":"7.00"}}};
        </script>
        <section data-testid="x-item-price">
          <div data-testid="x-price-primary">
            <span class="ux-textspans">AU $8.40</span>
          </div>
        </section>
        </body></html>"""
        soup = BeautifulSoup(html, "lxml")
        self.assertEqual(EbayParser.extract_price(soup, html), 7.0)

    def test_item_model_json_numeric_buy_it_now_without_quotes(self):
        """Preload may serialize ``value`` as a JSON number instead of a string."""
        html = """<html><head>
        <link rel="canonical" href="https://www.ebay.com.au/itm/Slug-Here/121819241814"/>
        </head><body>
        <script>{"itemId":"121819241814","buyItNowPrice":{"value":7}}</script>
        <section data-testid="x-item-price">
          <div data-testid="x-price-primary">
            <span class="ux-textspans">AU $8.40</span>
          </div>
        </section>
        </body></html>"""
        soup = BeautifulSoup(html, "lxml")
        self.assertEqual(EbayParser.extract_price(soup, html), 7.0)

    def test_item_model_json_skips_item_id_before_canonical(self):
        """Use the first ``itemId`` at/after the canonical URL, not an earlier unrelated blob."""
        html = """<html><head>
        <script type="text/javascript">{"itemId":"121819241814","buyItNowPrice":{"value":"99.00"}}</script>
        <link rel="canonical" href="https://www.ebay.com.au/itm/121819241814"/>
        </head><body>
        <script>{"itemId":"121819241814","buyItNowPrice":{"value":"7.00"}}</script>
        <section data-testid="x-item-price">
          <div data-testid="x-price-primary">
            <span class="ux-textspans">AU $8.40</span>
          </div>
        </section>
        </body></html>"""
        soup = BeautifulSoup(html, "lxml")
        self.assertEqual(EbayParser.extract_price(soup, html), 7.0)

    def test_afterpay_installment_amount_skipped(self):
        """BNPL per-payment amounts must not become the headline BIN."""
        html = """<html><head>
        <link rel="canonical" href="https://www.ebay.com.au/itm/999888777666"/>
        </head><body>
        <section data-testid="x-item-price">
          <div data-testid="x-price-primary">
            <span class="ux-textspans">AU $19.00</span>
          </div>
          <div data-testid="x-afterpay-message">
            <span class="ux-textspans">AU $4.75</span>
          </div>
        </section>
        </body></html>"""
        soup = BeautifulSoup(html, "lxml")
        self.assertEqual(EbayParser.extract_price(soup, html), 19.0)


class TestEbayPriceSuffix(unittest.TestCase):
    def test_strip_buy_it_now(self):
        raw = "US $19.99Buy It Now"
        cleaned = _strip_price_suffix(raw)
        self.assertEqual(parse_price_text(cleaned), 19.99)

    def test_strip_best_offer(self):
        raw = "AU $12.50 or Best Offer"
        cleaned = _strip_price_suffix(raw)
        self.assertEqual(parse_price_text(cleaned), 12.5)


class TestEbayDebugSnapshot(unittest.TestCase):
    def test_madrona_price_in_saved_html(self):
        p = Path(__file__).resolve().parent / "debug_html" / "ebay_no_price_20260327_215350_406073482378.html"
        if not p.is_file():
            self.skipTest("debug HTML snapshot not present")
        html = p.read_text(encoding="utf-8", errors="replace")
        soup = BeautifulSoup(html, "lxml")
        self.assertEqual(EbayParser.extract_price(soup, html), 34.69)


if __name__ == "__main__":
    unittest.main()
