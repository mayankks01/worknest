import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.test import Client
from django.utils import timezone

from accounts.models import User
from organizations.context import tenant_transaction
from organizations.models import (
    AuditEvent,
    Invitation,
    Membership,
    Organization,
    OrganizationUsage,
    OutboxEvent,
)
from organizations.tasks import dispatch_invitations, send_invitation
from organizations.team import invitation_secret
from tests import test_organizations as org_tests

app_role = org_tests.app_role
owner = org_tests.owner
signed_in = org_tests.signed_in
make_org = org_tests.make_org
restricted = org_tests.restricted

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def no_external_jobs():
    with (
        patch("organizations.team.check_rate", return_value=0),
        patch("organizations.team.publish_email"),
    ):
        yield


@pytest.fixture
def org(signed_in, app_role):
    return make_org(signed_in, app_role)


def call(client, route, data=None, method="POST", key=None):
    return client.generic(
        method,
        route,
        data=json.dumps(data or {}),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=key or uuid.uuid4().hex,
    )


def user_client(email="member@example.test", verified=True):
    user = User.objects.create_user(
        email,
        "test-password-1234",
        display_name="Teammate",
        verified_at=timezone.now() if verified else None,
    )
    client = Client()
    client.force_login(user)
    return user, client


def seed_member(org, role="Member", email="member@example.test"):
    user, client = user_client(email)
    member = Membership.objects.create(org_id=org["id"], user=user, role=role)
    usage = OrganizationUsage.objects.get(org_id=org["id"])
    usage.active_members += 1
    usage.save()
    return member, client


def invite(client, org, app_role, email="member@example.test", role="Member", key=None):
    with restricted(app_role):
        response = call(
            client,
            f"/api/v1/orgs/{org['id']}/invitations/",
            {"email": email, "role": role},
            key=key,
        )
    assert response.status_code == 201, response.content
    invitation = Invitation.objects.get(pk=response.json()["invitation"]["id"])
    return invitation, f"{org['id']}.{invitation.id}.{invitation_secret(invitation)}"


def accept(client, token, app_role):
    with restricted(app_role):
        return call(client, "/api/v1/invitations/accept/", {"token": token})


def test_invite_accept_roster_and_replay(signed_in, org, app_role):
    invitation, token = invite(signed_in, org, app_role, email=" MEMBER@EXAMPLE.TEST ")
    user, client = user_client()
    response = accept(client, token, app_role)
    assert response.status_code == 200, response.content
    assert accept(client, token, app_role).status_code == 400
    assert Membership.objects.get(org_id=org["id"], user=user).role == "Member"
    assert OrganizationUsage.objects.get(org_id=org["id"]).active_members == 2
    invitation.refresh_from_db()
    assert invitation.state == "accepted"
    with restricted(app_role):
        roster = client.get(f"/api/v1/orgs/{org['id']}/members/").json()
        assert len(roster["results"]) == 2
        assert roster["usage"]["active_members"] == 2
        assert client.get(f"/api/v1/orgs/{org['id']}/invitations/").status_code == 403


@pytest.mark.parametrize(
    "condition", ["wrong_email", "unverified", "expired", "revoked", "tampered"]
)
def test_invitation_invalid_conditions(signed_in, org, app_role, condition):
    invitation, token = invite(signed_in, org, app_role)
    _, client = user_client(
        "wrong@example.test" if condition == "wrong_email" else "member@example.test",
        verified=condition != "unverified",
    )
    if condition == "expired":
        Invitation.objects.filter(pk=invitation.id).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
    if condition == "revoked":
        Invitation.objects.filter(pk=invitation.id).update(state="revoked")
    if condition == "tampered":
        token = token[:-4] + "abcd"
    assert accept(client, token, app_role).status_code == (
        403 if condition == "unverified" else 400
    )
    assert Membership.objects.count() == 1


def test_existing_member_accept_never_changes_role(signed_in, org, app_role):
    member, client = seed_member(org, role="Viewer")
    _, token = invite(signed_in, org, app_role, role="Admin")
    assert accept(client, token, app_role).status_code == 200
    member.refresh_from_db()
    assert member.role == "Viewer"
    assert OrganizationUsage.objects.get(org_id=org["id"]).active_members == 2


def test_invitation_idempotency_duplicate_and_owner_rejection(signed_in, org, app_role):
    key = uuid.uuid4().hex
    invitation, _ = invite(signed_in, org, app_role, key=key)
    again, _ = invite(signed_in, org, app_role, key=key)
    assert invitation.id == again.id
    with restricted(app_role):
        route = f"/api/v1/orgs/{org['id']}/invitations/"
        assert (
            call(
                signed_in, route, {"email": "member@example.test", "role": "Viewer"}, key=key
            ).status_code
            == 409
        )
        assert (
            call(signed_in, route, {"email": "member@example.test", "role": "Member"}).status_code
            == 409
        )
        assert (
            call(signed_in, route, {"email": "other@example.test", "role": "Owner"}).status_code
            == 400
        )
    assert Invitation.objects.count() == 1


def test_resend_rotates_and_revoke_invalidates(signed_in, org, app_role):
    invitation, old = invite(signed_in, org, app_role)
    _, client = user_client()
    with restricted(app_role):
        assert (
            call(
                signed_in, f"/api/v1/orgs/{org['id']}/invitations/{invitation.id}/resend/"
            ).status_code
            == 200
        )
    assert accept(client, old, app_role).status_code == 400
    invitation.refresh_from_db()
    assert invitation.token_version == 2
    new = f"{org['id']}.{invitation.id}.{invitation_secret(invitation)}"
    with restricted(app_role):
        assert (
            call(
                signed_in, f"/api/v1/orgs/{org['id']}/invitations/{invitation.id}/revoke/"
            ).status_code
            == 200
        )
    assert accept(client, new, app_role).status_code == 400


@pytest.mark.parametrize(
    "actor_role,target_role,new_role,expected",
    [
        ("Owner", "Member", "Admin", 200),
        ("Admin", "Member", "Viewer", 200),
        ("Admin", "Member", "Admin", 403),
        ("Admin", "Admin", "Member", 403),
        ("Member", "Viewer", "Member", 403),
        ("Viewer", "Member", "Viewer", 403),
    ],
)
def test_role_matrix(signed_in, org, app_role, actor_role, target_role, new_role, expected):
    actor = (
        signed_in
        if actor_role == "Owner"
        else seed_member(org, actor_role, "actor@example.test")[1]
    )
    target, _ = seed_member(org, target_role)
    with restricted(app_role):
        response = call(
            actor,
            f"/api/v1/orgs/{org['id']}/members/{target.id}/",
            {"role": new_role},
            method="PATCH",
        )
    assert response.status_code == expected, response.content


def test_admin_cannot_invite_or_manage_admin_invitation(signed_in, org, app_role):
    _, admin = seed_member(org, "Admin", "admin@example.test")
    invitation, _ = invite(signed_in, org, app_role, role="Admin")
    with restricted(app_role):
        assert (
            call(
                admin,
                f"/api/v1/orgs/{org['id']}/invitations/",
                {"email": "new@example.test", "role": "Admin"},
            ).status_code
            == 403
        )
        assert (
            call(admin, f"/api/v1/orgs/{org['id']}/invitations/{invitation.id}/resend/").status_code
            == 403
        )
        assert (
            call(admin, f"/api/v1/orgs/{org['id']}/invitations/{invitation.id}/revoke/").status_code
            == 403
        )


def test_remove_immediate_access_revocation_and_rejoin(signed_in, org, app_role):
    member, client = seed_member(org)
    own_org = make_org(client, app_role, "second-org")
    with restricted(app_role):
        response = call(
            signed_in, f"/api/v1/orgs/{org['id']}/members/{member.id}/", method="DELETE"
        )
        assert response.status_code == 200, response.content
        assert client.get(f"/api/v1/orgs/{org['id']}/members/").status_code == 404
        assert client.get(f"/api/v1/orgs/{own_org['id']}/").status_code == 200
        assert (
            call(
                signed_in,
                f"/api/v1/orgs/{org['id']}/members/{org['membership']['id']}/",
                method="DELETE",
            ).status_code
            == 403
        )
    assert OrganizationUsage.objects.get(org_id=org["id"]).active_members == 1
    _, token = invite(signed_in, org, app_role, role="Viewer")
    response = accept(client, token, app_role)
    assert response.status_code == 200, response.content
    member.refresh_from_db()
    assert (member.state, member.role) == ("active", "Viewer")


def test_transfer_requires_password_and_preserves_one_owner(signed_in, org, app_role):
    target, client = seed_member(org)
    route = f"/api/v1/orgs/{org['id']}/transfer-ownership/"
    with restricted(app_role):
        assert (
            call(signed_in, route, {"member_id": str(target.id), "password": "wrong"}).status_code
            == 403
        )
        assert (
            call(
                client,
                route,
                {"member_id": org["membership"]["id"], "password": "test-password-1234"},
            ).status_code
            == 403
        )
        response = call(
            signed_in, route, {"member_id": str(target.id), "password": "test-password-1234"}
        )
        assert response.status_code == 200, response.content
    target.refresh_from_db()
    assert target.role == "Owner"
    assert Membership.objects.get(pk=org["membership"]["id"]).role == "Admin"
    assert Organization.objects.get(pk=org["id"]).owner_membership_id == target.id
    assert Membership.objects.filter(org_id=org["id"], role="Owner", state="active").count() == 1


def parallel_calls(app_role, jobs):
    def work(job):
        try:
            with restricted(app_role):
                client, route, data = job
                response = call(client, route, data)
                return response.status_code
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        return sorted(pool.map(work, jobs))


def test_acceptance_race(signed_in, org, app_role):
    _, token = invite(signed_in, org, app_role)
    user, first = user_client()
    second = Client()
    second.force_login(user)
    jobs = [(c, "/api/v1/invitations/accept/", {"token": token}) for c in [first, second]]
    assert parallel_calls(app_role, jobs) == [200, 400]
    assert Membership.objects.filter(org_id=org["id"], user=user).count() == 1


def test_last_capacity_slot_race(signed_in, org, app_role):
    OrganizationUsage.objects.filter(org_id=org["id"]).update(member_limit=2)
    jobs = []
    for index in range(2):
        email = f"join{index}@example.test"
        _, token = invite(signed_in, org, app_role, email=email)
        _, client = user_client(email)
        jobs.append((client, "/api/v1/invitations/accept/", {"token": token}))
    assert parallel_calls(app_role, jobs) == [200, 409]
    assert OrganizationUsage.objects.get(org_id=org["id"]).active_members == 2
    assert Membership.objects.filter(org_id=org["id"], state="active").count() == 2
    assert Invitation.objects.filter(state="pending").count() == 1


def test_ownership_transfer_race(signed_in, owner, org, app_role):
    second = Client()
    second.force_login(owner)
    a, _ = seed_member(org, email="a@example.test")
    b, _ = seed_member(org, email="b@example.test")
    route = f"/api/v1/orgs/{org['id']}/transfer-ownership/"
    jobs = [
        (client, route, {"member_id": str(target.id), "password": "test-password-1234"})
        for client, target in [(signed_in, a), (second, b)]
    ]
    assert parallel_calls(app_role, jobs) == [200, 403]
    assert Membership.objects.filter(org_id=org["id"], role="Owner", state="active").count() == 1


def test_tenant_denial_and_pagination(signed_in, org, app_role):
    _, stranger = user_client("stranger@example.test")
    other = make_org(stranger, app_role, "unrelated-org")
    member, _ = seed_member(org)
    invitation, _ = invite(signed_in, org, app_role, email="pending@example.test")
    with restricted(app_role):
        assert stranger.get(f"/api/v1/orgs/{org['id']}/members/").status_code == 404
        assert stranger.get(f"/api/v1/orgs/{org['id']}/invitations/").status_code == 404
        assert (
            call(
                stranger,
                f"/api/v1/orgs/{other['id']}/members/{member.id}/",
                {"role": "Admin"},
                method="PATCH",
            ).status_code
            == 404
        )
        assert (
            call(
                stranger, f"/api/v1/orgs/{other['id']}/invitations/{invitation.id}/revoke/"
            ).status_code
            == 404
        )
        page = signed_in.get(f"/api/v1/orgs/{org['id']}/members/?limit=1").json()
        assert page["next_cursor"]
        assert (
            signed_in.get(
                f"/api/v1/orgs/{org['id']}/members/", {"cursor": page["next_cursor"]}
            ).json()["next_cursor"]
            is None
        )
        assert Invitation.objects.count() == OrganizationUsage.objects.count() == 0


def test_delivery_worker_sends_once_and_has_narrow_scope(signed_in, org, app_role, mailoutbox):
    invitation, token = invite(signed_in, org, app_role)
    with restricted(app_role):
        send_invitation(org["id"], str(invitation.email_event_id))
        send_invitation(org["id"], str(invitation.email_event_id))
        with tenant_transaction(None, uuid.uuid4(), email_event=invitation.email_event_id):
            assert Invitation.objects.count() == OutboxEvent.objects.count() == 0
    assert len(mailoutbox) == 1
    assert token in mailoutbox[0].body
    assert OutboxEvent.objects.get(pk=invitation.email_event_id).delivery_state == "sent"


def test_delivery_fail_retry_lease_and_resend_recovery(signed_in, org, app_role):
    invitation, _ = invite(signed_in, org, app_role)
    with restricted(app_role), patch("organizations.tasks.send_mail", side_effect=OSError):
        send_invitation(org["id"], str(invitation.email_event_id))
    job = OutboxEvent.objects.get(pk=invitation.email_event_id)
    assert (job.delivery_state, job.attempts) == ("queued", 1)
    job.delivery_state, job.attempts = "sending", 5
    job.next_attempt_at = timezone.now() - timedelta(seconds=1)
    job.save()
    with restricted(app_role), patch("organizations.tasks.send_invitation.apply_async") as enqueue:
        dispatch_invitations()
        assert enqueue.call_count == 1
    with restricted(app_role):
        send_invitation(org["id"], str(job.id))
        response = call(signed_in, f"/api/v1/orgs/{org['id']}/invitations/{invitation.id}/resend/")
    assert response.status_code == 200
    job.refresh_from_db()
    assert job.delivery_state == "failed"
    invitation.refresh_from_db()
    assert invitation.email_event_id != job.id


def test_revoked_email_job_and_broker_outage(signed_in, org, app_role, mailoutbox):
    invitation, _ = invite(signed_in, org, app_role)
    Invitation.objects.filter(pk=invitation.id).update(state="revoked")
    with restricted(app_role):
        send_invitation(org["id"], str(invitation.email_event_id))
    assert not mailoutbox
    assert OutboxEvent.objects.get(pk=invitation.email_event_id).delivery_state == "obsolete"
    from organizations.team import publish_email

    with patch("organizations.tasks.send_invitation.apply_async", side_effect=ConnectionError):
        publish_email(org["id"], str(invitation.email_event_id))


def test_rate_limit_and_atomic_rollback(signed_in, org, app_role):
    with restricted(app_role), patch("organizations.team.check_rate", return_value=30):
        response = call(
            signed_in,
            f"/api/v1/orgs/{org['id']}/invitations/",
            {"email": "member@example.test", "role": "Member"},
        )
        assert response.status_code == 429
        assert response["Retry-After"] == "30"
    count = AuditEvent.objects.count()
    from django.db import IntegrityError

    with (
        restricted(app_role),
        patch("organizations.team.OutboxEvent.objects.create", side_effect=IntegrityError),
    ):
        response = call(
            signed_in,
            f"/api/v1/orgs/{org['id']}/invitations/",
            {"email": "member@example.test", "role": "Member"},
        )
    assert response.status_code == 503
    assert Invitation.objects.count() == 0
    assert AuditEvent.objects.count() == count


def test_direct_role_update(owner, org, app_role):
    member, _ = seed_member(org)
    with restricted(app_role), tenant_transaction(owner.id, org["id"]):
        Membership.objects.filter(pk=member.id).update(role="Admin")


def test_invitation_preview_is_private_and_does_not_consume(signed_in, org, app_role):
    invitation, token = invite(signed_in, org, app_role)
    _, client = user_client()
    _, wrong = user_client("wrong@example.test")
    with restricted(app_role):
        response = call(client, "/api/v1/invitations/preview/", {"token": token})
        assert response.status_code == 200, response.content
        assert response.json()["role"] == "Member"
        assert call(wrong, "/api/v1/invitations/preview/", {"token": token}).status_code == 400
    invitation.refresh_from_db()
    assert invitation.state == "pending"
