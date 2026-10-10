from django.conf import settings
from django.db import DatabaseError, connection
from redis import Redis
from redis.exceptions import RedisError
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


@api_view(["GET"])
@permission_classes([AllowAny])
def live(request):
    """Process health must not depend on database, broker, email, or object storage."""
    return Response({"status": "ok", "service": "worknest-api"})


@api_view(["GET"])
@permission_classes([AllowAny])
def ready(request):
    """Check current foundation dependencies without exposing connection details."""
    checks = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["database"] = "ok"
    except DatabaseError:
        checks["database"] = "unavailable"

    try:
        with Redis.from_url(
            settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2
        ) as client:
            client.ping()
        checks["redis"] = "ok"
    except (RedisError, OSError):
        checks["redis"] = "unavailable"

    if all(value == "ok" for value in checks.values()):
        return Response({"status": "ready", "checks": checks})
    return Response(
        {
            "status": "unavailable",
            "checks": checks,
            "error": {
                "code": "DEPENDENCY_UNAVAILABLE",
                "message": "A required service is unavailable. Please retry shortly.",
                "request_id": request.request_id,
                "details": {},
            },
        },
        status=503,
        headers={"Retry-After": "5"},
    )
