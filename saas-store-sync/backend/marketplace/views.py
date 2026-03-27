"""Read-only APIs for Marketplace lookup (for dropdowns)."""
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Marketplace


class MarketplaceViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Marketplace.objects.all()
    permission_classes = [IsAuthenticated]

    def list(self, request, *args, **kwargs):
        data = [{'id': str(m.id), 'code': m.code, 'name': m.name} for m in self.get_queryset()]
        return Response(data)
