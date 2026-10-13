import secrets
import uuid
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMessage
from django.db import connection, transaction
from django.db.models import Sum
from django.utils import timezone

from collab import storage
from collab.models import Attachment, Comment
from organizations.context import tenant_transaction
from organizations.models import Membership, Organization, OrganizationUsage, OutboxEvent
from organizations.team import event
from projects.models import Project, Task

from .models import Notification, Reconciliation
from .services import eligible_task, email_allowed


def publish(kind, org_id, job_id):
    try:
        {"event": consume, "email": send_assignment, "reconcile": reconcile}[kind].apply_async(
            args=[str(org_id), str(job_id)], retry=False
        )
    except Exception:
        pass  # PostgreSQL is the durable queue; beat retries missed publication.


@shared_task(name="activity.dispatch")
def dispatch():
    with connection.cursor() as cursor:
        cursor.execute("SELECT org_id, job_id, kind FROM public.worknest_pending_activity_jobs()")
        routes = cursor.fetchall()
    for org_id, job_id, kind in routes:
        publish(kind, org_id, job_id)


def claim(model, org_id, job_id, field, queued, running):
    with tenant_transaction(None, org_id, activity_id=job_id):
        OrganizationUsage.objects.select_for_update().filter(org_id=org_id).first()
        row = model.objects.select_for_update().filter(org_id=org_id, pk=job_id).first()
        now = timezone.now()
        if not row or getattr(row, field) not in {queued, running} or row.next_attempt_at > now:
            return None
        if row.attempts >= 5:
            setattr(row, field, "failed")
            row.last_error = "RETRY_LIMIT"
            row.save(update_fields=[field, "last_error"])
            return None
        row.lease = uuid.uuid4()
        row.attempts += 1
        row.next_attempt_at = now + timedelta(minutes=5)
        setattr(row, field, running)
        row.save()
        return row.lease


def fail(model, org_id, job_id, lease, field, queued, code):
    with tenant_transaction(None, org_id, activity_id=job_id):
        row = (
            model.objects.select_for_update().filter(org_id=org_id, pk=job_id, lease=lease).first()
        )
        if row:
            setattr(row, field, "failed" if row.attempts >= 5 else queued)
            row.last_error, row.lease = code, None
            row.next_attempt_at = timezone.now() + timedelta(
                seconds=30 * 2**row.attempts + secrets.randbelow(15)
            )
            row.save()


@shared_task(name="activity.consume", soft_time_limit=90, time_limit=120)
def consume(org_id, job_id):
    lease = claim(OutboxEvent, org_id, job_id, "delivery_state", "queued", "processing")
    if not lease:
        return
    try:
        with tenant_transaction(None, org_id, activity_id=job_id):
            OrganizationUsage.objects.select_for_update().get(org_id=org_id)
            row = OutboxEvent.objects.select_for_update().get(org_id=org_id, pk=job_id)
            if row.lease != lease:
                return
            payload = row.payload
            if payload.get("kind") in {"assignment", "comment"}:
                # Topic validation prevents unrelated outbox payloads from becoming alerts.
                kind = payload["kind"]
                assert row.topic in (
                    {"task.created", "task.updated"}
                    if kind == "assignment"
                    else {"comment.created"}
                )
                if (
                    kind == "comment"
                    and not Comment.objects.filter(
                        org_id=org_id, pk=payload["resource_id"], deleted_at__isnull=True
                    ).exists()
                ):
                    recipients = []
                else:
                    recipients = payload["recipients"]
                for member in Membership.objects.filter(
                    org_id=org_id, id__in=recipients
                ).select_related("user"):
                    task = eligible_task(org_id, member, payload["task_id"])
                    if not task or (
                        kind == "assignment"
                        and (task.assignee_id != member.id or member.role == "Viewer")
                    ):
                        continue
                    item, created = Notification.objects.get_or_create(
                        org_id=org_id, recipient=member, task=task, source=row, kind=kind
                    )
                    if created and kind == "assignment" and email_allowed(item):
                        item.email_state = "queued"
                        item.message_id = f"<worknest-{item.id}@notifications.worknest.invalid>"
                        item.save(update_fields=["email_state", "message_id"])
                        transaction.on_commit(
                            lambda item_id=item.id: publish("email", org_id, item_id)
                        )
            row.delivery_state, row.lease, row.last_error = "processed", None, ""
            row.dispatched_at = timezone.now()
            row.save()
    except Exception:
        fail(OutboxEvent, org_id, job_id, lease, "delivery_state", "queued", "EVENT_FAILED")


@shared_task(name="activity.send_assignment", soft_time_limit=90, time_limit=120)
def send_assignment(org_id, job_id):
    lease = claim(Notification, org_id, job_id, "email_state", "queued", "sending")
    if not lease:
        return
    try:
        with tenant_transaction(None, org_id, activity_id=job_id):
            OrganizationUsage.objects.select_for_update().get(org_id=org_id)
            item = Notification.objects.select_for_update().get(org_id=org_id, pk=job_id)
            if item.lease != lease:
                return
            eligible = email_allowed(item)
            if not eligible:
                item.email_state, item.lease, item.last_error = "obsolete", None, ""
                item.save()
                return
            member, task = eligible
            recipient = member.user.email
            link = (
                f"{settings.PUBLIC_APP_URL}/orgs/{org_id}/projects/{task.project_id}?task={task.id}"
            )
            message_id = item.message_id
        # Deliberately omit titles, descriptions, comments and member names from SMTP.
        # A revocation racing SMTP cannot recall accepted mail; the link always reauthorizes.
        message = EmailMessage(
            "You have a new task assignment in WorkNest",
            f"A task has been assigned to you. Sign in to view it:\n\n{link}\n\n"
            "Manage assignment emails in this organization's Notifications page.",
            settings.DEFAULT_FROM_EMAIL,
            [recipient],
            headers={"Message-ID": message_id},
        )
        if message.send(fail_silently=False) != 1:
            raise OSError("SMTP did not accept the message")
        with tenant_transaction(None, org_id, activity_id=job_id):
            Notification.objects.filter(org_id=org_id, pk=job_id, lease=lease).update(
                email_state="sent", sent_at=timezone.now(), lease=None, last_error=""
            )
    except Exception:
        fail(Notification, org_id, job_id, lease, "email_state", "queued", "EMAIL_UNAVAILABLE")


def reconcile_objects(org_id, job_id, cursor):
    prefix = f"orgs/{org_id}/attachments/"
    kwargs = {"Bucket": settings.S3_BUCKET, "Prefix": prefix, "MaxKeys": 1000}
    if cursor:
        kwargs["ContinuationToken"] = cursor
    result = storage.client().list_objects_v2(**kwargs)
    removed = 0
    for obj in result.get("Contents", []):
        key = obj["Key"]
        if not key.startswith(prefix) or obj["LastModified"] > timezone.now() - timedelta(hours=24):
            continue
        try:
            file_id = uuid.UUID(key[len(prefix) :])
        except ValueError:
            continue
        if key != prefix + str(file_id):
            continue
        # Caller passes a scoped job, but network calls never run in its transaction.
        with tenant_transaction(None, org_id, activity_id=job_id):
            OrganizationUsage.objects.select_for_update().get(org_id=org_id)
            row = Attachment.objects.filter(org_id=org_id, pk=file_id).first()
            removable = not row or (
                row.reserved_bytes == 0
                and not row.cleanup_pending
                and row.state in {"deleted", "rejected", "failed"}
            )
        if removable:
            storage.delete(key)
            removed += 1
    return result.get("NextContinuationToken", ""), removed


@shared_task(name="activity.reconcile", soft_time_limit=90, time_limit=120)
def reconcile(org_id, job_id):
    lease = claim(Reconciliation, org_id, job_id, "state", "queued", "running")
    if not lease:
        return
    try:
        with tenant_transaction(None, org_id, activity_id=job_id):
            usage = OrganizationUsage.objects.select_for_update().get(org_id=org_id)
            row = Reconciliation.objects.select_for_update().get(org_id=org_id, pk=job_id)
            org = Organization.objects.get(pk=org_id)
            if row.lease != lease:
                return
            if org.state != "active":
                row.state, row.lease = "obsolete", None
                row.save()
                return
            actual = {
                "active_members": Membership.objects.filter(org_id=org_id, state="active").count(),
                "active_projects": Project.objects.filter(
                    org_id=org_id, archived_at__isnull=True
                ).count(),
                "active_tasks": Task.objects.filter(org_id=org_id, deleted_at__isnull=True).count(),
                "storage_bytes": Attachment.objects.filter(org_id=org_id).aggregate(
                    total=Sum("reserved_bytes")
                )["total"]
                or 0,
            }
            changed = [key for key, value in actual.items() if getattr(usage, key) != value]
            for key, value in actual.items():
                setattr(usage, key, value)
            if changed:
                usage.save(update_fields=changed)
                actor = Membership.objects.get(pk=org.owner_membership_id, org_id=org_id)
                event(org, actor, "usage.reconciled", org.id, {"counters": changed})
            row.corrected_counters = changed
            row.save(update_fields=["corrected_counters"])
            cursor = row.object_cursor
        cursor, count = reconcile_objects(org_id, job_id, cursor)
        with tenant_transaction(None, org_id, activity_id=job_id):
            Reconciliation.objects.filter(org_id=org_id, pk=job_id, lease=lease).update(
                state="queued",
                attempts=0,
                lease=None,
                last_error="",
                checked_at=timezone.now(),
                next_attempt_at=timezone.now() + timedelta(minutes=5),
                object_cursor=cursor,
                orphan_count=count,
            )
    except Exception:
        fail(Reconciliation, org_id, job_id, lease, "state", "queued", "RECONCILIATION_FAILED")
