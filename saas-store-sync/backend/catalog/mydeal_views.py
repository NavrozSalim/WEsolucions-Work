"""API views for Mydeal template upload and export."""
from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog.mydeal_templates import (
    build_export_response,
    ingest_mydeal_template,
    ingest_mydeal_templates_zip,
    store_is_mydeal,
    template_status,
)
from stores.models import Store


class MydealTemplateStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, store_pk):
        store = get_object_or_404(
            Store.objects.select_related('marketplace'),
            id=store_pk,
            user=request.user,
        )
        if not store_is_mydeal(store):
            return Response(
                {'error': 'Not a Mydeal store.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(template_status(store))


class MydealTemplateUploadView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request, store_pk):
        store = get_object_or_404(
            Store.objects.select_related('marketplace'),
            id=store_pk,
            user=request.user,
        )
        if not store_is_mydeal(store):
            return Response(
                {'error': 'Not a Mydeal store.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        kind = (request.data.get('kind') or request.query_params.get('kind') or '').strip().lower()
        file_obj = request.data.get('file')
        if not file_obj:
            return Response(
                {'error': 'No file provided.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            if kind in ('zip', 'both'):
                result = ingest_mydeal_templates_zip(store, file_obj)
            elif kind in ('price', 'inventory'):
                result = ingest_mydeal_template(store, kind, file_obj)
            else:
                return Response(
                    {'error': 'kind must be price, inventory, or zip.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result, status=status.HTTP_201_CREATED)


class MydealTemplateExportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, store_pk):
        store = get_object_or_404(
            Store.objects.select_related('marketplace'),
            id=store_pk,
            user=request.user,
        )
        if not store_is_mydeal(store):
            return Response(
                {'error': 'Not a Mydeal store.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        export_type = (request.query_params.get('type') or 'both').strip().lower()
        try:
            return build_export_response(store, export_type)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
