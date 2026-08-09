from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from .org_views import (
    OrganizationPlanUpdateView,
    PricingPlansView,
    SuperUserSignupStartView,
    SuperUserSignupVerifyView,
    TeamMemberDetailView,
    TeamMemberListCreateView,
    TypedLoginView,
)
from .platform_views import (
    PlatformAuditListView,
    PlatformOrganizationListView,
    PlatformStatsView,
    PlatformUserDetailView,
    PlatformUserListView,
)
from .views import GoogleCallbackView, GoogleLoginView, RegisterView, UserProfileView

urlpatterns = [
    path('register/', RegisterView.as_view(), name='auth_register'),
    path('login/', TypedLoginView.as_view(), name='token_obtain_pair'),
    path('refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('profile/', UserProfileView.as_view(), name='user_profile'),
    path('pricing/', PricingPlansView.as_view(), name='auth_pricing'),
    path('super-user/signup/', SuperUserSignupStartView.as_view(), name='super_user_signup'),
    path('super-user/verify-otp/', SuperUserSignupVerifyView.as_view(), name='super_user_verify_otp'),
    path('team/', TeamMemberListCreateView.as_view(), name='team_members'),
    path('team/<uuid:member_id>/', TeamMemberDetailView.as_view(), name='team_member_detail'),
    path('organization/plan/', OrganizationPlanUpdateView.as_view(), name='organization_plan'),
    path('platform/stats/', PlatformStatsView.as_view(), name='platform_stats'),
    path('platform/users/', PlatformUserListView.as_view(), name='platform_users'),
    path('platform/users/<uuid:user_id>/', PlatformUserDetailView.as_view(), name='platform_user_detail'),
    path('platform/organizations/', PlatformOrganizationListView.as_view(), name='platform_organizations'),
    path('platform/audit/', PlatformAuditListView.as_view(), name='platform_audit'),
    path('google/', GoogleLoginView.as_view(), name='auth_google'),
    # Backward-compatible alias for older frontend links still pointing to /google/next.
    path('google/next', GoogleLoginView.as_view(), name='auth_google_next'),
    path('google/next/', GoogleLoginView.as_view(), name='auth_google_next_slash'),
    path('google/callback/', GoogleCallbackView.as_view(), name='auth_google_callback'),
]
