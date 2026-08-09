from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from users.models import EmailOTP, User


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class SuperUserFlowTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_pricing_seat_math(self):
        r = self.client.get('/api/v1/auth/pricing/')
        self.assertEqual(r.status_code, 200)
        plans = {p['seats']: p['price_usd'] for p in r.data['plans']}
        self.assertEqual(plans[5], 0)
        self.assertEqual(plans[10], 10)
        self.assertEqual(plans[15], 20)

    def test_signup_otp_team_and_login_gates(self):
        email = 'super.owner@gmail.com'
        r = self.client.post(
            '/api/v1/auth/super-user/signup/',
            {
                'email': email,
                'password': 'SecurePass1!',
                'organization_name': 'Acme Sync',
                'seat_limit': 10,
                'first_name': 'Su',
                'last_name': 'Per',
            },
            format='json',
        )
        self.assertEqual(r.status_code, 200, r.data)
        otp = EmailOTP.objects.filter(email=email).order_by('-created_at').first()
        self.assertIsNotNone(otp)

        r = self.client.post(
            '/api/v1/auth/super-user/verify-otp/',
            {'email': email, 'code': otp.code},
            format='json',
        )
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['user']['account_type'], 'super_user')
        token = r.data['access']

        auth = APIClient()
        auth.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        r = auth.post(
            '/api/v1/auth/team/',
            {
                'email': 'member.real@gmail.com',
                'password': 'MemberPass1!',
                'first_name': 'Mem',
                'permissions': {
                    'dashboard': True,
                    'catalog': True,
                    'orders': False,
                    'tickets': False,
                    'stores': False,
                    'team': False,
                },
            },
            format='json',
        )
        self.assertEqual(r.status_code, 201, r.data)

        member = APIClient()
        r = member.post(
            '/api/v1/auth/login/',
            {
                'email': 'member.real@gmail.com',
                'password': 'MemberPass1!',
                'account_type': 'user_account',
            },
            format='json',
        )
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data['user']['permissions']['catalog'])
        self.assertFalse(r.data['user']['permissions']['orders'])

        r = member.post(
            '/api/v1/auth/login/',
            {
                'email': email,
                'password': 'SecurePass1!',
                'account_type': 'user_account',
            },
            format='json',
        )
        self.assertEqual(r.status_code, 403)

        # Disposable email rejected
        r = self.client.post(
            '/api/v1/auth/super-user/signup/',
            {
                'email': 'x@mailinator.com',
                'password': 'SecurePass1!',
                'organization_name': 'Nope',
                'seat_limit': 5,
            },
            format='json',
        )
        self.assertEqual(r.status_code, 400)
        self.assertTrue(User.objects.filter(email=email).exists())
