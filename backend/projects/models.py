import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class Project(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, default="")
    archived_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["org", "id"], name="project_org_id")]
        indexes = [models.Index(fields=["org", "archived_at", "id"], name="project_org_page")]


class ProjectMembership(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT)
    project = models.ForeignKey(Project, on_delete=models.PROTECT)
    membership = models.ForeignKey("organizations.Membership", on_delete=models.PROTECT)
    state = models.CharField(max_length=10, default="active")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["org", "id"], name="project_member_org_id"),
            models.UniqueConstraint(
                fields=["org", "project", "membership"], name="project_member_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(state__in=["active", "removed"]), name="project_member_state"
            ),
        ]


class Task(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT)
    project = models.ForeignKey(Project, on_delete=models.PROTECT)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    assignee_id = models.UUIDField(null=True)
    created_by_id = models.UUIDField()
    status = models.CharField(max_length=16, default="To Do")
    priority = models.CharField(max_length=10, default="Medium")
    due_date = models.DateField(null=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True)
    deleted_at = models.DateTimeField(null=True, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["org", "id"], name="task_org_id"),
            models.CheckConstraint(
                condition=models.Q(status__in=["To Do", "In Progress", "Blocked", "Done"]),
                name="task_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(priority__in=["Low", "Medium", "High", "Urgent"]),
                name="task_priority_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(version__gte=1), name="task_version_positive"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="Done", completed_at__isnull=False)
                    | (~models.Q(status="Done") & models.Q(completed_at__isnull=True))
                ),
                name="task_completion_matches_status",
            ),
        ]
        indexes = [
            models.Index(
                fields=["org", "project", "status", "id"], name="task_project_status_page"
            ),
            models.Index(fields=["org", "assignee_id", "due_date"], name="task_assignee_due"),
        ]


class WriteRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    endpoint = models.CharField(max_length=100)
    key = models.CharField(max_length=128)
    payload_hash = models.CharField(max_length=64)
    response = models.JSONField()
    expires_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["org", "id"], name="project_request_org_id"),
            models.UniqueConstraint(
                fields=["org", "actor", "endpoint", "key"], name="project_request_key"
            ),
        ]
