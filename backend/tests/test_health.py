from unittest.mock import MagicMock, patch
from uuid import UUID

from django.db import OperationalError
from redis.exceptions import ConnectionError


def test_liveness_does_not_touch_dependencies(client):
    with patch("foundation.views.connection") as database, patch("foundation.views.Redis") as redis:
        response = client.get("/api/v1/health/live/")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    database.cursor.assert_not_called()
    redis.from_url.assert_not_called()
    UUID(response["X-Request-ID"])
    assert response["Cache-Control"] == "no-store"


def test_readiness_succeeds_when_dependencies_respond(client):
    with patch("foundation.views.connection"), patch("foundation.views.Redis.from_url"):
        response = client.get("/api/v1/health/ready/")
    assert response.status_code == 200
    assert response.json()["checks"] == {"database": "ok", "redis": "ok"}


def test_readiness_fails_safely_and_still_checks_redis(client):
    with (
        patch("foundation.views.connection") as database,
        patch("foundation.views.Redis.from_url") as redis,
    ):
        database.cursor.side_effect = OperationalError("password=do-not-leak")
        redis.return_value.__enter__.return_value = MagicMock()
        response = client.get("/api/v1/health/ready/")
    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "unavailable", "redis": "ok"}
    assert "do-not-leak" not in response.content.decode()
    assert response["Retry-After"] == "5"
    assert response.json()["error"]["request_id"] == response["X-Request-ID"]


def test_broker_outage_does_not_mark_readiness_successful(client):
    with (
        patch("foundation.views.connection"),
        patch("foundation.views.Redis.from_url", side_effect=ConnectionError("secret-url")),
    ):
        response = client.get("/api/v1/health/ready/")
    assert response.status_code == 503
    assert response.json()["checks"]["redis"] == "unavailable"
    assert "secret-url" not in response.content.decode()


def test_health_rejects_writes(client):
    assert client.post("/api/v1/health/live/").status_code == 405
