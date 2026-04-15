"""Read-only APIs for Vendor lookup (for dropdowns)."""
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Vendor


class VendorViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Vendor.objects.exclude(code__iexact='aliexpress')
    permission_classes = [IsAuthenticated]

    def list(self, request, *args, **kwargs):
        data = [{'id': str(v.id), 'code': v.code, 'name': v.name} for v in self.get_queryset()]
        return Response(data)
