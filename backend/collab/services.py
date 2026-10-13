import uuid
from datetime import datetime
from types import SimpleNamespace

from django.core import signing
from django.db.models import Q

from accounts.views import InputError
from organizations.models import Membership, Organization
from organizations.team import TeamError, event
from projects.models import Task
from projects.services import accessible_projects, visible_project


def visible_task(org, actor, task_id):
    task = Task.objects.filter(
        org=org,
        pk=task_id,
        deleted_at__isnull=True,
        project_id__in=accessible_projects(org.id, actor).values("id"),
    ).first()
    if not task:
        raise TeamError("NOT_FOUND", "This task is unavailable.", 404)
    return task, visible_project(org.id, actor, task.project_id)


def author_name(author):
    return (
        author.user.display_name
        if author.state == "active" and author.user.is_active
        else "Former member"
    )


def ordered_page(request, query, label, serialize):
    salt = f"collaboration:{request.user.id}:{label}"
    try:
        limit = int(request.GET.get("limit", 25))
        if not 1 <= limit <= 100:
            raise ValueError
        token = request.GET.get("cursor")
        if token:
            created, key = signing.loads(token, salt=salt, max_age=86400)
            created, key = datetime.fromisoformat(created), uuid.UUID(key)
            query = query.filter(Q(created_at__gt=created) | Q(created_at=created, id__gt=key))
    except (ValueError, TypeError, signing.BadSignature):
        raise InputError({"cursor": ["Invalid page. Refresh the list."]}) from None
    rows = list(query.order_by("created_at", "id")[: limit + 1])
    return {
        "results": [serialize(item) for item in rows[:limit]],
        "next_cursor": signing.dumps(
            [rows[limit - 1].created_at.isoformat(), str(rows[limit - 1].id)], salt=salt
        )
        if len(rows) > limit
        else None,
    }


def authorized_upload(item):
    # Recheck the upload author's live organization AND project access in every scan job.
    actor = (
        Membership.objects.filter(
            pk=item.author_id, org_id=item.org_id, state="active", user__is_active=True
        )
        .exclude(role="Viewer")
        .first()
    )
    if (
        not actor
        or not Organization.objects.filter(Organization.live_filter(), pk=item.org_id).exists()
    ):
        return False
    return Task.objects.filter(
        pk=item.task_id,
        org_id=item.org_id,
        deleted_at__isnull=True,
        project_id__in=accessible_projects(item.org_id, actor).values("id"),
    ).exists()


def file_event(item, action):
    return event(
        Organization(id=item.org_id),
        SimpleNamespace(id=item.author_id),
        action,
        item.id,
        {"state": item.state, "reason": item.reason},
    )
