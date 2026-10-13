from collab.models import Comment
from organizations.models import Membership, Organization
from projects.models import Task
from projects.services import accessible_projects

from .models import Notification, Preference


def notification_payload(task, actor, kind, recipient=None):
    if kind == "assignment":
        recipients = {recipient or task.assignee_id}
    else:
        recipients = {task.created_by_id, task.assignee_id}
        recipients.update(
            Comment.objects.filter(
                org_id=task.org_id, task=task, deleted_at__isnull=True
            ).values_list("author_id", flat=True)
        )
    recipients.discard(None)
    recipients.discard(actor.id)
    # Capture only recipients eligible at the moment of the business write.
    eligible = []
    for member in Membership.objects.filter(
        org_id=task.org_id, id__in=recipients, state="active", user__is_active=True
    ):
        if accessible_projects(task.org_id, member).filter(pk=task.project_id).exists():
            eligible.append(str(member.id))
    return {
        "resource_id": str(task.id),
        "task_id": str(task.id),
        "kind": kind,
        "recipients": sorted(eligible),
    }


def eligible_task(org_id, member, task_id):
    if (
        not member
        or member.state != "active"
        or not member.user.is_active
        or not member.user.verified_at
    ):
        return None
    if not Organization.objects.filter(Organization.live_filter(), pk=org_id).exists():
        return None
    return Task.objects.filter(
        org_id=org_id,
        pk=task_id,
        deleted_at__isnull=True,
        project_id__in=accessible_projects(org_id, member).values("id"),
    ).first()


def email_allowed(item):
    member = (
        Membership.objects.select_related("user")
        .filter(org_id=item.org_id, pk=item.recipient_id)
        .first()
    )
    task = eligible_task(item.org_id, member, item.task_id)
    if (
        not task
        or item.kind != "assignment"
        or task.assignee_id != member.id
        or member.role == "Viewer"
    ):
        return None
    if Preference.objects.filter(
        org_id=item.org_id, member=member, assignment_email=False
    ).exists():
        return None
    return member, task


def inbox_query(org, actor):
    tasks = Task.objects.filter(
        org=org,
        deleted_at__isnull=True,
        project_id__in=accessible_projects(org.id, actor).values("id"),
    )
    return Notification.objects.filter(
        org=org, recipient=actor, task_id__in=tasks.values("id")
    ).select_related("task")
