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
    _parse_ebay_display_price_text,
    _parse_html_to_result_au,
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
            self.assertEqual(_ebay_bin_hydrate_max_seconds("AU", "https://www.ebay.com/itm/1"), 2.0)

    def test_au_hostname_poll_budget_even_if_region_usa(self):
        with patch.dict(os.environ, {"EBAY_BIN_HYDRATE_MAX_SEC": ""}):
            self.assertEqual(
                _ebay_bin_hydrate_max_seconds("USA", "https://www.ebay.com.au/itm/1"),
                2.0,
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

    def test_us_headline_with_list_price_percent_off(self):
        """US BIN: headline US $173.99; list-price footnote must not become $21 from '21% off'."""
        html = """<html><body>
        <section data-testid="x-item-price">
          <div data-testid="x-price-primary">
            <span class="ux-textspans">US $173.99</span>
          </div>
          <div class="x-price-aux">
            <span class="ux-textspans">List price US $219.99 (21% off)</span>
          </div>
        </section>
        </body></html>"""
        soup = BeautifulSoup(html, "lxml")
        self.assertEqual(EbayParser.extract_price(soup, html), 173.99)

    def test_us_bin_price_nested_primary_span(self):
        """US layout: headline lives in .x-bin-price .x-price-primary > span (DevTools path)."""
        html = """<html><body>
        <div id="mainContent">
          <div>
            <div class="vim x-price-section mar-t-20">
              <div class="vim x-bin-price">
                <div>
                  <div class="x-price-primary">
                    <span>US $173.99</span>
                  </div>
                </div>
              </div>
              <div class="x-price-aux">
                <span class="ux-textspans">List price US $219.99 (21% off)</span>
              </div>
            </div>
          </div>
        </div>
        </body></html>"""
        soup = BeautifulSoup(html, "lxml")
        self.assertEqual(EbayParser.extract_price(soup, html), 173.99)

    def test_au_x_price_primary_us_headline_not_json_min(self):
        """eBay AU: use [data-testid=x-price-primary] span; ignore Approximately AU + JSON."""
        html = """<html><head>
        <link rel="canonical" href="https://www.ebay.com.au/itm/373817490567"/>
        </head><body>
        <script>{"itemId":"373817490567","buyItNowPrice":{"value":"208.79"}}</script>
        <section data-testid="x-item-price">
          <div data-testid="x-price-primary">
            <span>US $191.39</span>
          </div>
          <div class="x-price-aux">
            <span>Approximately AU $267.01</span>
          </div>
        </section>
        </body></html>"""
        soup = BeautifulSoup(html, "lxml")
        self.assertEqual(EbayParser.extract_price(soup, html), 191.39)

    def test_au_coupon_extra_off_not_used_as_price(self):
        """eBay AU promo banner 'Extra AU $100.00 off seller's price with code MAYSS2'
        must NOT lower the headline to $100. Headline AU $1,798.55 wins.
        """
        html = """<html><head>
        <link rel="canonical" href="https://www.ebay.com.au/itm/116494323320"/>
        </head><body>
        <section data-testid="x-item-price">
          <div data-testid="x-price-primary">
            <span class="ux-textspans">AU $1,798.55</span>
          </div>
          <div class="x-buy-it-now__message">
            <span class="ux-textspans">Buy now, pay later available</span>
          </div>
          <div class="x-coupon-pricing">
            <span class="ux-textspans">Extra AU $100.00 off seller's price with code MAYSS2</span>
          </div>
        </section>
        </body></html>"""
        soup = BeautifulSoup(html, "lxml")
        self.assertEqual(EbayParser.extract_price(soup, html), 1798.55)

    def test_au_thousands_separator_headline(self):
        """eBay AU prices over 1,000 keep the comma; parser must return full amount."""
        html = """<html><head>
        <link rel="canonical" href="https://www.ebay.com.au/itm/1"/>
        </head><body>
        <section data-testid="x-item-price">
          <div data-testid="x-price-primary">
            <span class="ux-textspans">AU $1,798.55</span>
          </div>
        </section>
        </body></html>"""
        soup = BeautifulSoup(html, "lxml")
        self.assertEqual(EbayParser.extract_price(soup, html), 1798.55)

    def test_percent_off_only_span_ignored(self):
        html = """<html><body>
        <section data-testid="x-item-price">
          <div data-testid="x-price-primary">
            <span class="ux-textspans">US $173.99</span>
          </div>
          <span class="ux-textspans">21% off</span>
        </section>
        </body></html>"""
        soup = BeautifulSoup(html, "lxml")
        self.assertEqual(EbayParser.extract_price(soup, html), 173.99)


class TestEbayDisplayPriceText(unittest.TestCase):
    def test_rejects_list_price_line(self):
        self.assertIsNone(_parse_ebay_display_price_text("List price US $219.99 (21% off)"))

    def test_rejects_bare_percent_off(self):
        self.assertIsNone(_parse_ebay_display_price_text("21% off"))

    def test_rejects_coupon_banner(self):
        self.assertIsNone(
            _parse_ebay_display_price_text(
                "Extra AU $100.00 off seller's price with code MAYSS2"
            )
        )

    def test_rejects_save_with_code(self):
        self.assertIsNone(
            _parse_ebay_display_price_text("Save AU $50.00 off with code SAVE50")
        )

    def test_parses_au_thousands(self):
        self.assertEqual(_parse_ebay_display_price_text("AU $1,798.55"), 1798.55)

    def test_parses_us_dollar_headline(self):
        self.assertEqual(_parse_ebay_display_price_text("US $173.99"), 173.99)


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


_AU_PRICE_BLOCK = """
<section data-testid="x-item-price">
  <div data-testid="x-price-primary">
    <span class="ux-textspans">AU $50.00</span>
  </div>
</section>
"""


_AU_SHIPPING_BLOCK = """
<div class="ux-labels-values ux-labels-values--shipping">
  <div class="ux-labels-values__values-content">
    <div>AU $12.99 delivery in 2-4 days</div>
    <div>Get it between Tue, 26 May and Thu, 28 May</div>
  </div>
</div>
"""


_AU_FREE_SHIPPING_BLOCK = """
<div class="ux-labels-values ux-labels-values--shipping">
  <div class="ux-labels-values__values-content">
    <div>Free postage</div>
  </div>
</div>
"""


_AU_QTY_BLOCK = """
<div class="x-quantity__availability"><span>More than 10 available</span></div>
"""


_AU_SHIPPING_BLOCK_DOESNT_POST = """
<div class="ux-labels-values ux-labels-values--shipping">
  <div class="ux-labels-values__values-content">
    <div>Item doesn't post to you</div>
    <div>AU $12.99 delivery in 2-4 days</div>
    <div>Get it between Tue, 26 May and Thu, 28 May to 2762</div>
  </div>
</div>
"""


_AU_SHIPPING_JSON_ONLY = """
<script type="application/json">{"shippingCost":{"value":"12.99","currency":"AUD"}}</script>
"""


_AU_SHIPPING_GRAPHQL_SNIPPET = """
<script>,\"converted\":null,\"original\":{\"__typename\":\"Price\",\"amount\":12.99,\"currency\":\"AUD\"}},\"shipToLocations\":[\"AUS\"],\"shippingServiceName\":\"Standard\"</script>
"""


_AU_FREE_DELIVERY_BLOCK = """
<div class="ux-labels-values ux-labels-values--shipping">
  <div class="ux-labels-values__values-content">
    <div>Free delivery in 1-2 days</div>
    <div>Get it between Tue, 26 May and Wed, 27 May to 2762</div>
  </div>
</div>
"""


_AU_PAID_GRAPHQL_599 = """
<script>,\"converted\":null,\"original\":{\"__typename\":\"Price\",\"amount\":5.99,\"currency\":\"AUD\"}},\"shipToLocations\":[\"AUS\"],\"shippingServiceName\":\"Standard\"</script>
"""


def _au_html(*blocks: str) -> str:
    return f"<html><body><h1 class='x-item-title'><span>Test title</span></h1>{''.join(blocks)}</body></html>"


class TestEbayAuTerminalStatus(unittest.TestCase):
    """AU-only: terminal status banner forces price=99.99, stock=0 when scrape misses."""

    def test_ux_message_title_with_no_price_returns_99_99(self):
        html = _au_html(
            "<div class='ux-message__title'><span class='ux-textspans'>This listing was ended by the seller</span></div>"
        )
        result = _parse_html_to_result_au(html, "https://www.ebay.com.au/itm/1")
        self.assertEqual(result, {"price": 99.99, "stock": 0, "title": "Test title"})

    def test_hotness_signal_with_no_price_returns_99_99(self):
        html = _au_html(
            "<div data-testid='ux-hotness-signal-text'><span class='signal--time-sensitive'>Selling fast</span></div>"
        )
        result = _parse_html_to_result_au(html, "https://www.ebay.com.au/itm/1")
        self.assertEqual(result, {"price": 99.99, "stock": 0, "title": "Test title"})

    def test_status_message_with_no_price_returns_99_99(self):
        html = _au_html(
            "<div class='ux-layout-section__textual-display--statusMessage'><span>This listing has ended.</span></div>"
        )
        result = _parse_html_to_result_au(html, "https://www.ebay.com.au/itm/1")
        self.assertEqual(result, {"price": 99.99, "stock": 0, "title": "Test title"})

    def test_page_notice_with_no_price_returns_99_99(self):
        html = _au_html(
            "<div class='page-notice__title'><span>Item not available</span></div>"
        )
        result = _parse_html_to_result_au(html, "https://www.ebay.com.au/itm/1")
        self.assertEqual(result, {"price": 99.99, "stock": 0, "title": "Test title"})

    def test_valid_price_and_stock_without_terminal_selector_unchanged(self):
        html = _au_html(_AU_PRICE_BLOCK, _AU_QTY_BLOCK)
        result = _parse_html_to_result_au(html, "https://www.ebay.com.au/itm/1")
        self.assertEqual(result["price"], 50.0)
        self.assertEqual(result["stock"], 10)

    def test_valid_price_and_stock_with_terminal_selector_does_not_override(self):
        """Both price and stock present: terminal selector must NOT downgrade to 99.99."""
        html = _au_html(
            _AU_PRICE_BLOCK,
            _AU_QTY_BLOCK,
            "<div data-testid='ux-hotness-signal-text'><span class='signal--time-sensitive'>Selling fast</span></div>",
        )
        result = _parse_html_to_result_au(html, "https://www.ebay.com.au/itm/1")
        self.assertEqual(result["price"], 50.0)
        self.assertEqual(result["stock"], 10)

    def test_no_price_no_terminal_returns_none(self):
        html = "<html><body><h1 class='x-item-title'><span>Naked page</span></h1></body></html>"
        self.assertIsNone(_parse_html_to_result_au(html, "https://www.ebay.com.au/itm/1"))


class TestEbayAuShippingAddOn(unittest.TestCase):
    """AU-only: scraped item price gets shipping added when shipping row is visible."""

    def test_paid_shipping_is_added_to_price(self):
        html = _au_html(_AU_PRICE_BLOCK, _AU_QTY_BLOCK, _AU_SHIPPING_BLOCK)
        result = _parse_html_to_result_au(html, "https://www.ebay.com.au/itm/1")
        self.assertEqual(result["price"], 62.99)
        self.assertEqual(result["stock"], 10)

    def test_free_shipping_does_not_change_price(self):
        html = _au_html(_AU_PRICE_BLOCK, _AU_QTY_BLOCK, _AU_FREE_SHIPPING_BLOCK)
        result = _parse_html_to_result_au(html, "https://www.ebay.com.au/itm/1")
        self.assertEqual(result["price"], 50.0)

    def test_missing_shipping_row_leaves_price_unchanged(self):
        html = _au_html(_AU_PRICE_BLOCK, _AU_QTY_BLOCK)
        result = _parse_html_to_result_au(html, "https://www.ebay.com.au/itm/1")
        self.assertEqual(result["price"], 50.0)

    def test_terminal_fallback_ignores_shipping(self):
        """The 99.99 sentinel must not be inflated by shipping."""
        html = _au_html(
            _AU_SHIPPING_BLOCK,
            "<div class='ux-message__title'><span class='ux-textspans'>Out of stock</span></div>",
        )
        result = _parse_html_to_result_au(html, "https://www.ebay.com.au/itm/1")
        self.assertEqual(result, {"price": 99.99, "stock": 0, "title": "Test title"})

    def test_shipping_amount_helper_parses_au_dollar(self):
        soup = BeautifulSoup(_AU_SHIPPING_BLOCK, "lxml")
        self.assertEqual(EbayParser.extract_au_shipping_amount(soup), 12.99)

    def test_shipping_amount_helper_returns_zero_for_free(self):
        soup = BeautifulSoup(_AU_FREE_SHIPPING_BLOCK, "lxml")
        self.assertEqual(EbayParser.extract_au_shipping_amount(soup), 0.0)

    def test_doesnt_post_first_div_still_finds_delivery_in_later_div(self):
        """Production HTML: div[0] is 'Item doesn't post to you', div[1] has postage."""
        html = _au_html(_AU_PRICE_BLOCK, _AU_QTY_BLOCK, _AU_SHIPPING_BLOCK_DOESNT_POST)
        result = _parse_html_to_result_au(html, "https://www.ebay.com.au/itm/1")
        self.assertEqual(result["price"], 62.99)

    def test_shipping_from_embedded_json_when_dom_has_no_amount(self):
        html = _au_html(_AU_PRICE_BLOCK, _AU_QTY_BLOCK, _AU_SHIPPING_JSON_ONLY)
        result = _parse_html_to_result_au(html, "https://www.ebay.com.au/itm/1")
        self.assertEqual(result["price"], 62.99)

    def test_shipping_json_fallback_in_raw_html_only(self):
        html = _au_html(
            _AU_PRICE_BLOCK,
            _AU_QTY_BLOCK,
            '<div class="ux-labels-values ux-labels-values--shipping">'
            '<div class="ux-labels-values__values-content">'
            "<div>Item doesn't post to you</div></div></div>",
            _AU_SHIPPING_JSON_ONLY,
        )
        result = _parse_html_to_result_au(html, "https://www.ebay.com.au/itm/1")
        self.assertEqual(result["price"], 62.99)

    def test_delivery_price_line_regex_in_embedded_html(self):
        html = _au_html(
            _AU_PRICE_BLOCK,
            _AU_QTY_BLOCK,
            '<div class="ux-labels-values ux-labels-values--shipping">'
            '<div>Item doesn\'t post to you</div></div>'
            '<span>AU $12.99 delivery in 2-4 days</span>',
        )
        result = _parse_html_to_result_au(html, "https://www.ebay.com.au/itm/1")
        self.assertEqual(result["price"], 62.99)

    def test_graphql_shipping_price_near_ship_to_locations(self):
        """Production HTTP embed: amount is numeric JSON before shipToLocations."""
        html = _au_html(
            _AU_PRICE_BLOCK,
            _AU_QTY_BLOCK,
            '<div class="ux-labels-values ux-labels-values--shipping">'
            '<div class="ux-labels-values__values-content">'
            "<div>Item doesn't post to you</div></div></div>",
            _AU_SHIPPING_GRAPHQL_SNIPPET,
        )
        result = _parse_html_to_result_au(html, "https://www.ebay.com.au/itm/1")
        self.assertEqual(result["price"], 62.99)

    def test_free_in_values_content_skips_graphql_paid_tier(self):
        """Free in values-content must not add shipping from embedded JSON."""
        html = _au_html(
            _AU_PRICE_BLOCK,
            _AU_QTY_BLOCK,
            _AU_FREE_DELIVERY_BLOCK,
            _AU_PAID_GRAPHQL_599,
        )
        result = _parse_html_to_result_au(html, "https://www.ebay.com.au/itm/1")
        self.assertEqual(result["price"], 50.0)


if __name__ == "__main__":
    unittest.main()
