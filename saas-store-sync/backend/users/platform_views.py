"""Platform master-admin APIs: account, organization, and audit oversight."""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import AuditLog
from users.login_tracking import client_ip, summarize_user_agent
from users.models import LoginEvent, Organization

User = get_user_model()


class IsPlatformAdmin(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_active
            and request.user.is_staff
        )


def _parse_bool(value, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def _pagination_params(request, *, default_limit: int = 50) -> tuple[int, int]:
    try:
        limit = int(request.query_params.get('limit') or default_limit)
    except ValueError:
        limit = default_limit
    try:
        offset = int(request.query_params.get('offset') or 0)
    except ValueError:
        offset = 0
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    return limit, offset


def _serialize_login_event(event: LoginEvent) -> dict:
    ua = summarize_user_agent(event.user_agent or '')
    return {
        'id': str(event.id),
        'ip_address': event.ip_address,
        'user_agent': event.user_agent,
        'success': event.success,
        'account_type': event.account_type,
        'created_at': event.created_at.isoformat(),
        'client': ua,
    }


def _latest_success_event(user: User) -> LoginEvent | None:
    return (
        LoginEvent.objects.filter(user=user, success=True)
        .order_by('-created_at')
        .first()
    )


def _serialize_user(user: User) -> dict:
    org = user.organization
    if not org and user.account_type == User.AccountType.SUPER_USER:
        org = getattr(user, 'owned_organization', None)
    latest = _latest_success_event(user)
    return {
        'id': str(user.id),
        'email': user.email,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'account_type': user.account_type,
        'is_staff': user.is_staff,
        'is_active': user.is_active,
        'email_verified': user.email_verified,
        'last_login': user.last_login.isoformat() if user.last_login else None,
        'created_at': user.created_at.isoformat() if user.created_at else None,
        'organization': (
            {
                'id': str(org.id),
                'name': org.name,
                'seat_limit': org.seat_limit,
                'occupied_seats': org.occupied_seats(),
                'seats_remaining': org.seats_remaining(),
            }
            if org
            else None
        ),
        'latest_login': _serialize_login_event(latest) if latest else None,
    }


def _log_platform_action(request, action: str, target_user: User, *, metadata: dict | None = None) -> None:
    AuditLog.objects.create(
        user=request.user if request.user.is_authenticated else None,
        action=action,
        object_type='user',
        object_id=str(target_user.id),
        metadata={
            'target_email': target_user.email,
            'target_account_type': target_user.account_type,
            **(metadata or {}),
        },
        ip_address=client_ip(request),
    )


class PlatformStatsView(APIView):
    permission_classes = (IsAuthenticated, IsPlatformAdmin)

    def get(self, request):
        users = User.objects.filter(is_staff=False)
        now = timezone.now()
        return Response({
            'super_users': users.filter(account_type=User.AccountType.SUPER_USER).count(),
            'user_accounts': users.filter(account_type=User.AccountType.USER_ACCOUNT).count(),
            'standalone': users.filter(account_type=User.AccountType.STANDALONE).count(),
            'active_users': users.filter(is_active=True).count(),
            'inactive_users': users.filter(is_active=False).count(),
            'organizations': Organization.objects.count(),
            'recent_logins_24h': LoginEvent.objects.filter(
                success=True,
                created_at__gte=now - timedelta(hours=24),
            ).count(),
            'failed_logins_24h': LoginEvent.objects.filter(
                success=False,
                created_at__gte=now - timedelta(hours=24),
            ).count(),
        })


class PlatformUserListView(APIView):
    permission_classes = (IsAuthenticated, IsPlatformAdmin)

    def get(self, request):
        account_type = (request.query_params.get('account_type') or '').strip()
        search = (request.query_params.get('q') or '').strip()
        include_staff = _parse_bool(request.query_params.get('include_staff'))
        include_inactive = _parse_bool(request.query_params.get('include_inactive'), default=True)
        limit, offset = _pagination_params(request)

        qs = User.objects.select_related('organization').order_by('-created_at')
        if not include_staff:
            qs = qs.filter(is_staff=False)
        if not include_inactive:
            qs = qs.filter(is_active=True)
        if account_type:
            qs = qs.filter(account_type=account_type)
        if search:
            qs = qs.filter(
                Q(email__icontains=search)
                | Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
                | Q(organization__name__icontains=search)
            )

        total = qs.count()
        rows = list(qs[offset:offset + limit])
        next_offset = offset + limit if (offset + limit) < total else None

        return Response({
            'users': [_serialize_user(u) for u in rows],
            'counts': list(qs.values('account_type').annotate(total=Count('id')).order_by()),
            'total': total,
            'limit': limit,
            'offset': offset,
            'next_offset': next_offset,
        })


class PlatformOrganizationListView(APIView):
    permission_classes = (IsAuthenticated, IsPlatformAdmin)

    def get(self, request):
        search = (request.query_params.get('q') or '').strip()
        limit, offset = _pagination_params(request, default_limit=30)

        org_qs = Organization.objects.select_related('owner').annotate(
            user_accounts=Count(
                'users',
                filter=Q(users__account_type=User.AccountType.USER_ACCOUNT),
                distinct=True,
            ),
            active_users=Count(
                'users',
                filter=Q(users__is_active=True),
                distinct=True,
            ),
            inactive_users=Count(
                'users',
                filter=Q(users__is_active=False),
                distinct=True,
            ),
        ).order_by('-created_at')
        if search:
            org_qs = org_qs.filter(
                Q(name__icontains=search)
                | Q(owner__email__icontains=search)
            )

        total = org_qs.count()
        orgs = list(org_qs[offset:offset + limit])
        next_offset = offset + limit if (offset + limit) < total else None

        owner_ids = [org.owner_id for org in orgs if org.owner_id]
        latest_owner_event = {}
        for event in LoginEvent.objects.filter(user_id__in=owner_ids, success=True).order_by('user_id', '-created_at'):
            if event.user_id not in latest_owner_event:
                latest_owner_event[event.user_id] = event

        payload = []
        for org in orgs:
            owner_event = latest_owner_event.get(org.owner_id)
            payload.append({
                'id': str(org.id),
                'name': org.name,
                'seat_limit': org.seat_limit,
                'plan_price_usd': org.plan_price_usd,
                'occupied_seats': org.occupied_seats(),
                'seats_remaining': org.seats_remaining(),
                'user_accounts': org.user_accounts,
                'active_users': org.active_users,
                'inactive_users': org.inactive_users,
                'owner': {
                    'id': str(org.owner_id),
                    'email': org.owner.email,
                    'first_name': org.owner.first_name,
                    'last_name': org.owner.last_name,
                    'is_active': org.owner.is_active,
                },
                'owner_latest_login': _serialize_login_event(owner_event) if owner_event else None,
                'created_at': org.created_at.isoformat(),
            })

        return Response({
            'organizations': payload,
            'total': total,
            'limit': limit,
            'offset': offset,
            'next_offset': next_offset,
        })


class PlatformAuditListView(APIView):
    permission_classes = (IsAuthenticated, IsPlatformAdmin)

    def get(self, request):
        limit, offset = _pagination_params(request, default_limit=50)
        qs = AuditLog.objects.select_related('user').filter(action__startswith='platform_').order_by('-timestamp')
        total = qs.count()
        rows = list(qs[offset:offset + limit])
        next_offset = offset + limit if (offset + limit) < total else None
        return Response({
            'events': [
                {
                    'id': str(row.id),
                    'action': row.action,
                    'object_type': row.object_type,
                    'object_id': row.object_id,
                    'timestamp': row.timestamp.isoformat(),
                    'actor_email': row.user.email if row.user else '',
                    'ip_address': row.ip_address,
                    'metadata': row.metadata or {},
                }
                for row in rows
            ],
            'total': total,
            'limit': limit,
            'offset': offset,
            'next_offset': next_offset,
        })


class PlatformUserDetailView(APIView):
    permission_classes = (IsAuthenticated, IsPlatformAdmin)

    def get(self, request, user_id):
        user = User.objects.filter(id=user_id).select_related('organization').first()
        if not user:
            return Response({'detail': 'User not found.'}, status=status.HTTP_404_NOT_FOUND)
        events = LoginEvent.objects.filter(user=user).order_by('-created_at')[:50]
        return Response({
            'user': _serialize_user(user),
            'login_events': [_serialize_login_event(e) for e in events],
        })

    def patch(self, request, user_id):
        user = User.objects.filter(id=user_id).first()
        if not user:
            return Response({'detail': 'User not found.'}, status=status.HTTP_404_NOT_FOUND)
        if user.id == request.user.id:
            return Response(
                {'detail': 'You cannot change your own platform admin status here.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if user.is_staff:
            return Response(
                {'detail': 'Cannot modify another platform admin from here.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if 'is_active' not in request.data:
            return Response(
                {'detail': 'Provide is_active as true or false.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        is_active = _parse_bool(request.data.get('is_active'))
        user.is_active = is_active
        user.save(update_fields=['is_active'])
        _log_platform_action(
            request,
            'platform_user_reactivated' if is_active else 'platform_user_deactivated',
            user,
        )
        return Response({
            'detail': f'{"Reactivated" if is_active else "Deactivated"} {user.email}.',
            'user': _serialize_user(user),
        })

    def delete(self, request, user_id):
        user = User.objects.filter(id=user_id).first()
        if not user:
            return Response({'detail': 'User not found.'}, status=status.HTTP_404_NOT_FOUND)
        if user.id == request.user.id:
            return Response(
                {'detail': 'You cannot remove your own master admin account.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if user.is_staff:
            return Response(
                {'detail': 'Cannot remove another platform admin from here.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        hard = _parse_bool(request.query_params.get('hard'))
        email = user.email
        if hard:
            # Deleting an org owner cascades the organization.
            _log_platform_action(request, 'platform_user_deleted', user, metadata={'hard_delete': True})
            user.delete()
            return Response({'detail': f'Permanently deleted {email}.'}, status=status.HTTP_200_OK)

        if not user.is_active:
            return Response({'detail': f'{email} is already deactivated.', 'user': _serialize_user(user)})

        user.is_active = False
        user.save(update_fields=['is_active'])
        _log_platform_action(request, 'platform_user_deactivated', user, metadata={'hard_delete': False})
        return Response({'detail': f'Deactivated {email}.', 'user': _serialize_user(user)})
