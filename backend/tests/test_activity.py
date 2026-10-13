import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from activity.models import Notification, Reconciliation
from activity.tasks import consume, dispatch, reconcile, send_assignment
from activity.tasks import publish as real_publish
from organizations.context import tenant_transaction
from organizations.models import AuditEvent, Membership, OrganizationUsage, OutboxEvent
from projects.models import ProjectMembership, Task
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
def broker():
    with patch("activity.tasks.publish"):
        yield


@pytest.fixture
def assigned(signed_in, app_role, org, work):
    member, client = seed_member(org)
    projects.add(signed_in, app_role, org, work, member)
    task = projects.task(signed_in, app_role, org, work, assignee_id=str(member.id))
    event = OutboxEvent.objects.get(org_id=org["id"], topic="task.created")
    return member, client, task, event


def run(app_role, function, row):
    with base.restricted(app_role):
        function(str(row.org_id), str(row.id))
    row.refresh_from_db()


def test_assignment_idempotency_inbox_email_and_no_sensitive_body(app_role, org, assigned):
    member, client, task, row = assigned
    run(app_role, consume, row)
    assert row.delivery_state == "processed"
    run(app_role, consume, row)
    assert Notification.objects.count() == 1
    notification = Notification.objects.get()
    assert notification.recipient_id == member.id and notification.email_state == "queued"
    listing = call(client, app_role, org["id"] + "/notifications/").json()
    assert listing["unread_count"] == 1 and listing["results"][0]["task_title"] == task["title"]
    assert (
        call(
            client,
            app_role,
            f"{org['id']}/notifications/{notification.id}/",
            {"read": True},
            "PATCH",
        ).status_code
        == 200
    )
    assert call(client, app_role, org["id"] + "/notifications/?unread=true").json()["results"] == []
    sent = []

    def smtp(message, **kwargs):
        assert not connection.in_atomic_block
        sent.append(message)
        return 1

    with patch("activity.tasks.EmailMessage.send", autospec=True, side_effect=smtp):
        run(app_role, send_assignment, notification)
        run(app_role, send_assignment, notification)
    assert notification.email_state == "sent" and len(sent) == 1
    assert task["title"] not in sent[0].body and member.user.email == sent[0].to[0]
    assert sent[0].extra_headers["Message-ID"] == notification.message_id


def test_worker_duplicates_make_one_inbox(app_role, assigned):
    *_, row = assigned

    def deliver(_):
        with base.restricted(app_role):
            consume(str(row.org_id), str(row.id))

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(deliver, range(2)))
    assert Notification.objects.count() == 1


def test_comment_targets_author_assignee_and_participants(signed_in, app_role, org, work, assigned):
    member, client, task, _ = assigned
    third, third_client = seed_member(org, email="participant@example.test")
    projects.add(signed_in, app_role, org, work, third)
    route = f"{org['id']}/tasks/{task['id']}/comments/"
    assert (
        call(
            third_client, app_role, route, {"body": "Private note never goes into an event"}, "POST"
        ).status_code
        == 201
    )
    assert call(client, app_role, route, {"body": "Response"}, "POST").status_code == 201
    row = OutboxEvent.objects.filter(topic="comment.created").order_by("-created_at").first()
    assert set(row.payload["recipients"]) == {str(third.id), org["membership"]["id"]}
    run(app_role, consume, row)
    assert Notification.objects.filter(source=row).count() == 2
    assert set(Notification.objects.values_list("email_state", flat=True)) == {"skipped"}
    assert "Response" not in str(row.payload)


@pytest.mark.parametrize("change", ["remove", "unlink", "delete_task", "reassign"])
def test_live_access_rechecked_before_delivery_and_open(
    signed_in, app_role, org, work, assigned, change
):
    member, client, task, row = assigned
    run(app_role, consume, row)
    item = Notification.objects.get()
    if change == "remove":
        Membership.objects.filter(pk=member.id).update(state="removed")
    elif change == "unlink":
        ProjectMembership.objects.filter(membership=member).update(state="removed")
    elif change == "delete_task":
        Task.objects.filter(pk=task["id"]).update(deleted_at=timezone.now())
    else:
        Task.objects.filter(pk=task["id"]).update(assignee_id=None)
    with patch("activity.tasks.EmailMessage.send") as smtp:
        run(app_role, send_assignment, item)
        smtp.assert_not_called()
    assert item.email_state == "obsolete"
    listing = call(client, app_role, org["id"] + "/notifications/")
    if change == "remove":
        assert listing.status_code == 404
    elif change != "reassign":
        assert listing.json()["results"] == [] and listing.json()["unread_count"] == 0
        assert (
            call(
                client, app_role, f"{org['id']}/notifications/{item.id}/", {"read": True}, "PATCH"
            ).status_code
            == 404
        )


def test_preferences_scoped_and_rechecked(signed_in, app_role, org, assigned):
    member, client, task, row = assigned
    run(app_role, consume, row)
    pref = f"{org['id']}/notifications/preferences/"
    assert call(client, app_role, pref, {"assignment_email": False}, "PATCH").status_code == 200
    assert call(signed_in, app_role, pref).json()["assignment_email"] is True
    item = Notification.objects.get()
    with patch("activity.tasks.EmailMessage.send") as smtp:
        run(app_role, send_assignment, item)
        smtp.assert_not_called()
    assert item.email_state == "obsolete"
    viewer, view_client = seed_member(org, "Viewer", "viewer@example.test")
    assert (
        call(view_client, app_role, pref, {"assignment_email": False}, "PATCH").status_code == 200
    )
    assert call(client, app_role, pref, {"assignment_email": "false"}, "PATCH").status_code == 400


def test_retry_five_attempts_and_admin_recovery(signed_in, app_role, org, assigned):
    member, client, task, row = assigned
    run(app_role, consume, row)
    item = Notification.objects.get()
    with patch("activity.tasks.EmailMessage.send", side_effect=OSError("private provider details")):
        for _ in range(5):
            Notification.objects.filter(pk=item.id).update(next_attempt_at=timezone.now())
            run(app_role, send_assignment, item)
    assert (
        item.email_state == "failed"
        and item.attempts == 5
        and item.last_error == "EMAIL_UNAVAILABLE"
    )
    path = f"{org['id']}/operations/email/{item.id}/retry/"
    assert call(client, app_role, path, {}, "POST").status_code == 403
    assert call(signed_in, app_role, path, {}, "POST").status_code == 200
    item.refresh_from_db()
    assert item.attempts == 0 and item.email_state == "queued"
    with patch("activity.tasks.EmailMessage.send", return_value=1):
        run(app_role, send_assignment, item)
    assert item.email_state == "sent"
    assert "private provider details" not in str(
        list(AuditEvent.objects.values_list("summary", flat=True))
    )


def test_outbox_failure_rollback_recovery_and_dispatch(app_role, assigned):
    *_, row = assigned
    with patch(
        "activity.tasks.Notification.objects.get_or_create", side_effect=RuntimeError("secret")
    ):
        run(app_role, consume, row)
    assert (
        row.delivery_state == "queued" and row.attempts == 1 and not Notification.objects.exists()
    )
    # Make the job unambiguously overdue across host and Docker database clocks.
    OutboxEvent.objects.filter(pk=row.id).update(
        next_attempt_at=timezone.now() - timedelta(minutes=1)
    )
    with patch("activity.tasks.publish") as publication, base.restricted(app_role):
        dispatch()
        assert ("event", row.org_id, row.id) in [call.args for call in publication.call_args_list]
    run(app_role, consume, row)
    assert Notification.objects.count() == 1 and row.delivery_state == "processed"


def test_audit_and_operations_permissions_and_append_only(signed_in, app_role, org, assigned):
    member, client, _, _ = assigned
    for path in ("audit/", "operations/"):
        assert call(client, app_role, org["id"] + "/" + path).status_code == 403
        assert call(signed_in, app_role, org["id"] + "/" + path).status_code == 200
    audit = call(signed_in, app_role, org["id"] + "/audit/?limit=1").json()
    assert len(audit["results"]) == 1 and audit["next_cursor"]
    assert "summary" not in audit["results"][0]
    with base.restricted(app_role), tenant_transaction(member.user_id, org["id"]):
        assert AuditEvent.objects.update(action="forged") == 0
        assert AuditEvent.objects.all().delete()[0] == 0


def test_job_context_cannot_cross_org_or_leak_after_transaction(
    signed_in, owner, app_role, org, assigned
):
    member, client, _, row = assigned
    other = base.make_org(signed_in, app_role, "another-space")
    with base.restricted(app_role):
        consume(other["id"], str(row.id))
        assert Notification.objects.count() == 0
        with tenant_transaction(None, other["id"], activity_id=row.id):
            assert not Task.objects.exists()
        with tenant_transaction(None, org["id"], activity_id=row.id):
            assert set(Task.objects.values_list("org_id", flat=True)) == {uuid.UUID(org["id"])}
        assert not Task.objects.exists()
    assert call(client, app_role, other["id"] + "/notifications/").status_code == 404
    run(app_role, consume, row)
    item = Notification.objects.get()
    other_job = Reconciliation.objects.get(org_id=other["id"]).id
    with (
        base.restricted(app_role),
        tenant_transaction(None, other["id"], activity_id=other_job),
        pytest.raises(IntegrityError),
        transaction.atomic(),
    ):
        Notification.objects.create(
            org_id=other["id"],
            recipient_id=member.id,
            task_id=item.task_id,
            source_id=row.id,
            kind="comment",
        )
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_reconciliation_repairs_counters_and_cleans_only_old_scoped_orphans(
    app_role, org, assigned
):
    *_, event = assigned
    row = Reconciliation.objects.get(org_id=org["id"])
    OrganizationUsage.objects.filter(org_id=org["id"]).update(active_tasks=7, storage_bytes=42)
    prefix = f"orgs/{org['id']}/attachments/"
    old = timezone.now() - timedelta(days=2)
    orphan = prefix + str(uuid.uuid4())
    foreign = f"orgs/{uuid.uuid4()}/attachments/{uuid.uuid4()}"
    fresh = prefix + str(uuid.uuid4())

    def listing(**kwargs):
        assert not connection.in_atomic_block
        return {
            "Contents": [
                {"Key": orphan, "LastModified": old},
                {"Key": foreign, "LastModified": old},
                {"Key": fresh, "LastModified": timezone.now()},
            ]
        }

    with (
        patch("activity.tasks.storage.client") as client,
        patch("activity.tasks.storage.delete") as delete,
    ):
        client.return_value.list_objects_v2.side_effect = listing
        run(app_role, reconcile, row)
        delete.assert_called_once_with(orphan)
    assert row.checked_at and row.state == "queued" and row.orphan_count == 1
    usage = OrganizationUsage.objects.get(org_id=org["id"])
    assert usage.active_tasks == 1 and usage.storage_bytes == 0
    assert set(row.corrected_counters) == {"active_tasks", "storage_bytes"}


def test_reconciliation_failure_is_durable(app_role, org):
    row = Reconciliation.objects.get(org_id=org["id"])
    with patch("activity.tasks.storage.client", side_effect=OSError("storage offline")):
        for _ in range(5):
            Reconciliation.objects.filter(pk=row.id).update(next_attempt_at=timezone.now())
            run(app_role, reconcile, row)
    assert row.state == "failed" and row.attempts == 5


def test_recipient_and_cursor_scope(signed_in, app_role, org, assigned):
    member, client, task, row = assigned
    run(app_role, consume, row)
    item = Notification.objects.get()
    other, other_client = seed_member(org, email="another@example.test")
    assert call(other_client, app_role, org["id"] + "/notifications/").json()["results"] == []
    assert (
        call(
            other_client, app_role, f"{org['id']}/notifications/{item.id}/", {"read": True}, "PATCH"
        ).status_code
        == 404
    )
    first = call(signed_in, app_role, org["id"] + "/audit/?limit=1").json()
    second = call(signed_in, app_role, org["id"] + "/audit/?cursor=" + first["next_cursor"]).json()
    assert first["results"][0]["id"] not in [entry["id"] for entry in second["results"]]
    assert (
        call(
            client, app_role, org["id"] + "/notifications/?cursor=" + first["next_cursor"]
        ).status_code
        == 400
    )


def test_broker_outage_does_not_rollback_business_write(signed_in, app_role, org, work):
    member, _ = seed_member(org)
    projects.add(signed_in, app_role, org, work, member)
    with (
        patch("activity.tasks.publish", side_effect=real_publish),
        patch("activity.tasks.consume.apply_async", side_effect=OSError("broker offline")),
    ):
        item = projects.task(signed_in, app_role, org, work, assignee_id=str(member.id))
    row = OutboxEvent.objects.get(topic="task.created")
    assert Task.objects.filter(pk=item["id"]).exists() and row.delivery_state == "queued"
    run(app_role, consume, row)
    assert Notification.objects.count() == 1
