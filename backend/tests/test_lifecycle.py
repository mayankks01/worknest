import hashlib
import io
import json
import sys
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import connection, connections
from django.utils import timezone

from activity.models import Notification
from collab.models import Attachment, Comment
from lifecycle.models import Export, Maintenance
from lifecycle.tasks import generate, maintain
from organizations.context import tenant_transaction
from organizations.models import (
    AuditEvent,
    Membership,
    Organization,
    OrganizationUsage,
    OutboxEvent,
)
from tests import test_organizations as base
from tests import test_projects as projects
from tests.test_team import seed_member

pytestmark = pytest.mark.django_db(transaction=True)
app_role = base.app_role
owner = base.owner
signed_in = base.signed_in
org = projects.org
work = projects.work
call = projects.call


@pytest.fixture(autouse=True)
def services():
    with (
        patch("lifecycle.tasks.publish"),
        patch("lifecycle.views.publish"),
        patch("activity.tasks.publish"),
        patch("lifecycle.views.throttle", return_value=None),
    ):
        yield


def revoke(member_id):
    def update():
        try:
            assert Membership.objects.filter(pk=member_id).update(state="removed") == 1
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(update).result()


def raise_worker_error(*args):
    raise RuntimeError("Unexpected worker failure") from sys.exception()


def run(role, function, row):
    with base.restricted(role):
        function(str(row.org_id), str(row.id))
    row.refresh_from_db()


def request_export(client, role, org, key=None):
    response = call(client, role, org["id"] + "/exports/", {}, "POST", key=key or uuid.uuid4().hex)
    assert response.status_code == 202, response.content
    return Export.objects.get(pk=response.json()["export"]["id"])


def test_search_and_counts_are_live_scoped(signed_in, app_role, org, work):
    member, client = seed_member(org)
    task = projects.task(
        signed_in,
        app_role,
        org,
        work,
        title="Find the needle",
        description="a detailed phrase",
        status="Blocked",
    )
    root = org["id"] + "/"
    assert call(client, app_role, root + "search/?q=needle").json()["count"] == 0
    assert call(client, app_role, root + "dashboard/").json()["counts"]["blocked"] == 0
    projects.add(signed_in, app_role, org, work, member)
    assert (
        call(client, app_role, root + "search/?q=detailed").json()["results"][0]["id"] == task["id"]
    )
    assert call(client, app_role, root + "dashboard/").json()["counts"]["blocked"] == 1
    other = base.make_org(signed_in, app_role, "other-search")
    assert call(client, app_role, other["id"] + "/search/?q=needle").status_code == 404
    Task = projects.Task
    Task.objects.filter(pk=task["id"]).update(deleted_at=timezone.now())
    assert call(client, app_role, root + "search/?q=needle").json()["count"] == 0
    assert call(client, app_role, root + "search/?q=n").status_code == 400


def test_export_replay_snapshot_and_live_download(signed_in, app_role, org, work):
    task = projects.task(signed_in, app_role, org, work, title="Export me")
    key = uuid.uuid4().hex
    row = request_export(signed_in, app_role, org, key)
    assert request_export(signed_in, app_role, org, key).id == row.id
    objects = {}

    def put(key, content):
        assert not connection.in_atomic_block
        objects[key] = content.read()

    with patch("lifecycle.tasks.storage.put", side_effect=put):
        run(app_role, generate, row)
        run(app_role, generate, row)
    assert row.state == "ready" and len(objects) == 1
    archive = objects[row.object_key]
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        manifest = json.loads(bundle.read("workspace.json"))
    assert [t["id"] for t in manifest["tasks"]] == [task["id"]]
    path = f"{org['id']}/exports/{row.id}/download/"
    with patch("lifecycle.views.storage.client") as client:
        client.return_value.get_object.return_value = {"Body": io.BytesIO(archive)}
        response = call(signed_in, app_role, path)
        assert response.status_code == 200
        assert b"".join(response.streaming_content) == archive
        response.close()
    Export.objects.filter(pk=row.id).update(expires_at=timezone.now() - timedelta(seconds=1))
    assert call(signed_in, app_role, path).status_code == 409


@pytest.mark.parametrize("when", ["before", "during", "download"])
def test_export_rechecks_admin_permission(signed_in, app_role, org, work, when):
    member, client = seed_member(org, role="Admin")
    row = request_export(client, app_role, org)
    if when == "before":
        Membership.objects.filter(pk=member.id).update(role="Member")

    def put(key, content):
        if when == "during":
            revoke(member.id)

    with patch("lifecycle.tasks.storage.put", side_effect=put):
        run(app_role, generate, row)
    if when != "download":
        assert row.state == "obsolete"
    else:
        assert row.state == "ready"
        Membership.objects.filter(pk=member.id).update(role="Viewer")
        assert call(client, app_role, f"{org['id']}/exports/{row.id}/download/").status_code == 403


def test_export_download_rechecks_after_storage(signed_in, app_role, org):
    member, client = seed_member(org, role="Admin")
    row = request_export(client, app_role, org)
    objects = {}
    with patch(
        "lifecycle.tasks.storage.put",
        side_effect=lambda key, stream: objects.update({key: stream.read()}),
    ):
        run(app_role, generate, row)

    def read(**kwargs):
        assert not connection.in_atomic_block
        revoke(member.id)
        return {"Body": io.BytesIO(objects[row.object_key])}

    with patch("lifecycle.views.storage.client") as s3:
        s3.return_value.get_object.side_effect = read
        response = call(client, app_role, f"{org['id']}/exports/{row.id}/download/")
    assert response.status_code == 404


def test_export_failures_retry_and_wrong_tenant(signed_in, app_role, org):
    row = request_export(signed_in, app_role, org)
    other = base.make_org(signed_in, app_role, "other-export")
    with base.restricted(app_role):
        generate(other["id"], str(row.id))
    row.refresh_from_db()
    assert row.attempts == 0
    with patch("lifecycle.tasks.storage.put", side_effect=OSError("private failure")):
        for _ in range(5):
            Export.objects.filter(pk=row.id).update(next_attempt_at=timezone.now())
            run(app_role, generate, row)
    assert (
        row.state == "failed" and row.attempts == 5 and row.last_error == "PROCESSING_UNAVAILABLE"
    )
    response = call(signed_in, app_role, f"{org['id']}/exports/{row.id}/", {}, "POST")
    assert response.status_code == 200
    assert response.json()["export"]["state"] == "queued"


def deletion(client, role, org, action="schedule", password="test-password-1234"):
    return call(
        client,
        role,
        org["id"] + "/deletion/",
        {"password": password, "confirmation": org["slug"], "action": action},
        "POST",
    )


def test_deletion_requires_owner_reauth_and_allows_cancel(signed_in, app_role, org, work):
    _, admin = seed_member(org, role="Admin")
    assert deletion(admin, app_role, org).status_code == 403
    assert deletion(signed_in, app_role, org, password="wrong").status_code == 403
    response = deletion(signed_in, app_role, org)
    assert response.status_code == 200, response.content
    assert response.json()["state"] == "pending_delete"
    due = response.json()["deletion_due_at"]
    assert deletion(signed_in, app_role, org).json()["deletion_due_at"] == due
    assert call(signed_in, app_role, org["id"] + "/projects/").status_code == 200
    with base.restricted(app_role):
        assert signed_in.get("/api/v1/orgs/").json()["results"]
    assert deletion(signed_in, app_role, org, "cancel").json()["state"] == "active"
    assert Organization.objects.get(pk=org["id"]).deletion_due_at is None


def test_deadline_hides_access_without_worker_and_purge_is_scoped(signed_in, app_role, org, work):
    projects.task(signed_in, app_role, org, work)
    other = base.make_org(signed_in, app_role, "keep-me")
    row = Maintenance.objects.get(org_id=org["id"])
    Organization.objects.filter(pk=org["id"]).update(
        state="pending_delete", deletion_due_at=timezone.now() - timedelta(hours=1)
    )
    assert call(signed_in, app_role, org["id"] + "/projects/").status_code == 404
    assert deletion(signed_in, app_role, org, "cancel").status_code == 404
    with (
        patch("lifecycle.tasks.objects", return_value=iter([])),
        patch("lifecycle.tasks.fail", side_effect=raise_worker_error),
    ):
        run(app_role, maintain, row)
    assert row.state == "completed", row.last_error
    assert row.completed_at and not Organization.objects.filter(pk=org["id"]).exists()
    assert Organization.objects.filter(pk=other["id"]).exists()
    assert not AuditEvent.objects.filter(org_id=org["id"]).exists()
    assert not OutboxEvent.objects.filter(org_id=org["id"]).exists()


def test_storage_failure_preserves_tenant_and_receipt_retries(signed_in, app_role, org):
    row = Maintenance.objects.get(org_id=org["id"])
    Organization.objects.filter(pk=org["id"]).update(
        state="pending_delete", deletion_due_at=timezone.now() - timedelta(hours=1)
    )
    with patch("lifecycle.tasks.objects", side_effect=OSError("secret")):
        run(app_role, maintain, row)
    assert row.state == "queued" and row.attempts == 1
    assert Organization.objects.filter(pk=org["id"]).exists()


def test_retention_purges_old_task_but_keeps_new_data(signed_in, app_role, org, work):
    old = projects.task(signed_in, app_role, org, work)
    fresh = projects.task(signed_in, app_role, org, work)
    projects.Task.objects.filter(pk=old["id"]).update(
        deleted_at=timezone.now() - timedelta(days=31)
    )
    row = Maintenance.objects.get(org_id=org["id"])
    with (
        patch("lifecycle.tasks.objects", return_value=iter([])),
        patch("lifecycle.tasks.fail", side_effect=raise_worker_error),
    ):
        run(app_role, maintain, row)
    assert row.checked_at and row.state == "queued", row.last_error
    assert not projects.Task.objects.filter(pk=old["id"]).exists()
    assert projects.Task.objects.filter(pk=fresh["id"]).exists()
    with base.restricted(app_role), tenant_transaction(None, uuid.uuid4(), lifecycle_id=row.id):
        assert not projects.Task.objects.exists()
    with base.restricted(app_role):
        assert not Maintenance.objects.exists()


def attachment(org, task):
    key = uuid.uuid4()
    return Attachment.objects.create(
        id=key,
        org_id=org["id"],
        task_id=task["id"],
        author_id=org["membership"]["id"],
        filename="brief.txt",
        extension=".txt",
        size=4,
        reserved_bytes=4,
        sha256=hashlib.sha256(b"safe").hexdigest(),
        object_key=f"orgs/{org['id']}/attachments/{key}",
        state="available",
        scanned_at=timezone.now(),
        request_key=uuid.uuid4().hex,
        payload_hash="a" * 64,
        request_expires_at=timezone.now() + timedelta(hours=24),
    )


def test_export_includes_only_safe_files_and_comments(signed_in, app_role, org, work):
    task = projects.task(signed_in, app_role, org, work)
    file = attachment(org, task)
    blocked = attachment(org, task)
    Attachment.objects.filter(pk=blocked.id).update(state="queued", scanned_at=None)
    Comment.objects.create(
        org_id=org["id"],
        task_id=task["id"],
        author_id=org["membership"]["id"],
        body="Keep this discussion",
    )
    row = request_export(signed_in, app_role, org)
    saved = {}
    with (
        patch("lifecycle.tasks.storage.read", return_value=io.BytesIO(b"safe")) as read,
        patch(
            "lifecycle.tasks.storage.put",
            side_effect=lambda k, stream: saved.update({k: stream.read()}),
        ),
    ):
        run(app_role, generate, row)
    read.assert_called_once_with(file.object_key, 4, file.sha256)
    assert row.state == "ready"
    with zipfile.ZipFile(io.BytesIO(saved[row.object_key])) as bundle:
        assert bundle.read(f"attachments/{file.id}.txt") == b"safe"
        assert len(bundle.namelist()) == 2
        assert (
            json.loads(bundle.read("workspace.json"))["comments"][0]["body"]
            == "Keep this discussion"
        )


def test_retention_removes_objects_audit_and_notifications_at_boundaries(
    signed_in, app_role, org, work
):
    old = projects.task(signed_in, app_role, org, work)
    fresh = projects.task(signed_in, app_role, org, work)
    file = attachment(org, old)
    OrganizationUsage.objects.filter(org_id=org["id"]).update(storage_bytes=4)
    projects.Task.objects.filter(pk=old["id"]).update(
        deleted_at=timezone.now() - timedelta(days=31)
    )
    source = OutboxEvent.objects.filter(topic="task.created").first()
    Notification.objects.create(
        org_id=org["id"],
        recipient_id=org["membership"]["id"],
        task_id=fresh["id"],
        source=source,
        kind="assignment",
        created_at=timezone.now() - timedelta(days=31),
    )
    stale_audit = AuditEvent.objects.get(action="organization.created")
    AuditEvent.objects.filter(pk=stale_audit.id).update(
        created_at=timezone.now() - timedelta(days=91)
    )
    export = request_export(signed_in, app_role, org)
    Export.objects.filter(pk=export.id).update(
        state="ready",
        expires_at=timezone.now() - timedelta(seconds=1),
        object_key=f"orgs/{org['id']}/exports/old.zip",
    )
    row = Maintenance.objects.get(org_id=org["id"])
    removed = []

    def remove(key):
        assert not connection.in_atomic_block
        removed.append(key)

    with (
        patch("lifecycle.tasks.objects", return_value=iter([])),
        patch("lifecycle.tasks.storage.delete", side_effect=remove),
        patch("lifecycle.tasks.fail", side_effect=raise_worker_error),
    ):
        run(app_role, maintain, row)
    assert file.object_key in removed and len(removed) == 2
    assert not Attachment.objects.filter(pk=file.id).exists()
    assert not AuditEvent.objects.filter(pk=stale_audit.id).exists()
    assert not Notification.objects.exists()
    assert OrganizationUsage.objects.get(org_id=org["id"]).storage_bytes == 0
    assert Export.objects.get(pk=export.id).state == "expired"
    assert projects.Task.objects.filter(pk=fresh["id"]).exists()


def test_purge_never_deletes_objects_from_another_prefix(signed_in, app_role, org):
    row = Maintenance.objects.get(org_id=org["id"])
    Organization.objects.filter(pk=org["id"]).update(
        state="pending_delete", deletion_due_at=timezone.now() - timedelta(hours=1)
    )
    ours = f"orgs/{org['id']}/exports/old.zip"
    foreign = f"orgs/{uuid.uuid4()}/attachments/secret"
    with (
        patch("lifecycle.tasks.storage.client") as s3,
        patch("lifecycle.tasks.storage.delete") as remove,
        patch("lifecycle.tasks.fail", side_effect=raise_worker_error),
    ):
        s3.return_value.list_objects_v2.side_effect = [
            {"Contents": [{"Key": ours}, {"Key": foreign}]},
            {"Contents": []},
        ]
        run(app_role, maintain, row)
    assert row.state == "completed"
    remove.assert_called_once_with(ours)


def test_search_cursor_and_archived_visibility(signed_in, app_role, org, work):
    for _ in range(2):
        projects.task(signed_in, app_role, org, work, title="Needle")
    path = org["id"] + "/search/?q=needle&limit=1"
    first = call(signed_in, app_role, path).json()
    second = call(signed_in, app_role, path + "&cursor=" + first["next_cursor"]).json()
    assert first["results"][0]["id"] != second["results"][0]["id"]
    assert (
        call(
            signed_in, app_role, org["id"] + "/search/?q=other&cursor=" + first["next_cursor"]
        ).status_code
        == 400
    )
    projects.Project.objects.filter(pk=work["id"]).update(archived_at=timezone.now())
    assert call(signed_in, app_role, path).json()["count"] == 0
    assert call(signed_in, app_role, path + "&state=archived").json()["count"] == 2


@pytest.mark.parametrize("days", [2, -1])
def test_invitation_delivery_respects_deletion_window(signed_in, app_role, org, mailoutbox, days):
    from organizations.tasks import send_invitation
    from tests.test_team import invite

    with (
        patch("organizations.team.check_rate", return_value=0),
        patch("organizations.team.publish_email"),
    ):
        invitation, _ = invite(signed_in, org, app_role)
    Organization.objects.filter(pk=org["id"]).update(
        state="pending_delete", deletion_due_at=timezone.now() + timedelta(days=days)
    )
    with base.restricted(app_role):
        send_invitation(org["id"], str(invitation.email_event_id))
    assert len(mailoutbox) == (1 if days > 0 else 0)
