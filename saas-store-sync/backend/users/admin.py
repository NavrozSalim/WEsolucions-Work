from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import EmailOTP, LoginEvent, Membership, Organization, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ('email', 'account_type', 'email_verified', 'organization', 'is_staff', 'is_active')
    list_filter = ('account_type', 'email_verified', 'is_staff', 'is_active')
    search_fields = ('email', 'first_name', 'last_name', 'username')
    ordering = ('email',)
    fieldsets = DjangoUserAdmin.fieldsets + (
        ('SellerPilot Hub', {
            'fields': ('account_type', 'email_verified', 'organization'),
        }),
    )


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'seat_limit', 'plan_price_usd', 'created_at')
    search_fields = ('name', 'owner__email')


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ('user', 'organization', 'updated_at')
    search_fields = ('user__email', 'organization__name')


@admin.register(EmailOTP)
class EmailOTPAdmin(admin.ModelAdmin):
    list_display = ('email', 'purpose', 'is_used', 'attempts', 'expires_at', 'created_at')
    list_filter = ('purpose', 'is_used')
    search_fields = ('email',)


@admin.register(LoginEvent)
class LoginEventAdmin(admin.ModelAdmin):
    list_display = ('email', 'account_type', 'ip_address', 'success', 'created_at')
    list_filter = ('success', 'account_type')
    search_fields = ('email', 'ip_address', 'user_agent')
