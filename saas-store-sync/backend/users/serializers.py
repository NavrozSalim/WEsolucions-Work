from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from users.permissions_defs import PERMISSION_KEYS, normalize_permissions

User = get_user_model()


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Accept email + password for login (frontend sends email, not username)."""

    def validate(self, attrs):
        email = self.initial_data.get('email') or attrs.get('username')
        password = attrs.get('password')
        if not email or not password:
            raise serializers.ValidationError('Must include "email" and "password".')
        user = User.objects.filter(email__iexact=email).first()
        if not user or not user.check_password(password):
            raise serializers.ValidationError('Invalid email or password.')
        refresh = self.get_token(user)
        return {
            'refresh': str(refresh),
            'access': str(refresh.access_token),
            'user': {'id': str(user.id), 'email': user.email},
        }


class UserSerializer(serializers.ModelSerializer):
    account_type = serializers.CharField(read_only=True)
    email_verified = serializers.BooleanField(read_only=True)
    is_platform_admin = serializers.SerializerMethodField()
    permissions = serializers.SerializerMethodField()
    organization = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            'id',
            'email',
            'password',
            'first_name',
            'last_name',
            'created_at',
            'account_type',
            'email_verified',
            'is_platform_admin',
            'permissions',
            'organization',
        )
        extra_kwargs = {
            'password': {'write_only': True},
            'id': {'read_only': True},
            'created_at': {'read_only': True},
        }

    def get_is_platform_admin(self, obj):
        return bool(obj.is_staff)

    def get_permissions(self, obj):
        return obj.effective_permissions()

    def get_organization(self, obj):
        org = obj.organization
        if not org and obj.account_type == User.AccountType.SUPER_USER:
            org = getattr(obj, 'owned_organization', None)
        if not org:
            return None
        return {
            'id': str(org.id),
            'name': org.name,
            'seat_limit': org.seat_limit,
            'plan_price_usd': org.plan_price_usd,
            'occupied_seats': org.occupied_seats(),
            'seats_remaining': org.seats_remaining(),
            'is_owner': org.owner_id == obj.id,
        }

    def create(self, validated_data):
        username = validated_data.get('email')
        validated_data['username'] = username
        validated_data['password'] = make_password(validated_data['password'])
        return super().create(validated_data)


class SuperUserSignupSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(min_length=8, write_only=True)
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default='')
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default='')
    organization_name = serializers.CharField(max_length=200)
    seat_limit = serializers.IntegerField(min_value=5)


class VerifyOTPSerializer(serializers.Serializer):
    email = serializers.EmailField()
    code = serializers.CharField(min_length=6, max_length=6)


class MemberCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(min_length=8, write_only=True)
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default='')
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default='')
    permissions = serializers.DictField(child=serializers.BooleanField(), required=False)

    def validate_permissions(self, value):
        return normalize_permissions(value or {})


class MemberUpdateSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=150, required=False)
    last_name = serializers.CharField(max_length=150, required=False)
    permissions = serializers.DictField(child=serializers.BooleanField(), required=False)
    is_active = serializers.BooleanField(required=False)
    password = serializers.CharField(min_length=8, write_only=True, required=False)

    def validate_permissions(self, value):
        unknown = set(value.keys()) - set(PERMISSION_KEYS)
        if unknown:
            raise serializers.ValidationError(f'Unknown permission keys: {sorted(unknown)}')
        return normalize_permissions(value)


class MemberSerializer(serializers.ModelSerializer):
    permissions = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            'id',
            'email',
            'first_name',
            'last_name',
            'account_type',
            'email_verified',
            'is_active',
            'created_at',
            'permissions',
        )

    def get_permissions(self, obj):
        return obj.effective_permissions()
