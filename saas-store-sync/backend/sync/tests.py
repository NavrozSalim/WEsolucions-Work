import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from scrapers import core


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
