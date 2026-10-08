from django.core.cache import cache
from django.test import RequestFactory, SimpleTestCase

from core.throttles import IngestRateThrottle


class IngestRateThrottleKeyTests(SimpleTestCase):
    def test_key_uses_token_hash_and_peer_address_not_forwarded_header(self):
        throttle = IngestRateThrottle()
        factory = RequestFactory()
        same = factory.get(
            '/',
            HTTP_AUTHORIZATION='Bearer secret-token',
            REMOTE_ADDR='10.0.0.8',
            HTTP_X_FORWARDED_FOR='1.2.3.4',
        )
        forwarded_changed = factory.get(
            '/',
            HTTP_AUTHORIZATION='Bearer secret-token',
            REMOTE_ADDR='10.0.0.8',
            HTTP_X_FORWARDED_FOR='9.9.9.9',
        )
        other_token = factory.get(
            '/',
            HTTP_AUTHORIZATION='Bearer other-token',
            REMOTE_ADDR='10.0.0.8',
        )
        self.assertEqual(
            throttle.get_cache_key(same, None),
            throttle.get_cache_key(forwarded_changed, None),
        )
        self.assertNotEqual(
            throttle.get_cache_key(same, None),
            throttle.get_cache_key(other_token, None),
        )


class _OnePerMinute(IngestRateThrottle):
    rate = '1/minute'


class IngestRateThrottleRequestTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_second_request_with_same_token_is_throttled(self):
        factory = RequestFactory()
        request = factory.get(
            '/',
            HTTP_AUTHORIZATION='Bearer secret-token',
            REMOTE_ADDR='10.0.0.8',
        )
        first = _OnePerMinute()
        second = _OnePerMinute()
        self.assertTrue(first.allow_request(request, None))
        self.assertFalse(second.allow_request(request, None))
