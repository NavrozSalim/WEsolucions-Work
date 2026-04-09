from django.urls import path
from .views import RegisterView, UserProfileView, LoginView, GoogleLoginView, GoogleCallbackView
from rest_framework_simplejwt.views import TokenRefreshView

urlpatterns = [
    path('register/', RegisterView.as_view(), name='auth_register'),
    path('login/', LoginView.as_view(), name='token_obtain_pair'),
    path('refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('profile/', UserProfileView.as_view(), name='user_profile'),
    path('google/', GoogleLoginView.as_view(), name='auth_google'),
    # Backward-compatible alias for older frontend links still pointing to /google/next.
    path('google/next', GoogleLoginView.as_view(), name='auth_google_next'),
    path('google/next/', GoogleLoginView.as_view(), name='auth_google_next_slash'),
    path('google/callback/', GoogleCallbackView.as_view(), name='auth_google_callback'),
]
