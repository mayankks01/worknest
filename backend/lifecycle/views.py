import hashlib
import tempfile
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, JsonResponse
from django.utils import timezone

from accounts.views import InputError, endpoint, error
from collab import storage
from organizations.context import tenant_transaction
from organizations.team import TeamError, actor_for, event, page, throttle
from organizations.views import authenticated
from projects.models import Task
from projects.services import accessible_projects, manager, visible_project
from projects.views import idempotent, local_today, project_endpoint, task_data

from .models import Export, Maintenance
from .tasks import export_actor, publish


@project_endpoint("GET")
def search(request, data, org, actor):
    query = request.GET.get("q", "").strip()
    if not 2 <= len(query) <= 200:
        raise InputError({"q": ["Search with 2–200 characters."]})
    projects = accessible_projects(org.id, actor)
    state = request.GET.get("state", "active")
    if state not in {"active", "archived", "all"}:
        raise InputError({"state": ["Choose active, archived or all."]})
    if state != "all":
        projects = projects.filter(archived_at__isnull=state == "active")
    if project_id := request.GET.get("project"):
        from projects.views import identifier

        project_id = identifier(project_id, "project")
        visible_project(org.id, actor, project_id)
        projects = projects.filter(pk=project_id)
    tasks = Task.objects.filter(
        org=org, project_id__in=projects.values("id"), deleted_at__isnull=True
    ).filter(Q(title__icontains=query) | Q(description__icontains=query))
    today = local_today(org)
    return JsonResponse(
        {
            **page(
                request,
                tasks,
                f"{org.id}:search:{query}:{state}:{project_id or ''}",
                lambda t: task_data(t, today),
            ),
            "count": tasks.count(),
        }
    )


def export_data(row):
    ready = row.state == "ready" and row.expires_at and row.expires_at > timezone.now()
    return {
        "id": str(row.id),
        "state": "expired" if row.state == "ready" and not ready else row.state,
        "created_at": row.created_at.isoformat(),
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "download_url": f"/api/v1/orgs/{row.org_id}/exports/{row.id}/download/" if ready else None,
    }


@project_endpoint("GET, POST")
def exports(request, data, org, actor):
    manager(actor)
    if request.method == "GET":
        return JsonResponse(
            page(
                request,
                Export.objects.filter(org=org, requester=actor),
                f"{org.id}:exports",
                export_data,
            )
        )
    if data:
        raise InputError({"body": ["Send an empty object."]})

    def create():
        if Export.objects.filter(org=org, state__in=["queued", "running"]).count() >= 2:
            raise TeamError(
                "EXPORT_BUSY", "Two exports are already being prepared. Try again later.", 409
            )
        row = Export.objects.create(org=org, requester=actor)
        event(org, actor, "export.requested", row.id, {})
        transaction.on_commit(lambda: publish("export", org.id, row.id))
        return {"export": export_data(row)}

    response = idempotent(request, data, org, "exports", create)
    response.status_code = 202
    return response


@project_endpoint("GET, POST")
def export_detail(request, data, org, actor, export_id):
    manager(actor)
    row = Export.objects.filter(org=org, pk=export_id, requester=actor).first()
    if not row:
        raise TeamError("NOT_FOUND", "This export is unavailable.", 404)
    if request.method == "POST":
        if data:
            raise InputError({"body": ["Send an empty object to retry."]})
        if row.state != "failed":
            raise TeamError("EXPORT_NOT_FAILED", "Only failed exports can be retried.", 409)
        row.state, row.attempts, row.lease = "queued", 0, None
        row.next_attempt_at, row.last_error = timezone.now(), ""
        row.save()
        event(org, actor, "export.retry_requested", row.id, {})
        transaction.on_commit(lambda: publish("export", org.id, row.id))
    return JsonResponse({"export": export_data(row)})


@endpoint("GET")
def download(request, data, org_id, export_id):
    denied = authenticated(request)
    if denied is not None:
        return denied

    def authorize():
        org, actor = actor_for(request, org_id)
        manager(actor)
        row = Export.objects.filter(org=org, pk=export_id, requester=actor).first()
        if not row or not export_actor(row):
            raise TeamError("NOT_FOUND", "This export is unavailable.", 404)
        if row.state != "ready" or not row.expires_at or row.expires_at <= timezone.now():
            raise TeamError("EXPORT_UNAVAILABLE", "This export is not ready or has expired.", 409)
        return row

    content = None
    try:
        with tenant_transaction(request.user.id, org_id):
            row = authorize()
        content = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
        try:
            stream = storage.client().get_object(Bucket=settings.S3_BUCKET, Key=row.object_key)[
                "Body"
            ]
            digest, size = hashlib.sha256(), 0
            try:
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > row.size:
                        raise ValueError("Export size mismatch")
                    digest.update(chunk)
                    content.write(chunk)
            finally:
                stream.close()
            if size != row.size or digest.hexdigest() != row.sha256:
                raise ValueError("Export integrity mismatch")
        except Exception:
            content.close()
            return error(
                request, "STORAGE_UNAVAILABLE", "The export is temporarily unavailable.", 503
            )
        with tenant_transaction(request.user.id, org_id):
            authorize()
        content.seek(0)
        response = FileResponse(
            content,
            as_attachment=True,
            filename=f"worknest-{export_id}.zip",
            content_type="application/zip",
        )
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = "default-src 'none'; sandbox"
        return response
    except TeamError as exc:
        if content:
            content.close()
        return error(request, exc.code, exc.message, exc.status)


@project_endpoint("GET, POST", ("password", "confirmation", "action"))
def deletion(request, data, org, actor):
    manager(actor)
    maintenance = Maintenance.objects.get(org_id=org.id)
    if request.method == "POST":
        if actor.role != "Owner":
            raise TeamError("FORBIDDEN", "Only the Owner can schedule or cancel deletion.", 403)
        limited = throttle(request, "reauth", request.user.email)
        if limited is not None:
            return limited
        password = data.get("password", "")
        if (
            not isinstance(password, str)
            or len(password) > 128
            or not request.user.check_password(password)
        ):
            raise TeamError("REAUTH_REQUIRED", "Enter your current password to continue.", 403)
        action = data.get("action")
        if action not in {"schedule", "cancel"} or data.get("confirmation") != org.slug:
            raise InputError(
                {
                    "confirmation": [
                        "Enter this organization's address and choose schedule or cancel."
                    ]
                }
            )
        if action == "schedule" and org.state == "active":
            org.state, org.deletion_due_at = "pending_delete", timezone.now() + timedelta(days=7)
            org.save(update_fields=["state", "deletion_due_at"])
            event(org, actor, "organization.deletion_requested", org.id, {})
        elif action == "cancel" and org.state == "pending_delete":
            org.state, org.deletion_due_at = "active", None
            org.save(update_fields=["state", "deletion_due_at"])
            event(org, actor, "organization.deletion_cancelled", org.id, {})
        maintenance.state, maintenance.attempts = "queued", 0
        maintenance.next_attempt_at, maintenance.lease = timezone.now(), None
        maintenance.last_error = ""
        maintenance.save()
        transaction.on_commit(lambda: publish("maintenance", org.id, maintenance.id))
    return JsonResponse(
        {
            "state": org.state,
            "deletion_due_at": org.deletion_due_at.isoformat() if org.deletion_due_at else None,
            "maintenance_state": maintenance.state,
            "backup_retention_days": 30,
        }
    )
