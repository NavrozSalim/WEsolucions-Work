import tempfile
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

from django.test import SimpleTestCase

from scrapers import core
from sync.tasks import _inventory_from_scrape_result, resolve_vendor_scrape_url


class ScraperDebugArtifactTests(SimpleTestCase):
    def test_save_debug_html_truncates_large_payload(self):
        large_html = "<html>" + ("A" * (core.MAX_DEBUG_HTML_BYTES + 5000)) + "</html>"
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(core, "DEBUG_HTML_DIR", tmpdir):
                ok = core.save_debug_html(large_html, "amazon_us", "https://example.com/item/1", "captcha")
                self.assertTrue(ok)
                html_files = list(Path(tmpdir).glob("*.html"))
                self.assertEqual(len(html_files), 1)
                content = html_files[0].read_text(encoding="utf-8", errors="replace")
                self.assertLessEqual(len(content.encode("utf-8")), core.MAX_DEBUG_HTML_BYTES + 2000)


class VendorScrapeUrlTests(SimpleTestCase):
    def test_inventory_prefers_inventory_key(self):
        self.assertEqual(_inventory_from_scrape_result({'inventory': 3, 'stock': 9}), 3)
        self.assertEqual(_inventory_from_scrape_result({'stock': 2}), 2)
        self.assertIsNone(_inventory_from_scrape_result(None))

    def test_resolve_uses_catalog_vendor_id_when_url_empty(self):
        row = MagicMock()
        row.vendor_url_raw = ''
        row.vendor_id_raw = 'B0ABC1234'
        v = MagicMock()
        v.code = 'amazon'
        p = MagicMock()
        p.vendor = v
        p.vendor_url = ''
        p.vendor_sku = 'LISTING-SKU-ONLY'
        st = MagicMock()
        st.region = 'USA'
        url = resolve_vendor_scrape_url(p, st, row)
        self.assertIn('amazon.com/dp/B0ABC1234', url)
