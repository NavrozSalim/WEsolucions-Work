from django.core.cache import cache
from django.test import RequestFactory, SimpleTestCase

from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from core.throttles import FailedLoginThrottleMixin, IngestRateThrottle, LoginRateThrottle


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


class _TwoFailuresPerMinute(LoginRateThrottle):
    rate = '2/minute'


class _FakeLoginView(FailedLoginThrottleMixin, APIView):
    permission_classes = (AllowAny,)
    authentication_classes = ()
    throttle_classes = [_TwoFailuresPerMinute]

    def post(self, request):
        if request.data.get('password') == 'right':
            return Response({'ok': True})
        return Response({'detail': 'bad'}, status=401)


class FailedLoginThrottleTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.factory = APIRequestFactory()
        self.view = _FakeLoginView.as_view()

    def tearDown(self):
        cache.clear()

    def _post(self, password, ip='10.0.0.8'):
        request = self.factory.post(
            '/login/', {'password': password}, format='json', REMOTE_ADDR=ip,
        )
        return self.view(request).status_code

    def test_successful_logins_are_not_counted(self):
        for _ in range(10):
            self.assertEqual(self._post('right'), 200)

    def test_failed_logins_are_throttled_after_limit(self):
        self.assertEqual(self._post('wrong'), 401)
        self.assertEqual(self._post('wrong'), 401)
        self.assertEqual(self._post('right'), 429)

    def test_throttled_responses_do_not_extend_the_block(self):
        self._post('wrong')
        self._post('wrong')
        self.assertEqual(self._post('wrong'), 429)
        key = _TwoFailuresPerMinute().cache_format % {
            'scope': 'login', 'ident': '10.0.0.8',
        }
        self.assertEqual(len(cache.get(key)), 2)

    def test_other_ip_is_not_affected(self):
        self._post('wrong')
        self._post('wrong')
        self.assertEqual(self._post('right', ip='10.0.0.9'), 200)
