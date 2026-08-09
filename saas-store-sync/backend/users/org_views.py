"""Super User signup, pricing, and team management APIs."""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.utils import timezone
from datetime import timedelta
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from core.throttles import LoginRateThrottle, OTPRateThrottle
from users.email_utils import send_otp_email, validate_real_email
from users.models import EmailOTP, Membership, Organization
from users.permissions_defs import (
    ALL_PERMISSIONS,
    DEFAULT_MEMBER_PERMISSIONS,
    PERMISSION_KEYS,
    PERMISSION_LABELS,
    normalize_permissions,
    price_for_seats,
    seat_plan_options,
)
from users.serializers import (
    MemberCreateSerializer,
    MemberSerializer,
    MemberUpdateSerializer,
    SuperUserSignupSerializer,
    UserSerializer,
    VerifyOTPSerializer,
)

User = get_user_model()


def _tokens_for(user):
    refresh = RefreshToken.for_user(user)
    return {
        'refresh': str(refresh),
        'access': str(refresh.access_token),
        'user': UserSerializer(user).data,
    }


def _get_owned_org(user):
    if user.account_type != User.AccountType.SUPER_USER:
        return None
    return getattr(user, 'owned_organization', None) or user.organization


class PricingPlansView(APIView):
    permission_classes = (AllowAny,)

    def get(self, request):
        return Response({
            'plans': seat_plan_options(),
            'permission_keys': [
                {'key': k, 'label': PERMISSION_LABELS[k]} for k in PERMISSION_KEYS
            ],
            'pricing_note': (
                'First 5 user accounts are free. Each additional pack of 5 seats costs $10.'
            ),
        })


class SuperUserSignupStartView(APIView):
    """Start Super User signup: validate, store payload, email OTP."""
    permission_classes = (AllowAny,)
    throttle_classes = [OTPRateThrottle]

    def post(self, request):
        ser = SuperUserSignupSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        email = data['email'].strip().lower()

        err = validate_real_email(email)
        if err:
            return Response({'detail': err}, status=status.HTTP_400_BAD_REQUEST)

        if User.objects.filter(email__iexact=email).exists():
            return Response(
                {'detail': 'An account with this email already exists. Please log in.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            price = price_for_seats(data['seat_limit'])
        except ValueError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        EmailOTP.objects.filter(
            email__iexact=email,
            purpose=EmailOTP.Purpose.SUPER_USER_SIGNUP,
            is_used=False,
        ).update(is_used=True)

        code = EmailOTP.generate_code()
        expiry = timezone.now() + timedelta(minutes=getattr(settings, 'OTP_EXPIRY_MINUTES', 10))
        EmailOTP.objects.create(
            email=email,
            code=code,
            purpose=EmailOTP.Purpose.SUPER_USER_SIGNUP,
            expires_at=expiry,
            payload={
                'password': make_password(data['password']),
                'first_name': data.get('first_name') or '',
                'last_name': data.get('last_name') or '',
                'organization_name': data['organization_name'].strip(),
                'seat_limit': data['seat_limit'],
                'plan_price_usd': price,
            },
        )

        sent = send_otp_email(email, code, purpose='super_user_signup')
        if not sent:
            return Response(
                {
                    'detail': (
                        'Could not send verification email. '
                        'Check Brevo SMTP settings: EMAIL_HOST_USER must be the SMTP Login '
                        '(often xxx@smtp-brevo.com), not your Gmail, and EMAIL_HOST_PASSWORD '
                        'must be the SMTP key.'
                    ),
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            {
                'detail': 'Verification code sent to your email.',
                'email': email,
                'expires_in_minutes': getattr(settings, 'OTP_EXPIRY_MINUTES', 10),
            },
            status=status.HTTP_200_OK,
        )


class SuperUserSignupVerifyView(APIView):
    """Verify OTP and create Super User + Organization."""
    permission_classes = (AllowAny,)
    throttle_classes = [OTPRateThrottle]

    def post(self, request):
        ser = VerifyOTPSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        email = ser.validated_data['email'].strip().lower()
        code = ser.validated_data['code'].strip()

        otp = (
            EmailOTP.objects.filter(
                email__iexact=email,
                purpose=EmailOTP.Purpose.SUPER_USER_SIGNUP,
                is_used=False,
            )
            .order_by('-created_at')
            .first()
        )
        if not otp:
            return Response(
                {'detail': 'No pending verification for this email. Start signup again.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if otp.is_expired():
            otp.mark_used()
            return Response(
                {'detail': 'Verification code expired. Start signup again.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        otp.attempts += 1
        otp.save(update_fields=['attempts'])
        if otp.attempts > 5:
            otp.mark_used()
            return Response(
                {'detail': 'Too many failed attempts. Start signup again.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if otp.code != code:
            return Response(
                {'detail': 'Invalid verification code.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if User.objects.filter(email__iexact=email).exists():
            otp.mark_used()
            return Response(
                {'detail': 'An account with this email already exists.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        payload = otp.payload or {}
        with transaction.atomic():
            user = User(
                username=email,
                email=email,
                first_name=payload.get('first_name') or '',
                last_name=payload.get('last_name') or '',
                account_type=User.AccountType.SUPER_USER,
                email_verified=True,
            )
            user.password = payload['password']
            user.save()

            org = Organization.objects.create(
                name=payload.get('organization_name') or f"{email}'s organization",
                owner=user,
                seat_limit=int(payload.get('seat_limit') or 5),
                plan_price_usd=int(payload.get('plan_price_usd') or 0),
            )
            user.organization = org
            user.save(update_fields=['organization'])

            Membership.objects.create(
                user=user,
                organization=org,
                permissions=dict(ALL_PERMISSIONS),
            )
            otp.mark_used()

        return Response(_tokens_for(user), status=status.HTTP_201_CREATED)


class TypedLoginView(APIView):
    """
    Login with account_type gate:
      - account_type=super_user → only Super Users
      - account_type=user_account → only User Accounts (and standalone for legacy)
    """
    permission_classes = (AllowAny,)
    throttle_classes = [LoginRateThrottle]

    def post(self, request):
        from users.login_tracking import record_login_event

        identifier = (
            request.data.get('email') or request.data.get('username') or ''
        ).strip()
        password = request.data.get('password') or ''
        account_type = (request.data.get('account_type') or '').strip().lower()

        if not identifier or not password:
            record_login_event(
                request,
                success=False,
                account_type=account_type or '',
                email=identifier,
            )
            return Response(
                {'detail': 'Must include email and password.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user = (
            User.objects.filter(email__iexact=identifier).first()
            or User.objects.filter(username__iexact=identifier).first()
        )
        if not user or not user.check_password(password):
            record_login_event(
                request,
                success=False,
                account_type=account_type or '',
                email=identifier,
            )
            return Response(
                {'detail': 'Invalid email or password.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        if not user.is_active:
            record_login_event(
                request,
                user=user,
                success=False,
                account_type=account_type or user.account_type,
            )
            return Response(
                {'detail': 'This account is disabled.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        if account_type == 'platform_admin':
            if not user.is_staff:
                record_login_event(
                    request,
                    user=user,
                    success=False,
                    account_type='platform_admin',
                )
                return Response(
                    {'detail': 'Invalid master admin credentials.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
        elif user.is_staff:
            record_login_event(
                request,
                user=user,
                success=False,
                account_type=account_type or user.account_type,
            )
            return Response(
                {'detail': 'This is a platform admin account. Use the master login instead.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        elif account_type == 'super_user':
            if user.account_type != User.AccountType.SUPER_USER:
                record_login_event(
                    request,
                    user=user,
                    success=False,
                    account_type='super_user',
                )
                return Response(
                    {
                        'detail': (
                            'This login is for Super Users only. '
                            'Use “Login with User Account”, or create a Super User account.'
                        ),
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )
            if not user.email_verified:
                record_login_event(
                    request,
                    user=user,
                    success=False,
                    account_type='super_user',
                )
                return Response(
                    {'detail': 'Please verify your email before logging in as Super User.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
        elif account_type == 'user_account':
            if user.account_type == User.AccountType.SUPER_USER:
                record_login_event(
                    request,
                    user=user,
                    success=False,
                    account_type='user_account',
                )
                return Response(
                    {
                        'detail': (
                            'This is a Super User account. Use “Login as Super User” instead.'
                        ),
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )
            # user_account + standalone (legacy) allowed

        record_login_event(request, user, success=True, account_type=account_type or user.account_type)
        user.last_login = timezone.now()
        user.save(update_fields=['last_login'])

        return Response(_tokens_for(user))


class TeamMemberListCreateView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        org = _get_owned_org(request.user)
        if not org:
            return Response(
                {'detail': 'Only Super Users can manage team members.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        members = User.objects.filter(organization=org).order_by('created_at')
        return Response({
            'organization': {
                'id': str(org.id),
                'name': org.name,
                'seat_limit': org.seat_limit,
                'plan_price_usd': org.plan_price_usd,
                'occupied_seats': org.occupied_seats(),
                'seats_remaining': org.seats_remaining(),
            },
            'members': MemberSerializer(members, many=True).data,
            'permission_keys': [
                {'key': k, 'label': PERMISSION_LABELS[k]} for k in PERMISSION_KEYS
            ],
        })

    def post(self, request):
        org = _get_owned_org(request.user)
        if not org:
            return Response(
                {'detail': 'Only Super Users can create user accounts.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        ser = MemberCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        email = data['email'].strip().lower()

        err = validate_real_email(email)
        if err:
            return Response({'detail': err}, status=status.HTTP_400_BAD_REQUEST)

        if User.objects.filter(email__iexact=email).exists():
            return Response(
                {'detail': 'A user with this email already exists.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if org.seats_remaining() < 1:
            return Response(
                {
                    'detail': (
                        f'Seat limit reached ({org.seat_limit}). '
                        'Upgrade your plan to add more user accounts.'
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        perms = normalize_permissions(data.get('permissions') or DEFAULT_MEMBER_PERMISSIONS)
        # Members cannot manage the team
        perms['team'] = False

        with transaction.atomic():
            user = User.objects.create_user(
                username=email,
                email=email,
                password=data['password'],
                first_name=data.get('first_name') or '',
                last_name=data.get('last_name') or '',
                account_type=User.AccountType.USER_ACCOUNT,
                email_verified=True,
                organization=org,
            )
            Membership.objects.create(
                user=user,
                organization=org,
                permissions=perms,
            )

        return Response(MemberSerializer(user).data, status=status.HTTP_201_CREATED)


class TeamMemberDetailView(APIView):
    permission_classes = (IsAuthenticated,)

    def _get_member(self, request, member_id):
        org = _get_owned_org(request.user)
        if not org:
            return None, Response(
                {'detail': 'Only Super Users can manage team members.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        member = User.objects.filter(organization=org, id=member_id).first()
        if not member:
            return None, Response({'detail': 'Member not found.'}, status=status.HTTP_404_NOT_FOUND)
        return (org, member), None

    def patch(self, request, member_id):
        result, err = self._get_member(request, member_id)
        if err:
            return err
        org, member = result

        if member.id == request.user.id:
            # Super User can update own name/password but not strip own team access
            ser = MemberUpdateSerializer(data=request.data, partial=True)
            ser.is_valid(raise_exception=True)
            data = ser.validated_data
            if 'first_name' in data:
                member.first_name = data['first_name']
            if 'last_name' in data:
                member.last_name = data['last_name']
            if data.get('password'):
                member.set_password(data['password'])
            member.save()
            return Response(MemberSerializer(member).data)

        if member.account_type == User.AccountType.SUPER_USER:
            return Response(
                {'detail': 'Cannot modify another Super User this way.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ser = MemberUpdateSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        if 'first_name' in data:
            member.first_name = data['first_name']
        if 'last_name' in data:
            member.last_name = data['last_name']
        if 'is_active' in data:
            member.is_active = data['is_active']
        if data.get('password'):
            member.set_password(data['password'])
        member.save()

        if 'permissions' in data:
            perms = normalize_permissions(data['permissions'])
            perms['team'] = False
            Membership.objects.update_or_create(
                user=member,
                defaults={'organization': org, 'permissions': perms},
            )

        return Response(MemberSerializer(member).data)

    def delete(self, request, member_id):
        result, err = self._get_member(request, member_id)
        if err:
            return err
        _org, member = result
        if member.id == request.user.id:
            return Response(
                {'detail': 'You cannot delete your own Super User account here.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if member.account_type == User.AccountType.SUPER_USER:
            return Response(
                {'detail': 'Cannot delete a Super User account.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        member.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class OrganizationPlanUpdateView(APIView):
    """Upgrade/downgrade seat plan for the Super User's organization."""
    permission_classes = (IsAuthenticated,)

    def patch(self, request):
        org = _get_owned_org(request.user)
        if not org:
            return Response(
                {'detail': 'Only Super Users can change the plan.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            seat_limit = int(request.data.get('seat_limit'))
            price = price_for_seats(seat_limit)
        except (TypeError, ValueError) as e:
            return Response({'detail': str(e) or 'Invalid seat_limit.'}, status=status.HTTP_400_BAD_REQUEST)

        occupied = org.occupied_seats()
        if seat_limit < occupied:
            return Response(
                {
                    'detail': (
                        f'Cannot set seat limit below current occupied seats ({occupied}). '
                        'Remove user accounts first.'
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        org.seat_limit = seat_limit
        org.plan_price_usd = price
        org.save(update_fields=['seat_limit', 'plan_price_usd', 'updated_at'])
        return Response({
            'id': str(org.id),
            'name': org.name,
            'seat_limit': org.seat_limit,
            'plan_price_usd': org.plan_price_usd,
            'occupied_seats': org.occupied_seats(),
            'seats_remaining': org.seats_remaining(),
        })
