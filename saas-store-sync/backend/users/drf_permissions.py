"""DRF permission helpers for product module access."""

from rest_framework.permissions import BasePermission


class HasProductPermission(BasePermission):
    """
    Require a product permission key on the view:
      permission_classes = [IsAuthenticated, HasProductPermission]
      required_product_permission = 'catalog'
    Super Users and standalone accounts always pass.
    """

    message = 'You do not have permission to access this module.'

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        key = getattr(view, 'required_product_permission', None)
        if not key:
            return True
        return user.has_product_permission(key)
