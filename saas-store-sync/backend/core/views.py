"""
Health and readiness for production (k8s, load balancers, monitoring).
"""
from django.http import JsonResponse
from django.db import connection
from django.views.decorators.http import require_GET
from django.views.decorators.csrf import csrf_exempt


@require_GET
@csrf_exempt
def health(request):
    """Liveness: process is up. No DB check."""
    return JsonResponse({"status": "ok"})


@require_GET
@csrf_exempt
def ready(request):
    """Readiness: app can serve traffic (DB reachable)."""
    try:
        connection.ensure_connection()
        return JsonResponse({"status": "ok", "db": "ok"})
    except Exception as e:
        return JsonResponse({"status": "error", "db": str(e)}, status=503)


@require_GET
def metrics(request):
    """Placeholder for Prometheus or JSON metrics. Protect in production (auth or IP)."""
    return JsonResponse({
        "version": "1.0",
        "metrics": {},
    })
