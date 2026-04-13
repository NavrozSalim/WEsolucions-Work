from django.test import SimpleTestCase

from scrapers.core import parse_price_text, classify_failure


class ScraperParsingTests(SimpleTestCase):
    def test_parse_price_text_extracts_float(self):
        self.assertEqual(parse_price_text("$1,249.99"), 1249.99)
        self.assertEqual(parse_price_text("AUD 15.00"), 15.00)
        self.assertIsNone(parse_price_text("not-a-price"))

    def test_classify_failure_detects_http_and_captcha(self):
        self.assertEqual(classify_failure(404, "", parse_failed=False), "not_found")
        html = "<html><body>Please verify you are human captcha</body></html>"
        self.assertEqual(classify_failure(200, html, parse_failed=False), "captcha")
        self.assertEqual(classify_failure(200, "<html>ok</html>", parse_failed=True), "parse_error")
