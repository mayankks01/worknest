import hashlib
import json
import secrets
import tempfile
import uuid
import zipfile
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection
from django.db.models import Q
from django.utils import timezone

from collab import storage
from collab.models import Attachment, Comment
from organizations.context import tenant_transaction
from organizations.models import Membership, Organization, OrganizationUsage
from organizations.team import event
from projects.models import Project, Task

from .models import Export, Maintenance


def publish(kind, org_id, job_id):
    try:
        {"export": generate, "maintenance": maintain}[kind].apply_async(
            args=[str(org_id), str(job_id)], retry=False
        )
    except Exception:
        pass  # The database schedule survives broker outages.


@shared_task(name="lifecycle.dispatch")
def dispatch():
    with connection.cursor() as cursor:
        cursor.execute("SELECT org_id,job_id,kind FROM public.worknest_pending_lifecycle_jobs()")
        jobs = cursor.fetchall()
    for org_id, job_id, kind in jobs:
        publish(kind, org_id, job_id)


def claim(model, org_id, job_id):
    with tenant_transaction(None, org_id, lifecycle_id=job_id):
        OrganizationUsage.objects.select_for_update().filter(org_id=org_id).first()
        row = model.objects.select_for_update().filter(org_id=org_id, pk=job_id).first()
        if (
            not row
            or row.state not in {"queued", "running"}
            or row.next_attempt_at > timezone.now()
        ):
            return None
        if row.attempts >= 5:
            row.state, row.last_error = "failed", "RETRY_LIMIT"
            row.save()
            return None
        row.state, row.lease = "running", uuid.uuid4()
        row.attempts += 1
        row.next_attempt_at = timezone.now() + timedelta(minutes=5)
        row.save()
        return row.lease


def fail(model, org_id, job_id, lease):
    with tenant_transaction(None, org_id, lifecycle_id=job_id):
        row = (
            model.objects.select_for_update().filter(pk=job_id, org_id=org_id, lease=lease).first()
        )
        if row:
            row.state = "failed" if row.attempts >= 5 else "queued"
            row.last_error, row.lease = "PROCESSING_UNAVAILABLE", None
            row.next_attempt_at = timezone.now() + timedelta(
                seconds=30 * 2**row.attempts + secrets.randbelow(15)
            )
            row.save()


def export_actor(row):
    if not Organization.objects.filter(Organization.live_filter(), pk=row.org_id).exists():
        return None
    return Membership.objects.filter(
        org_id=row.org_id,
        pk=row.requester_id,
        state="active",
        role__in=["Owner", "Admin"],
        user__is_active=True,
        user__verified_at__isnull=False,
    ).first()


def export_key(row, lease):
    return f"orgs/{row.org_id}/exports/{row.id}/{lease}.zip"


def archive_snapshot(row):
    org = Organization.objects.get(pk=row.org_id)
    tasks = Task.objects.filter(org_id=row.org_id, deleted_at__isnull=True)
    files = list(
        Attachment.objects.filter(
            org_id=row.org_id,
            task_id__in=tasks.values("id"),
            state="available",
            deleted_at__isnull=True,
        )
    )
    members = Membership.objects.filter(org=org).select_related("user")
    manifest = {
        "format_version": 1,
        "generated_at": timezone.now(),
        "organization": {"id": org.id, "name": org.display_name, "timezone": org.timezone},
        "members": [
            {
                "id": m.id,
                "role": m.role,
                "state": m.state,
                "display_name": m.user.display_name
                if m.state == "active" and m.user.is_active
                else "Former member",
            }
            for m in members
        ],
        "projects": list(
            Project.objects.filter(org=org).values("id", "name", "description", "archived_at")
        ),
        "tasks": list(
            tasks.values(
                "id",
                "project_id",
                "title",
                "description",
                "status",
                "priority",
                "assignee_id",
                "created_by_id",
                "due_date",
                "created_at",
                "updated_at",
            )
        ),
        "comments": list(
            Comment.objects.filter(
                org=org, task_id__in=tasks.values("id"), deleted_at__isnull=True
            ).values("id", "task_id", "author_id", "body", "created_at", "updated_at")
        ),
        "attachments": [
            {
                "id": f.id,
                "task_id": f.task_id,
                "filename": f.filename,
                "path": f"attachments/{f.id}{f.extension}",
                "sha256": f.sha256,
            }
            for f in files
        ],
    }
    return manifest, files


@shared_task(name="lifecycle.generate", soft_time_limit=210, time_limit=240)
def generate(org_id, job_id):
    lease = claim(Export, org_id, job_id)
    if not lease:
        return
    try:
        with tenant_transaction(None, org_id, lifecycle_id=job_id):
            OrganizationUsage.objects.select_for_update().get(org_id=org_id)
            row = Export.objects.select_for_update().get(pk=job_id, org_id=org_id)
            if row.lease != lease:
                return
            if not export_actor(row):
                row.state, row.lease = "obsolete", None
                row.save()
                return
            manifest, files = archive_snapshot(row)
            key = export_key(row, lease)
        with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as archive:
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
                bundle.writestr("workspace.json", json.dumps(manifest, cls=DjangoJSONEncoder))
                for item in files:
                    with storage.read(item.object_key, item.size, item.sha256) as content:
                        bundle.writestr(f"attachments/{item.id}{item.extension}", content.read())
            size = archive.tell()
            archive.seek(0)
            digest = hashlib.file_digest(archive, "sha256").hexdigest()
            archive.seek(0)
            storage.put(key, archive)
        with tenant_transaction(None, org_id, lifecycle_id=job_id):
            OrganizationUsage.objects.select_for_update().get(org_id=org_id)
            row = Export.objects.select_for_update().get(pk=job_id, org_id=org_id)
            if row.lease != lease:
                return  # Old-lease objects are reclaimed by maintenance.
            actor = export_actor(row)
            row.object_key, row.size, row.sha256 = key, size, digest
            row.state, row.lease = ("ready" if actor else "obsolete"), None
            row.expires_at = timezone.now() + timedelta(hours=24)
            row.save()
            if actor:
                event(Organization.objects.get(pk=org_id), actor, "export.ready", row.id, {})
    except Exception:
        fail(Export, org_id, job_id, lease)


def objects(prefix):
    # Bounded pages, with streaming enumeration; no database transaction is open.
    cursor = None
    while True:
        kwargs = {"Bucket": settings.S3_BUCKET, "Prefix": prefix, "MaxKeys": 1000}
        if cursor:
            kwargs["ContinuationToken"] = cursor
        result = storage.client().list_objects_v2(**kwargs)
        yield from (item for item in result.get("Contents", []) if item["Key"].startswith(prefix))
        cursor = result.get("NextContinuationToken")
        if not cursor:
            break


def delete_rows(org_id, task_ids=None):
    # Static table names only; deferred FKs remain enforced at commit. ORM PROTECT is
    # intentionally bypassed only by this scoped, leased retention/purge worker.
    with connection.cursor() as cursor:
        if task_ids is not None:
            for table in ["activity_notification", "collab_comment", "collab_attachment"]:
                cursor.execute(
                    f"DELETE FROM {table} WHERE org_id=%s AND task_id=ANY(%s)", [org_id, task_ids]
                )
            cursor.execute(
                "DELETE FROM projects_task WHERE org_id=%s AND id=ANY(%s)", [org_id, task_ids]
            )
            return
        for table in [
            "lifecycle_export",
            "activity_notification",
            "activity_preference",
            "activity_reconciliation",
            "collab_attachment",
            "collab_comment",
            "projects_writerequest",
            "projects_task",
            "projects_projectmembership",
            "projects_project",
            "organizations_teamrequest",
            "organizations_invitation",
            "organizations_outboxevent",
            "organizations_auditevent",
            "organizations_organizationusage",
            "organizations_membership",
        ]:
            cursor.execute(f"DELETE FROM {table} WHERE org_id=%s", [org_id])
        cursor.execute(
            "DELETE FROM organizations_creationrequest WHERE response->'organization'->>'id'=%s",
            [str(org_id)],
        )
        cursor.execute("DELETE FROM organizations_organization WHERE id=%s", [org_id])
        if cursor.rowcount != 1:
            raise ValueError("Organization purge deadline not reached")


def retention(org_id, job_id):
    now = timezone.now()
    with tenant_transaction(None, org_id, lifecycle_id=job_id):
        OrganizationUsage.objects.select_for_update().get(org_id=org_id)
        tasks = list(
            Task.objects.filter(
                org_id=org_id, deleted_at__lte=now - timedelta(days=30)
            ).values_list("id", flat=True)
        )
        files = list(
            Attachment.objects.filter(org_id=org_id, task_id__in=tasks).values(
                "id", "object_key", "reserved_bytes"
            )
        )
        exports = list(
            Export.objects.filter(org_id=org_id)
            .filter(Q(expires_at__lte=now) | Q(state="obsolete"))
            .exclude(object_key="")
            .values("id", "object_key")
        )
        live_keys = set(
            Export.objects.filter(org_id=org_id, state="ready", expires_at__gt=now).values_list(
                "object_key", flat=True
            )
        )
    for item in [*files, *exports]:
        storage.delete(item["object_key"])
    for obj in objects(f"orgs/{org_id}/exports/"):
        if obj["Key"] not in live_keys and obj["LastModified"] < now - timedelta(hours=24):
            storage.delete(obj["Key"])
    with tenant_transaction(None, org_id, lifecycle_id=job_id):
        usage = OrganizationUsage.objects.select_for_update().get(org_id=org_id)
        # Re-read reservations because the attachment cleanup worker may have released them.
        from django.db.models import Sum

        released = (
            Attachment.objects.filter(org_id=org_id, task_id__in=tasks).aggregate(
                total=Sum("reserved_bytes")
            )["total"]
            or 0
        )
        usage.storage_bytes = max(0, usage.storage_bytes - released)
        usage.save(update_fields=["storage_bytes"])
        delete_rows(org_id, tasks)
        Export.objects.filter(org_id=org_id, id__in=[x["id"] for x in exports]).update(
            state="expired", object_key=""
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM activity_notification WHERE org_id=%s AND created_at<%s",
                [org_id, now - timedelta(days=30)],
            )
            # Ninety-day retention also retires abandoned delivery work and its links.
            cutoff = now - timedelta(days=90)
            cursor.execute(
                "SELECT e.id FROM organizations_outboxevent e JOIN organizations_auditevent a "
                "ON a.id=e.audit_id AND a.org_id=e.org_id WHERE e.org_id=%s "
                "AND (e.created_at<%s OR a.created_at<%s)",
                [org_id, cutoff, cutoff],
            )
            expired_events = [entry[0] for entry in cursor.fetchall()]
            cursor.execute(
                "UPDATE organizations_invitation SET email_event_id=NULL WHERE org_id=%s "
                "AND email_event_id=ANY(%s)",
                [org_id, expired_events],
            )
            cursor.execute(
                "DELETE FROM activity_notification WHERE org_id=%s AND source_id=ANY(%s)",
                [org_id, expired_events],
            )
            cursor.execute(
                "DELETE FROM organizations_outboxevent WHERE org_id=%s AND id=ANY(%s)",
                [org_id, expired_events],
            )
            cursor.execute(
                "DELETE FROM organizations_auditevent WHERE org_id=%s AND created_at<%s",
                [org_id, cutoff],
            )
            for table in ["projects_writerequest", "organizations_teamrequest"]:
                cursor.execute(
                    f"DELETE FROM {table} WHERE org_id=%s AND expires_at<%s", [org_id, now]
                )
            cursor.execute(
                "DELETE FROM organizations_creationrequest WHERE "
                "response->'organization'->>'id'=%s AND expires_at<%s",
                [str(org_id), now],
            )


@shared_task(name="lifecycle.maintain", soft_time_limit=210, time_limit=240)
def maintain(org_id, job_id):
    lease = claim(Maintenance, org_id, job_id)
    if not lease:
        return
    try:
        with tenant_transaction(None, org_id, lifecycle_id=job_id):
            org = Organization.objects.get(pk=org_id)
            due = org.deletion_due_at
            purge = (
                org.state == "pending_delete"
                and due
                and due <= timezone.now() - timedelta(minutes=10)
            )
        if purge:
            prefix = f"orgs/{org_id}/"
            for obj in objects(prefix):
                storage.delete(obj["Key"])
            if next(objects(prefix), None) is not None:
                raise OSError("Storage cleanup incomplete")
            with tenant_transaction(None, org_id, lifecycle_id=job_id):
                OrganizationUsage.objects.select_for_update().get(org_id=org_id)
                row = Maintenance.objects.select_for_update().get(pk=job_id, org_id=org_id)
                org = Organization.objects.get(pk=org_id)
                if row.lease != lease or org.deletion_due_at != due:
                    return
                delete_rows(org_id)
                row.state, row.lease, row.completed_at = "completed", None, timezone.now()
                row.last_error = ""
                row.save()
        else:
            retention(org_id, job_id)
            with tenant_transaction(None, org_id, lifecycle_id=job_id):
                Maintenance.objects.filter(pk=job_id, org_id=org_id, lease=lease).update(
                    state="queued",
                    attempts=0,
                    lease=None,
                    checked_at=timezone.now(),
                    next_attempt_at=timezone.now() + timedelta(minutes=5),
                    last_error="",
                )
    except Exception:
        fail(Maintenance, org_id, job_id, lease)
