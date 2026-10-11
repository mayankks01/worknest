from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.conf import settings
from django.contrib.sessions.models import Session
from django.core import mail
from django.db import IntegrityError, close_old_connections, transaction
from django.test import Client
from django.utils import timezone
from redis.exceptions import ConnectionError as RedisConnectionError

from accounts.models import AccountToken, User
from accounts.permissions import HasVerifiedEmail
from accounts.services import issue_token, secret_for
from accounts.services import schedule_email as publish_email
from accounts.tasks import dispatch_emails, send_account_email
from accounts.throttle import check_rate

PASSWORD = "Orchid-river-bridge-94!"
NEW_PASSWORD = "Copper-sunset-lantern-62!"


@pytest.fixture(autouse=True)
def isolated_services():
    with (
        patch("accounts.views.check_rate", return_value=0),
        patch("accounts.services.schedule_email"),
    ):
        yield


@pytest.fixture
def user(db):
    return User.objects.create_user("person@example.test", PASSWORD, display_name="Test Person")


def post(client, route, data=None, **extra):
    return client.post(
        f"/api/v1/auth/{route}/", data=data or {}, content_type="application/json", **extra
    )


def token_for(user, purpose="verify"):
    with transaction.atomic():
        token = issue_token(user, purpose)
    return token, f"{token.id}.{secret_for(token, user)}"


@pytest.mark.django_db
def test_registration_normalizes_and_does_not_reveal_existing_account(client):
    data = {"email": "  Person@Example.TEST  ", "password": PASSWORD, "display_name": "Person"}
    first = post(client, "register", data)
    duplicate = post(client, "register", {**data, "password": NEW_PASSWORD})
    assert first.status_code == duplicate.status_code == 202
    assert first.json() == duplicate.json()
    user = User.objects.get()
    assert user.email == "person@example.test" and user.check_password(PASSWORD)
    assert user.verified_at is None
    assert AccountToken.objects.count() == 1
    assert "token" not in first.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "changes",
    [
        {"password": "short"},
        {"password": "123456789012"},
        {"password": "x" * 129},
        {"email": "invalid"},
        {"display_name": " "},
        {"is_staff": "true"},
        {"organization_id": str(uuid4())},
        {"email": 42},
    ],
)
def test_registration_rejects_weak_and_unexpected_input(client, changes):
    response = post(
        client,
        "register",
        {"email": "new@example.test", "password": PASSWORD, "display_name": "Person", **changes},
    )
    assert response.status_code == 400
    assert response.json()["error"]["request_id"] == response["X-Request-ID"]
    assert User.objects.count() == 0


def test_database_rejects_non_normalized_email(user):
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create(email="UPPER@example.test", display_name="Invalid")


def test_login_logout_and_csrf_rotation(user):
    client = Client(enforce_csrf_checks=True)
    before = client.get("/api/v1/auth/session/").json()["csrf_token"]
    assert post(client, "login", {"email": user.email, "password": PASSWORD}).status_code == 403
    response = post(
        client,
        "login",
        {"email": user.email.upper(), "password": PASSWORD},
        HTTP_X_CSRFTOKEN=before,
    )
    assert response.status_code == 200
    assert response.json()["user"]["verified_at"] is None
    cookie = response.cookies[settings.SESSION_COOKIE_NAME]
    assert cookie["httponly"] and cookie["samesite"] == "Lax"
    assert response["Cache-Control"] == "no-store"
    assert client.get("/api/v1/auth/me/").status_code == 200
    assert post(client, "logout", HTTP_X_CSRFTOKEN=before).status_code == 403
    assert post(client, "logout", HTTP_X_CSRFTOKEN=response.json()["csrf_token"]).status_code == 200
    assert client.get("/api/v1/auth/me/").status_code == 401


@pytest.mark.parametrize(
    "route,data",
    [
        ("register", {}),
        ("password-reset", {}),
        ("password-reset/confirm", {}),
        ("verify-email", {}),
        ("resend-verification", {}),
        ("logout", {}),
    ],
)
def test_anonymous_posts_require_csrf(route, data):
    response = post(Client(enforce_csrf_checks=True), route, data)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_FAILED"


def test_cross_origin_login_is_rejected(user):
    client = Client(enforce_csrf_checks=True)
    csrf = client.get("/api/v1/auth/session/").json()["csrf_token"]
    assert (
        post(
            client,
            "login",
            {"email": user.email, "password": PASSWORD},
            HTTP_X_CSRFTOKEN=csrf,
            HTTP_ORIGIN="https://evil.example",
        ).status_code
        == 403
    )


def test_login_rotates_existing_session_key(user, client):
    old = client.session
    old["prelogin"] = "value"
    old.save()
    post(client, "login", {"email": user.email, "password": PASSWORD})
    assert client.session.session_key != old.session_key


def test_inactive_user_and_bad_password_are_generic(user, client):
    missing = post(client, "login", {"email": "missing@example.test", "password": PASSWORD})
    wrong = post(client, "login", {"email": user.email, "password": NEW_PASSWORD})
    client.force_login(user)
    user.is_active = False
    user.save()
    assert client.get("/api/v1/auth/me/").status_code == 401
    inactive = post(client, "login", {"email": user.email, "password": PASSWORD})
    assert missing.status_code == wrong.status_code == inactive.status_code == 401
    assert missing.json()["error"]["message"] == inactive.json()["error"]["message"]


def test_verification_once_and_future_organization_gate(user, client):
    assert not HasVerifiedEmail().has_permission(SimpleNamespace(user=user), None)
    token, value = token_for(user)
    assert post(client, "verify-email", {"token": value}).status_code == 200
    user.refresh_from_db()
    assert user.verified_at
    assert HasVerifiedEmail().has_permission(SimpleNamespace(user=user), None)
    assert post(client, "verify-email", {"token": value}).status_code == 400
    token.refresh_from_db()
    assert token.used_at


@pytest.mark.parametrize(
    "purpose,route", [("verify", "verify-email"), ("reset", "password-reset/confirm")]
)
def test_token_expiry_wrong_purpose_and_tamper(user, client, purpose, route):
    token, value = token_for(user, purpose)
    expected = 1800 if purpose == "reset" else 86400
    assert abs((token.expires_at - token.created_at).total_seconds() - expected) < 1
    other = "verify-email" if purpose == "reset" else "password-reset/confirm"
    assert (
        post(
            client,
            other,
            {"token": value, **({"password": NEW_PASSWORD} if other != "verify-email" else {})},
        ).status_code
        == 400
    )
    body = {"token": value, **({"password": NEW_PASSWORD} if purpose == "reset" else {})}
    assert (
        post(
            client, route, {**body, "token": value[:-1] + ("0" if value[-1] != "0" else "1")}
        ).status_code
        == 400
    )
    AccountToken.objects.filter(pk=token.pk).update(expires_at=timezone.now())
    assert post(client, route, body).status_code == 400


def test_reset_invalidates_all_sessions_and_links(user, client):
    other = Client()
    for browser in [client, other]:
        post(browser, "login", {"email": user.email, "password": PASSWORD})
    token, value = token_for(user, "reset")
    _, verify = token_for(user, "verify")
    assert (
        post(
            client, "password-reset/confirm", {"token": value, "password": NEW_PASSWORD}
        ).status_code
        == 200
    )
    assert client.get("/api/v1/auth/me/").status_code == 401
    assert other.get("/api/v1/auth/me/").status_code == 401
    assert post(client, "verify-email", {"token": verify}).status_code == 400
    assert (
        post(client, "password-reset/confirm", {"token": value, "password": PASSWORD}).status_code
        == 400
    )
    assert post(client, "login", {"email": user.email, "password": PASSWORD}).status_code == 401
    assert post(client, "login", {"email": user.email, "password": NEW_PASSWORD}).status_code == 200


def test_resend_invalidates_previous_link_without_account_disclosure(user, client):
    _, old = token_for(user)
    known = post(client, "resend-verification", {"email": user.email})
    unknown = post(client, "resend-verification", {"email": "unknown@example.test"})
    assert known.json() == unknown.json() and known.status_code == 202
    assert post(client, "verify-email", {"token": old}).status_code == 400
    token = AccountToken.objects.get(used_at=None)
    assert token.token_hash != secret_for(token, user)


def test_reset_request_is_generic(user, client):
    assert (
        post(client, "password-reset", {"email": user.email}).json()
        == post(client, "password-reset", {"email": "unknown@example.test"}).json()
    )


def test_throttle_and_outage_fail_closed(user, client):
    with patch("accounts.views.check_rate", return_value=59):
        response = post(client, "login", {"email": user.email, "password": PASSWORD})
    assert response.status_code == 429 and response["Retry-After"] == "59"
    with patch("accounts.views.check_rate", side_effect=RedisConnectionError("secret")):
        response = post(client, "login", {"email": user.email, "password": PASSWORD})
    assert response.status_code == 503 and "secret" not in response.content.decode()


def test_real_redis_rate_limits_both_email_and_ip(settings):
    settings.AUTH_RATE_LIMITS = {"login": (2, 1, 60)}
    tag = str(uuid4())
    request = SimpleNamespace(META={"REMOTE_ADDR": tag, "HTTP_X_FORWARDED_FOR": "fake"})
    assert check_rate(request, "login", tag + "@example.test") == 0
    assert check_rate(request, "login", tag + "@example.test") > 0
    assert check_rate(request, "login", "other" + tag + "@example.test") > 0


def test_email_delivery_is_single_job_and_secret_not_stored(user):
    token, value = token_for(user)
    send_account_email(str(token.id))
    send_account_email(str(token.id))
    assert len(mail.outbox) == 1 and value in mail.outbox[0].body
    token.refresh_from_db()
    assert token.delivery_state == "sent" and token.attempts == 1
    assert value.split(".")[1] not in str(token.__dict__)


def test_email_failure_retries_then_records_failure(user):
    token, _ = token_for(user)
    with patch("accounts.tasks.send_mail", side_effect=OSError("secret-provider-error")):
        for attempt in range(1, 6):
            AccountToken.objects.filter(pk=token.id).update(next_attempt_at=timezone.now())
            send_account_email(str(token.id))
            token.refresh_from_db()
            assert token.attempts == attempt
    assert token.delivery_state == "failed"
    assert "secret-provider-error" not in str(token.__dict__)


def test_dispatch_recovers_pending_emails(user):
    token, _ = token_for(user)
    with patch("accounts.tasks.send_account_email.apply_async") as deliver:
        dispatch_emails()
    deliver.assert_called_once_with(args=[str(token.id)], retry=False)


@pytest.mark.django_db(transaction=True)
def test_reset_race_consumes_token_once():
    user = User.objects.create_user("race@example.test", PASSWORD, display_name="Race Person")
    _, value = token_for(user, "reset")

    def submit():
        close_old_connections()
        try:
            return post(
                Client(), "password-reset/confirm", {"token": value, "password": NEW_PASSWORD}
            ).status_code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))
    assert sorted(results) == [200, 400]


def test_weak_reset_does_not_consume_token(user, client):
    token, value = token_for(user, "reset")
    assert (
        post(client, "password-reset/confirm", {"token": value, "password": "short"}).status_code
        == 400
    )
    token.refresh_from_db()
    assert token.used_at is None


def test_session_expires_and_relogin_rotates_key(user, client):
    credentials = {"email": user.email, "password": PASSWORD}
    post(client, "login", credentials)
    old_key = client.session.session_key
    post(client, "login", credentials)
    assert client.session.session_key != old_key
    session = Session.objects.get(session_key=client.session.session_key)
    assert 43190 < (session.expire_date - timezone.now()).total_seconds() <= 43200
    session.expire_date = timezone.now() - timedelta(seconds=1)
    session.save()
    assert client.get("/api/v1/auth/me/").status_code == 401


def test_consumed_email_is_never_delivered(user):
    token, _ = token_for(user)
    AccountToken.objects.filter(pk=token.pk).update(used_at=timezone.now())
    send_account_email(str(token.pk))
    token.refresh_from_db()
    assert token.delivery_state == "obsolete"


def test_exhausted_worker_lease_becomes_failed(user):
    token, _ = token_for(user)
    AccountToken.objects.filter(pk=token.pk).update(
        delivery_state="sending", attempts=5, next_attempt_at=timezone.now()
    )
    dispatch_emails()
    token.refresh_from_db()
    assert token.delivery_state == "failed"


def test_broker_failure_preserves_email_job(user):
    token, _ = token_for(user)
    with patch("accounts.tasks.send_account_email.apply_async", side_effect=OSError("offline")):
        publish_email(str(token.pk))
    token.refresh_from_db()
    assert token.delivery_state == "queued" and token.used_at is None


def test_atomic_redis_limit_under_concurrency(settings):
    settings.AUTH_RATE_LIMITS = {"login": (100, 3, 60)}
    tag = str(uuid4())
    request = SimpleNamespace(META={"REMOTE_ADDR": tag})
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: check_rate(request, "login", tag), range(8)))
    assert results.count(0) == 3
