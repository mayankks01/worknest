import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.test import Client
from django.utils import timezone

from accounts.models import User
from organizations.context import tenant_transaction
from organizations.models import AuditEvent, CreationRequest, Membership, Organization, OutboxEvent

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(scope="module")
def app_role(django_db_setup, django_db_blocker):
    name = f"test_worknest_{uuid.uuid4().hex}"
    with django_db_blocker.unblock(), connection.cursor() as cursor:
        cursor.execute(f'CREATE ROLE "{name}" NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE')
        cursor.execute(f'GRANT USAGE ON SCHEMA public TO "{name}"')
        cursor.execute(
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{name}"'
        )
        cursor.execute(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{name}"')
    yield name
    with django_db_blocker.unblock(), connection.cursor() as cursor:
        cursor.execute("RESET ROLE")
        cursor.execute(f'DROP OWNED BY "{name}"')
        cursor.execute(f'DROP ROLE "{name}"')


@contextmanager
def restricted(app_role):
    with connection.cursor() as cursor:
        cursor.execute(f'SET ROLE "{app_role}"')
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET ROLE")


@pytest.fixture
def owner():
    return User.objects.create_user(
        "owner@example.test", "test-password-1234", display_name="Owner", verified_at=timezone.now()
    )


def create(client, slug="first-workspace", key=None, **extra):
    return client.post(
        "/api/v1/orgs/",
        data=json.dumps({"display_name": "Our workspace", "slug": slug, **extra}),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=key or uuid.uuid4().hex,
    )


@pytest.fixture
def signed_in(owner):
    client = Client()
    client.force_login(owner)
    return client


def make_org(client, app_role, slug="first-workspace"):
    with restricted(app_role):
        response = create(client, slug)
    assert response.status_code == 201, response.content
    return response.json()["organization"]


def test_creation_atomic_owner_audit_outbox(signed_in, owner, app_role):
    org = make_org(signed_in, app_role)
    assert org["membership"]["role"] == "Owner"
    assert Membership.objects.get(org_id=org["id"]).user == owner
    assert AuditEvent.objects.get(org_id=org["id"]).action == "organization.created"
    assert OutboxEvent.objects.get(org_id=org["id"]).dispatched_at is None
    with restricted(app_role):
        assert signed_in.get(f"/api/v1/orgs/{org['id']}/").status_code == 200


def test_idempotency_replays_and_conflicts(signed_in, app_role):
    key = uuid.uuid4().hex
    with restricted(app_role):
        first = create(signed_in, key=key)
        again = create(signed_in, key=key)
        conflict = create(signed_in, slug="other-workspace", key=key)
    assert first.status_code == again.status_code == 201
    assert first.json() == again.json()
    assert conflict.status_code == 409
    assert (
        Organization.objects.count()
        == AuditEvent.objects.count()
        == OutboxEvent.objects.count()
        == 1
    )


def test_failed_event_rolls_back_everything(signed_in, app_role):
    with (
        restricted(app_role),
        patch("organizations.views.OutboxEvent.objects.create", side_effect=IntegrityError),
    ):
        response = create(signed_in)
    assert response.status_code == 503
    assert (
        Organization.objects.count()
        == Membership.objects.count()
        == CreationRequest.objects.count()
        == 0
    )
    assert AuditEvent.objects.count() == 0


def test_unique_slug_returns_field_error(signed_in, app_role):
    make_org(signed_in, app_role)
    with restricted(app_role):
        response = create(signed_in)
    assert response.status_code == 400
    assert "slug" in response.json()["error"]["details"]
    assert Organization.objects.count() == 1


@pytest.mark.parametrize(
    "values",
    [
        {"slug": "Bad!"},
        {"slug": "api"},
        {"display_name": " "},
        {"timezone": "Not/AZone"},
        {"org_id": str(uuid.uuid4())},
        {"role": "Owner"},
        {"owner_membership_id": str(uuid.uuid4())},
    ],
)
def test_rejects_invalid_and_untrusted_fields(signed_in, app_role, values):
    with restricted(app_role):
        assert create(signed_in, **values).status_code == 400
    assert Organization.objects.count() == 0


def test_requires_auth_verification_key_and_csrf(owner, signed_in, app_role):
    with restricted(app_role):
        assert Client().get("/api/v1/orgs/").status_code == 401
        owner.verified_at = None
        owner.save(update_fields=["verified_at"])
        assert create(signed_in).status_code == 403
        owner.verified_at = timezone.now()
        owner.save(update_fields=["verified_at"])
        assert create(signed_in, key="short").status_code == 400
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(owner)
        assert create(protected).status_code == 403


def test_discovery_and_cross_tenant_404(signed_in, owner, app_role):
    org = make_org(signed_in, app_role)
    stranger = User.objects.create_user("stranger@example.test", verified_at=timezone.now())
    other = Client()
    other.force_login(stranger)
    second = make_org(other, app_role, "other-workspace")
    with restricted(app_role):
        assert [row["id"] for row in signed_in.get("/api/v1/orgs/").json()["results"]] == [
            org["id"]
        ]
        assert signed_in.get(f"/api/v1/orgs/{second['id']}/").status_code == 404
        assert (
            other.patch(
                f"/api/v1/orgs/{org['id']}/",
                json.dumps({"display_name": "stolen"}),
                content_type="application/json",
            ).status_code
            == 404
        )
        with tenant_transaction(owner.id, second["id"]):
            assert (
                Organization.objects.count()
                == AuditEvent.objects.count()
                == OutboxEvent.objects.count()
                == 0
            )


def test_missing_context_connection_reuse_and_nested_rollback(signed_in, owner, app_role):
    org = make_org(signed_in, app_role)
    with restricted(app_role):
        for model in (Organization, Membership, AuditEvent, OutboxEvent, CreationRequest):
            assert model.objects.count() == 0
        with tenant_transaction(owner.id, org["id"]):
            assert Organization.objects.count() == 1
            with pytest.raises(ValueError), tenant_transaction(owner.id, uuid.uuid4()):
                assert Organization.objects.count() == 0
                raise ValueError("rollback")
            assert Organization.objects.count() == 1
        assert Organization.objects.count() == Membership.objects.count() == 0
        assert signed_in.get(f"/api/v1/orgs/{org['id']}/").status_code == 200
        assert Organization.objects.count() == 0


@pytest.mark.parametrize("role,expected", [("Admin", 200), ("Member", 403), ("Viewer", 403)])
def test_role_permissions_and_removal(signed_in, owner, app_role, role, expected):
    org = make_org(signed_in, app_role)
    member = User.objects.create_user("member@example.test", verified_at=timezone.now())
    membership = Membership.objects.create(org_id=org["id"], user=member, role=role)
    client = Client()
    client.force_login(member)
    with restricted(app_role):
        response = client.patch(
            f"/api/v1/orgs/{org['id']}/",
            json.dumps({"display_name": "Renamed"}),
            content_type="application/json",
        )
        assert response.status_code == expected
    membership.state = "removed"
    membership.save()
    with restricted(app_role):
        assert client.get(f"/api/v1/orgs/{org['id']}/").status_code == 404
        assert client.get("/api/v1/orgs/").json()["results"] == []
        assert client.get("/api/v1/auth/me/").status_code == 200


def test_settings_audited_and_noop_does_not_duplicate(signed_in, app_role):
    org = make_org(signed_in, app_role)
    for _ in range(2):
        with restricted(app_role):
            response = signed_in.patch(
                f"/api/v1/orgs/{org['id']}/",
                json.dumps({"display_name": "New name", "timezone": "Asia/Kolkata"}),
                content_type="application/json",
            )
            assert response.status_code == 200
    assert AuditEvent.objects.count() == OutboxEvent.objects.count() == 2


def test_owner_and_cross_org_constraints(signed_in, owner, app_role):
    first = make_org(signed_in, app_role)
    second = make_org(signed_in, app_role, "second-workspace")
    with pytest.raises(IntegrityError), transaction.atomic():
        Membership.objects.filter(pk=first["membership"]["id"]).update(state="removed")
    with pytest.raises(IntegrityError), transaction.atomic():
        Organization.objects.filter(pk=first["id"]).update(
            owner_membership_id=second["membership"]["id"]
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        AuditEvent.objects.create(
            org_id=first["id"],
            actor_membership_id=second["membership"]["id"],
            action="invalid",
            resource_id=first["id"],
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        OutboxEvent.objects.create(
            org_id=second["id"],
            audit_id=AuditEvent.objects.get(org_id=first["id"]).id,
            topic="invalid",
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        Organization.objects.create(
            created_by=owner,
            display_name="Orphan",
            slug="orphan-org",
            owner_membership_id=uuid.uuid4(),
        )


def test_audit_immutable_and_role_cannot_bypass(signed_in, owner, app_role):
    org = make_org(signed_in, app_role)
    with restricted(app_role), tenant_transaction(owner.id, org["id"]):
        assert AuditEvent.objects.update(action="tampered") == 0
        assert AuditEvent.objects.all().delete()[0] == 0
        with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("ALTER TABLE organizations_organization DISABLE ROW LEVEL SECURITY")
        with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("TRUNCATE organizations_auditevent CASCADE")
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname LIKE 'organizations_%' AND relkind = 'r'"
            )
            assert all(row == (True, True) for row in cursor.fetchall())


def test_pagination_and_expired_idempotency(signed_in, app_role):
    key = uuid.uuid4().hex
    with restricted(app_role):
        assert create(signed_in, key=key).status_code == 201
    CreationRequest.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    with restricted(app_role):
        assert create(signed_in, slug="second-workspace", key=key).status_code == 201
        first = signed_in.get("/api/v1/orgs/?limit=1").json()
        second = signed_in.get("/api/v1/orgs/", {"limit": 1, "cursor": first["next_cursor"]}).json()
        assert len(first["results"]) == len(second["results"]) == 1
        assert first["results"][0]["id"] != second["results"][0]["id"]
        assert second["next_cursor"] is None
        assert signed_in.get("/api/v1/orgs/?cursor=bad").status_code == 400
        assert signed_in.get("/api/v1/orgs/?limit=101").status_code == 400


def test_simultaneous_creation_retries_create_once(owner, app_role):
    clients = [Client(), Client()]
    for client in clients:
        client.force_login(owner)
    key = uuid.uuid4().hex

    def attempt(client):
        try:
            with restricted(app_role):
                response = create(client, key=key)
                return response.status_code, response.json()
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, clients))
    assert results[0] == results[1]
    assert results[0][0] == 201
    assert Organization.objects.count() == OutboxEvent.objects.count() == 1


def test_removed_actor_cannot_replay_saved_response(signed_in, owner, app_role):
    key = uuid.uuid4().hex
    with restricted(app_role):
        response = create(signed_in, key=key)
    org_id = response.json()["organization"]["id"]
    Organization.objects.filter(pk=org_id).update(state="pending_delete")
    with restricted(app_role):
        assert create(signed_in, key=key).status_code == 404
        assert signed_in.get(f"/api/v1/orgs/{org_id}/").status_code == 404


def test_wrong_context_write_and_row_security_off_fail(signed_in, owner, app_role):
    org = make_org(signed_in, app_role)
    with restricted(app_role), tenant_transaction(owner.id, uuid.uuid4()):
        assert Organization.objects.filter(pk=org["id"]).update(display_name="tampered") == 0
        with pytest.raises(DatabaseError), transaction.atomic():
            OutboxEvent.objects.create(org_id=org["id"], audit_id=uuid.uuid4(), topic="invalid")
        with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SET LOCAL row_security = off")
            cursor.execute("SELECT * FROM organizations_organization")
