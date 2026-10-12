"""Shared live project policy and atomic offboarding; callers hold the usage lock."""

from django.db.models import F
from django.utils import timezone

from organizations.models import Membership
from organizations.team import TeamError, event

from .models import Project, ProjectMembership, Task


def accessible_projects(org_id, actor):
    query = Project.objects.filter(org_id=org_id)
    if actor.role not in {"Owner", "Admin"}:
        ids = ProjectMembership.objects.filter(
            org_id=org_id, membership=actor, state="active"
        ).values("project_id")
        query = query.filter(id__in=ids)
    return query


def visible_project(org_id, actor, project_id):
    project = accessible_projects(org_id, actor).filter(pk=project_id).first()
    if not project:
        raise TeamError("NOT_FOUND", "This project is unavailable.", 404)
    return project


def manager(actor):
    if actor.role not in {"Owner", "Admin"}:
        raise TeamError("FORBIDDEN", "Only an Owner or Admin can do this.", 403)


def writable(project, actor):
    if actor.role == "Viewer":
        raise TeamError("FORBIDDEN", "Viewers have read-only access.", 403)
    if project.archived_at:
        raise TeamError("PROJECT_ARCHIVED", "Restore this project before making changes.", 409)


def project_members(org_id, project_id):
    return Membership.objects.filter(
        org_id=org_id,
        state="active",
        user__is_active=True,
        id__in=ProjectMembership.objects.filter(
            org_id=org_id, project_id=project_id, state="active"
        ).values("membership_id"),
    ).select_related("user")


def clear_assignments(org, actor, member, *, project_id=None, remove_access=True):
    links = ProjectMembership.objects.filter(org=org, membership=member, state="active")
    tasks = Task.objects.filter(org=org, assignee_id=member.id, deleted_at__isnull=True).exclude(
        status="Done"
    )
    if project_id:
        links = links.filter(project_id=project_id)
        tasks = tasks.filter(project_id=project_id)
    # System cleanup also applies to archived projects. Every affected task gets
    # a new version so a previously opened editor cannot restore stale assignments.
    ids = list(tasks.values_list("id", flat=True))
    tasks.update(assignee_id=None, version=F("version") + 1, updated_at=timezone.now())
    for task_id in ids:
        event(org, actor, "task.unassigned", task_id, {"reason": "access_changed"})
    if remove_access:
        links.update(state="removed")
    return len(ids)
