from django.shortcuts import get_object_or_404
from rest_framework import viewsets, status
from rest_framework.views import APIView
from rest_framework.response import Response
from sync.models import SyncSchedule, StoreSyncRun
from sync.serializers import SyncScheduleSerializer, StoreSyncRunSerializer
from stores.models import Store
from rest_framework.permissions import IsAuthenticated
from core.throttles import SyncTriggerRateThrottle


class StoreSyncRunViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = StoreSyncRunSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        store_id = self.kwargs.get('store_pk')
        if store_id:
            return StoreSyncRun.objects.filter(store_id=store_id, store__user=self.request.user).order_by('-started_at')
        return StoreSyncRun.objects.filter(store__user=self.request.user).order_by('-started_at')


class SyncScheduleView(APIView):
    """GET/PUT/PATCH for per-store sync schedule (OneToOne with Store)."""
    permission_classes = [IsAuthenticated]

    def get_store(self, store_pk):
        return Store.objects.get(id=store_pk, user=self.request.user)

    def get(self, request, store_pk):
        try:
            store = self.get_store(store_pk)
        except Store.DoesNotExist:
            return Response({"error": "Store not found"}, status=status.HTTP_404_NOT_FOUND)
        try:
            schedule = store.sync_schedule
        except SyncSchedule.DoesNotExist:
            return Response({"schedule": None, "message": "No schedule configured"}, status=status.HTTP_200_OK)
        return Response(SyncScheduleSerializer(schedule).data)

    def put(self, request, store_pk):
        return self._create_or_update(request, store_pk, partial=False)

    def patch(self, request, store_pk):
        return self._create_or_update(request, store_pk, partial=True)

    def _create_or_update(self, request, store_pk, partial=False):
        try:
            store = self.get_store(store_pk)
        except Store.DoesNotExist:
            return Response({"error": "Store not found"}, status=status.HTTP_404_NOT_FOUND)
        try:
            schedule = store.sync_schedule
        except SyncSchedule.DoesNotExist:
            schedule = None
        serializer = SyncScheduleSerializer(schedule, data=request.data, partial=partial)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save(store=store)
        return Response(serializer.data)


class TriggerManualSyncView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [SyncTriggerRateThrottle]

    def post(self, request, store_pk, *args, **kwargs):
        try:
            store = Store.objects.get(id=store_pk, user=request.user)
        except Store.DoesNotExist:
            return Response({"error": "Store not found"}, status=status.HTTP_404_NOT_FOUND)

        try:
            from sync.tasks import run_store_sync
            run_store_sync.delay(str(store.pk))
        except Exception as e:
            detail = str(e)
            if "redis" in detail.lower() or "connection" in detail.lower():
                detail = "Redis unavailable. Ensure Redis is running (e.g. docker compose up redis)."
            return Response({"detail": detail}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        return Response({"message": "Manual sync triggered successfully"}, status=status.HTTP_200_OK)


class TriggerManualUpdateView(APIView):
    """Scrape + push to marketplace in one shot."""
    permission_classes = [IsAuthenticated]
    throttle_classes = [SyncTriggerRateThrottle]

    def post(self, request, store_pk, *args, **kwargs):
        try:
            store = Store.objects.get(id=store_pk, user=request.user)
        except Store.DoesNotExist:
            return Response({"error": "Store not found"}, status=status.HTTP_404_NOT_FOUND)

        if store.connection_status != 'connected':
            return Response(
                {"error": "Store not connected. Validate connection first."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        run_inline = (
            request.data.get('run_inline')
            or request.query_params.get('inline') == '1'
        )

        if run_inline:
            from sync.tasks import run_store_update
            result = run_store_update(str(store.pk))
            return Response(result, status=status.HTTP_200_OK)

        try:
            from sync.tasks import run_store_update
            async_result = run_store_update.delay(str(store.pk))
        except Exception as e:
            detail = str(e)
            if "redis" in detail.lower() or "connection" in detail.lower():
                detail = (
                    "Redis/Celery unavailable — running update inline instead. "
                    "Start Redis + worker for background jobs."
                )
                from sync.tasks import run_store_update
                result = run_store_update(str(store.pk))
                return Response(result, status=status.HTTP_200_OK)
            return Response({"detail": detail}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        return Response(
            {
                "message": "Update queued — scrape and push to marketplace.",
                "job_id": async_result.id,
                "status": "queued",
            },
            status=status.HTTP_202_ACCEPTED,
        )


class SyncJobStatusView(APIView):
    """Poll Celery task status for manual store update (run_store_update)."""

    permission_classes = [IsAuthenticated]

    def get(self, request, store_pk, job_id):
        from celery.result import AsyncResult

        get_object_or_404(Store, id=store_pk, user=request.user)
        result = AsyncResult(job_id)
        data = {
            "job_id": job_id,
            "status": result.status.lower() if result.status else "unknown",
            "ready": result.ready(),
            "successful": result.successful() if result.ready() else None,
        }
        if result.ready():
            if result.successful():
                data["result"] = result.result
            else:
                err = result.result
                data["error"] = str(err) if err is not None else "Task failed"
        return Response(data)
