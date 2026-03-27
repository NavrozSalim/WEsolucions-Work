from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

User = get_user_model()


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Accept email + password for login (frontend sends email, not username)."""
    def validate(self, attrs):
        # Frontend sends "email"; parent serializer only has "username", so get email from raw request
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
    class Meta:
        model = User
        fields = ('id', 'email', 'password', 'first_name', 'last_name', 'created_at')
        extra_kwargs = {
            'password': {'write_only': True},
            'id': {'read_only': True},
            'created_at': {'read_only': True}
        }
        
    def create(self, validated_data):
        username = validated_data.get('email') # Use email as username
        validated_data['username'] = username
        validated_data['password'] = make_password(validated_data['password'])
        return super().create(validated_data)
