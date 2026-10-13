import hashlib
import re
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.http import FileResponse, HttpResponse, JsonResponse
from django.utils import timezone

from accounts.views import InputError, endpoint, error
from organizations.context import tenant_transaction
from organizations.models import OrganizationUsage
from organizations.team import TeamError, actor_for, event
from organizations.views import authenticated
from projects.services import writable
from projects.views import idempotent, project_endpoint, text

from . import storage
from .models import Attachment, Comment
from .services import author_name, file_event, ordered_page, visible_task
from .tasks import publish
from .validation import safe_name


def comment_data(item, actor, project):
    editable = actor.role != "Viewer" and not project.archived_at
    return {
        "id": str(item.id),
        "body": item.body,
        "author_name": author_name(item.author),
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
        "version": item.version,
        "can_edit": editable and item.author_id == actor.id,
        "can_delete": editable and (item.author_id == actor.id or actor.role in {"Owner", "Admin"}),
    }


@project_endpoint("GET, POST", ("body",))
def comments(request, data, org, actor, task_id):
    task, project = visible_task(org, actor, task_id)
    if request.method == "GET":
        return JsonResponse(
            ordered_page(
                request,
                Comment.objects.filter(org=org, task=task, deleted_at__isnull=True).select_related(
                    "author__user"
                ),
                f"{org.id}:{task.id}:comments",
                lambda item: comment_data(item, actor, project),
            )
        )
    writable(project, actor)
    body = text(data, "body", 5000, True)

    def create():
        item = Comment.objects.create(org=org, task=task, author=actor, body=body)
        event(org, actor, "comment.created", item.id, {"task_id": str(task.id)})
        return {"comment": comment_data(item, actor, project)}

    return idempotent(request, data, org, f"{task.id}:comments", create)


@project_endpoint("PATCH, DELETE", ("body", "version"))
def comment_detail(request, data, org, actor, comment_id):
    item = (
        Comment.objects.filter(org=org, pk=comment_id, deleted_at__isnull=True)
        .select_related("author__user")
        .first()
    )
    if not item:
        raise TeamError("NOT_FOUND", "This comment is unavailable.", 404)
    _, project = visible_task(org, actor, item.task_id)
    writable(project, actor)
    if item.author_id != actor.id and (
        request.method == "PATCH" or actor.role not in {"Owner", "Admin"}
    ):
        raise TeamError(
            "FORBIDDEN", "Only the author can edit; Owners and Admins can also delete.", 403
        )
    if type(data.get("version")) is not int:
        raise InputError({"version": ["Send the current comment version."]})
    if data["version"] != item.version:
        return error(
            request,
            "VERSION_CONFLICT",
            "This comment changed. Reload before retrying.",
            409,
            {"current_version": item.version},
        )
    if request.method == "DELETE":
        if set(data) != {"version"}:
            raise InputError({"body": ["Send only the comment version."]})
        item.deleted_at, item.body = timezone.now(), ""
    else:
        item.body = text(data, "body", 5000, True)
    item.version += 1
    item.updated_at = timezone.now()
    item.save()
    event(
        org,
        actor,
        "comment.deleted" if item.deleted_at else "comment.updated",
        item.id,
        {"task_id": str(item.task_id), "version": item.version},
    )
    return (
        HttpResponse(status=204)
        if item.deleted_at
        else JsonResponse({"comment": comment_data(item, actor, project)})
    )


def attachment_data(item, actor, project):
    edit = actor.role != "Viewer" and not project.archived_at
    own = item.author_id == actor.id
    return {
        "id": str(item.id),
        "filename": item.filename,
        "size": item.size,
        "state": item.state,
        "reason": item.reason,
        "author_name": author_name(item.author),
        "created_at": item.created_at.isoformat(),
        "can_download": item.state == "available",
        "can_edit": edit and own and item.state == "available",
        "can_delete": edit and (own or actor.role in {"Owner", "Admin"}),
        "cleanup_pending": item.cleanup_pending,
    }


@project_endpoint("GET")
def attachments(request, data, org, actor, task_id):
    task, project = visible_task(org, actor, task_id)
    result = ordered_page(
        request,
        Attachment.objects.filter(org=org, task=task, deleted_at__isnull=True).select_related(
            "author__user"
        ),
        f"{org.id}:{task.id}:attachments",
        lambda item: attachment_data(item, actor, project),
    )
    usage = OrganizationUsage.objects.get(org=org)
    result["storage"] = {
        "used_bytes": usage.storage_bytes,
        "limit_bytes": usage.storage_limit,
        "file_limit_bytes": settings.ATTACHMENT_MAX_BYTES,
    }
    return JsonResponse(result)


@endpoint("POST", multipart=True)
def upload(request, data, org_id, task_id):
    denied = authenticated(request)
    if denied is not None:
        return denied
    try:
        incoming_files = request.FILES
        if getattr(request, "upload_too_large", False):
            return error(request, "FILE_TOO_LARGE", "The file exceeds the upload limit.", 413)
        if (
            request.POST
            or set(incoming_files) != {"file"}
            or len(request.FILES.getlist("file")) != 1
        ):
            raise InputError({"file": ["Send exactly one file and no other fields."]})
        incoming = request.FILES["file"]
        content = incoming.read(settings.ATTACHMENT_MAX_BYTES + 1)
        if len(content) > settings.ATTACHMENT_MAX_BYTES:
            return error(request, "FILE_TOO_LARGE", "The file exceeds the upload limit.", 413)
        if not content:
            raise InputError({"file": ["Choose a nonempty file."]})
        try:
            name, extension = safe_name(incoming.name)
        except ValueError as exc:
            raise InputError({"file": [str(exc)]}) from None
        key = request.headers.get("Idempotency-Key", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", key):
            raise InputError(
                {"idempotency_key": ["Send a unique Idempotency-Key of 16–128 characters."]}
            )
        digest = hashlib.sha256(content).hexdigest()
        payload_hash = hashlib.sha256(f"{name}:{digest}".encode()).hexdigest()
        with tenant_transaction(request.user.id, org_id):
            org, actor = actor_for(request, org_id)
            task, project = visible_task(org, actor, task_id)
            writable(project, actor)
            org, actor = actor_for(request, org_id, lock=True)
            task, project = visible_task(org, actor, task_id)
            writable(project, actor)
            previous = Attachment.objects.filter(
                org=org, task=task, author=actor, request_key=key
            ).first()
            if previous and previous.request_expires_at > timezone.now():
                if previous.payload_hash != payload_hash:
                    raise TeamError(
                        "IDEMPOTENCY_CONFLICT", "This upload key was used with another file.", 409
                    )
                return JsonResponse(
                    {"attachment": attachment_data(previous, actor, project)}, status=202
                )
            if previous:
                previous.request_key = uuid.uuid4().hex
                previous.save(update_fields=["request_key"])
            usage = OrganizationUsage.objects.get(org=org)
            if usage.storage_bytes + len(content) > usage.storage_limit:
                raise TeamError(
                    "STORAGE_LIMIT",
                    "This organization has reached its attachment storage limit.",
                    409,
                )
            file_id = uuid.uuid4()
            item = Attachment.objects.create(
                id=file_id,
                org=org,
                task=task,
                author=actor,
                filename=name,
                extension=extension,
                size=len(content),
                reserved_bytes=len(content),
                sha256=digest,
                object_key=f"orgs/{org.id}/attachments/{file_id}",
                request_key=key,
                payload_hash=payload_hash,
                request_expires_at=timezone.now() + timedelta(hours=24),
                next_attempt_at=timezone.now() + timedelta(minutes=15),
            )
            usage.storage_bytes += item.size
            usage.save(update_fields=["storage_bytes"])
            event(
                org,
                actor,
                "attachment.reserved",
                item.id,
                {"task_id": str(task.id), "size": item.size},
            )
        try:
            storage.put(item.object_key, content)
            stored = True
        except Exception:
            stored = False
        # A file-scoped system transaction can clean up even if access was revoked during I/O.
        with tenant_transaction(None, org_id, file_id=item.id):
            OrganizationUsage.objects.select_for_update().get(org_id=org_id)
            item = Attachment.objects.select_for_update().get(org_id=org_id, pk=item.id)
            if item.state == "uploading" and stored:
                item.state = "queued"
            else:
                item.state = "failed" if item.deleted_at is None else "deleting"
                item.reason, item.cleanup_pending = "UPLOAD_FAILED", True
                item.cleanup_attempts = 0
            item.lease = None
            item.next_attempt_at = timezone.now()
            item.save()
            file_event(item, "attachment.uploaded" if stored else "attachment.upload_failed")
            transaction.on_commit(lambda: publish(org_id, item.id))
        if not stored:
            return error(
                request,
                "STORAGE_UNAVAILABLE",
                "Upload did not complete. Reserved storage will be cleaned up.",
                503,
            )
        with tenant_transaction(request.user.id, org_id):
            org, actor = actor_for(request, org_id)
            _, project = visible_task(org, actor, task_id)
            item = Attachment.objects.select_related("author__user").get(org=org, pk=item.id)
            return JsonResponse({"attachment": attachment_data(item, actor, project)}, status=202)
    except TeamError as exc:
        return error(request, exc.code, exc.message, exc.status)


@project_endpoint("GET, PATCH, DELETE", ("filename",))
def attachment_detail(request, data, org, actor, attachment_id):
    item = (
        Attachment.objects.filter(org=org, pk=attachment_id, deleted_at__isnull=True)
        .select_related("author__user")
        .first()
    )
    if not item:
        raise TeamError("NOT_FOUND", "This attachment is unavailable.", 404)
    _, project = visible_task(org, actor, item.task_id)
    if request.method == "GET":
        return JsonResponse({"attachment": attachment_data(item, actor, project)})
    writable(project, actor)
    if item.author_id != actor.id and (
        request.method == "PATCH" or actor.role not in {"Owner", "Admin"}
    ):
        raise TeamError("FORBIDDEN", "You cannot change this attachment.", 403)
    if request.method == "PATCH":
        try:
            name, extension = safe_name(text(data, "filename", 140, True))
            if extension != item.extension or item.state != "available":
                raise ValueError(
                    "Only available files can be renamed; keep the original extension."
                )
        except ValueError as exc:
            raise InputError({"filename": [str(exc)]}) from None
        item.filename = name
        item.save(update_fields=["filename"])
        event(org, actor, "attachment.renamed", item.id, {})
        return JsonResponse({"attachment": attachment_data(item, actor, project)})
    if data:
        raise InputError({"body": ["Send an empty object to delete."]})
    was_uploading = item.state == "uploading"
    item.deleted_at, item.state, item.cleanup_pending = timezone.now(), "deleting", True
    item.lease, item.cleanup_attempts = None, 0
    # Do not race an in-flight object PUT; an interrupted upload has a bounded lease.
    item.next_attempt_at = (
        max(timezone.now(), item.next_attempt_at) if was_uploading else timezone.now()
    )
    item.save()
    event(org, actor, "attachment.deleted", item.id, {})
    transaction.on_commit(lambda: publish(org.id, item.id))
    return HttpResponse(status=204)


@endpoint("GET")
def download(request, data, org_id, attachment_id):
    denied = authenticated(request)
    if denied is not None:
        return denied

    def authorize():
        org, actor = actor_for(request, org_id)
        item = Attachment.objects.filter(org=org, pk=attachment_id, deleted_at__isnull=True).first()
        if not item:
            raise TeamError("NOT_FOUND", "This attachment is unavailable.", 404)
        visible_task(org, actor, item.task_id)
        if item.state != "available":
            raise TeamError("FILE_NOT_READY", "This file has not passed its safety checks.", 409)
        return item

    try:
        with tenant_transaction(request.user.id, org_id):
            item = authorize()
        try:
            content = storage.read(item.object_key, item.size, item.sha256)
        except Exception:
            return error(
                request, "STORAGE_UNAVAILABLE", "The file is temporarily unavailable.", 503
            )
        try:
            with tenant_transaction(request.user.id, org_id):
                item = authorize()  # Access can change while storage is being read.
        except Exception:
            content.close()
            raise
        response = FileResponse(
            content, as_attachment=True, filename=item.filename, content_type=item.content_type
        )
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = "default-src 'none'; sandbox"
        return response
    except TeamError as exc:
        return error(request, exc.code, exc.message, exc.status)
