import uuid
from datetime import datetime

from django.core import signing
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone

from accounts.views import InputError
from collab.models import Attachment
from collab.tasks import publish as publish_file
from organizations.models import AuditEvent, Membership, OutboxEvent
from organizations.team import TeamError, event, page
from projects.services import manager
from projects.views import project_endpoint

from .models import Notification, Preference, Reconciliation
from .services import inbox_query
from .tasks import publish


def newest(request, query, label, serialize):
    salt = f"activity:{request.user.id}:{label}"
    try:
        limit = int(request.GET.get("limit", 25))
        if not 1 <= limit <= 100:
            raise ValueError
        if token := request.GET.get("cursor"):
            date, key = signing.loads(token, salt=salt, max_age=86400)
            date, key = datetime.fromisoformat(date), uuid.UUID(key)
            query = query.filter(Q(created_at__lt=date) | Q(created_at=date, id__lt=key))
    except (ValueError, TypeError, signing.BadSignature):
        raise InputError({"cursor": ["Invalid page. Refresh the list."]}) from None
    rows = list(query.order_by("-created_at", "-id")[: limit + 1])
    return {
        "results": [serialize(item) for item in rows[:limit]],
        "next_cursor": signing.dumps(
            [rows[limit - 1].created_at.isoformat(), str(rows[limit - 1].id)], salt=salt
        )
        if len(rows) > limit
        else None,
    }


def notification_data(item):
    return {
        "id": str(item.id),
        "kind": item.kind,
        "task_title": item.task.title,
        "created_at": item.created_at.isoformat(),
        "read_at": item.read_at.isoformat() if item.read_at else None,
        "href": f"/orgs/{item.org_id}/projects/{item.task.project_id}?task={item.task_id}",
    }


@project_endpoint("GET")
def inbox(request, data, org, actor):
    query = inbox_query(org, actor)
    unread = query.filter(read_at__isnull=True).count()
    only_unread = request.GET.get("unread", "false")
    if only_unread not in {"true", "false"}:
        raise InputError({"unread": ["Use true or false."]})
    if only_unread == "true":
        query = query.filter(read_at__isnull=True)
    result = newest(request, query, f"{org.id}:inbox:{only_unread}", notification_data)
    return JsonResponse({**result, "unread_count": unread})


@project_endpoint("PATCH", ("read",))
def mark_read(request, data, org, actor, notification_id):
    if set(data) != {"read"} or type(data["read"]) is not bool:
        raise InputError({"read": ["Send true or false."]})
    row = inbox_query(org, actor).filter(pk=notification_id).first()
    if not row:
        raise TeamError("NOT_FOUND", "This notification is unavailable.", 404)
    row.read_at = (row.read_at or timezone.now()) if data["read"] else None
    row.save(update_fields=["read_at"])
    event(org, actor, "notification.read" if data["read"] else "notification.unread", row.id, {})
    return JsonResponse({"notification": notification_data(row)})


@project_endpoint("GET, PATCH", ("assignment_email",))
def preferences(request, data, org, actor):
    if request.method == "PATCH":
        if set(data) != {"assignment_email"} or type(data["assignment_email"]) is not bool:
            raise InputError({"assignment_email": ["Choose whether to receive assignment emails."]})
        row, _ = Preference.objects.update_or_create(org=org, member=actor, defaults=data)
        event(
            org,
            actor,
            "notification.preference_updated",
            actor.id,
            {"assignment_email": row.assignment_email},
        )
        value = row.assignment_email
    else:
        row = Preference.objects.filter(org=org, member=actor).first()
        value = row.assignment_email if row else True
    return JsonResponse({"assignment_email": value})


@project_endpoint("GET")
def audit(request, data, org, actor):
    manager(actor)
    actors = {
        str(member.id): member.user.display_name
        if member.state == "active" and member.user.is_active
        else "Former member"
        for member in Membership.objects.filter(org=org).select_related("user")
    }
    return JsonResponse(
        newest(
            request,
            AuditEvent.objects.filter(org=org),
            f"{org.id}:audit",
            lambda row: {
                "id": str(row.id),
                "action": row.action,
                "resource_id": str(row.resource_id),
                "actor_name": actors.get(str(row.actor_membership_id), "Former member"),
                "created_at": row.created_at.isoformat(),
            },
        )
    )


@project_endpoint("GET")
def operations(request, data, org, actor):
    manager(actor)
    kind = request.GET.get("kind", "event")
    if kind == "event":
        query = OutboxEvent.objects.filter(org=org, delivery_state="failed")

        def serialize(row):
            return {
                "id": str(row.id),
                "kind": "event",
                "label": row.topic,
                "attempts": row.attempts,
                "reason": row.last_error or "DELIVERY_FAILED",
            }
    elif kind == "email":
        query = Notification.objects.filter(org=org, email_state="failed")

        def serialize(row):
            return {
                "id": str(row.id),
                "kind": "email",
                "label": "Assignment email",
                "attempts": row.attempts,
                "reason": row.last_error,
            }
    elif kind == "file":
        query = Attachment.objects.filter(org=org).filter(
            Q(cleanup_pending=True, cleanup_attempts__gte=5)
        )

        def serialize(row):
            return {
                "id": str(row.id),
                "kind": "file",
                "label": "File cleanup" if row.cleanup_pending else "File checks",
                "attempts": row.cleanup_attempts if row.cleanup_pending else row.scan_attempts,
                "reason": row.reason,
            }
    elif kind == "reconcile":
        query = Reconciliation.objects.filter(org=org, state="failed")

        def serialize(row):
            return {
                "id": str(row.id),
                "kind": "reconcile",
                "label": "Storage and quota reconciliation",
                "attempts": row.attempts,
                "reason": row.last_error,
            }
    else:
        raise InputError({"kind": ["Choose event, email, file or reconcile."]})
    result = page(request, query, f"{org.id}:failed:{kind}", serialize)
    pending = OutboxEvent.objects.filter(
        org=org, delivery_state__in=["queued", "processing", "sending"]
    )
    oldest = pending.order_by("created_at").values_list("created_at", flat=True).first()
    rec = Reconciliation.objects.filter(org=org).first()
    return JsonResponse(
        {
            **result,
            "pending_events": pending.count(),
            "oldest_event_at": oldest.isoformat() if oldest else None,
            "reconciliation": {
                "state": rec.state,
                "checked_at": rec.checked_at.isoformat() if rec.checked_at else None,
                "corrected_counters": rec.corrected_counters,
                "orphans_removed": rec.orphan_count,
            }
            if rec
            else None,
        }
    )


@project_endpoint("POST")
def retry(request, data, org, actor, kind, job_id):
    manager(actor)
    if data:
        raise InputError({"body": ["Send an empty object."]})
    now = timezone.now()
    if kind == "file":
        row = Attachment.objects.select_for_update().filter(org=org, pk=job_id).first()
        if not row or not row.cleanup_pending or row.cleanup_attempts < 5:
            raise TeamError("NOT_FOUND", "No exhausted cleanup exists for this file.", 404)
        row.cleanup_attempts, row.next_attempt_at, row.lease = 0, now, None
        row.save(update_fields=["cleanup_attempts", "next_attempt_at", "lease"])
        transaction.on_commit(lambda: publish_file(org.id, row.id))
    else:
        mapping = {
            "event": (OutboxEvent, "delivery_state"),
            "email": (Notification, "email_state"),
            "reconcile": (Reconciliation, "state"),
        }
        if kind not in mapping:
            raise TeamError("NOT_FOUND", "This job is unavailable.", 404)
        model, field = mapping[kind]
        row = (
            model.objects.select_for_update()
            .filter(org=org, pk=job_id, **{field: "failed"})
            .first()
        )
        if not row:
            raise TeamError("NOT_FOUND", "No failed job exists with this identifier.", 404)
        setattr(row, field, "queued")
        row.attempts, row.next_attempt_at, row.lease, row.last_error = 0, now, None, ""
        row.save()
        if kind == "event" and row.topic == "invitation.email":
            from organizations.team import publish_email

            transaction.on_commit(lambda: publish_email(str(org.id), str(row.id)))
        else:
            transaction.on_commit(lambda: publish(kind, org.id, row.id))
    event(org, actor, "job.retry_requested", job_id, {"kind": kind})
    return JsonResponse({"queued": True})
