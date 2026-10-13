import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone as django_timezone


class Organization(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    display_name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=63, unique=True)
    timezone = models.CharField(max_length=64, default="UTC")
    state = models.CharField(max_length=16, default="active")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(default=django_timezone.now)
    # A deferred composite FK in 0002 guarantees a matching active Owner at commit.
    owner_membership_id = models.UUIDField()
    owner_role = models.CharField(max_length=10, default="Owner", editable=False)
    owner_state = models.CharField(max_length=10, default="active", editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(state__in=["active", "pending_delete", "deleted"]),
                name="org_valid_state",
            ),
            models.CheckConstraint(
                condition=models.Q(owner_role="Owner", owner_state="active"),
                name="org_requires_active_owner",
            ),
            models.CheckConstraint(
                condition=models.Q(slug__regex=r"^[a-z0-9]+(-[a-z0-9]+)*$"),
                name="org_normalized_slug",
            ),
        ]


class Membership(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org = models.ForeignKey(Organization, on_delete=models.PROTECT)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    role = models.CharField(max_length=10)
    state = models.CharField(max_length=10, default="active")
    created_at = models.DateTimeField(default=django_timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["org", "user"], name="membership_org_user"),
            models.UniqueConstraint(fields=["org", "id"], name="membership_org_id"),
            models.UniqueConstraint(
                fields=["org", "id", "role", "state"], name="membership_owner_reference"
            ),
            models.UniqueConstraint(
                fields=["org"],
                condition=models.Q(role="Owner", state="active"),
                name="membership_one_active_owner",
            ),
            models.CheckConstraint(
                condition=models.Q(role__in=["Owner", "Admin", "Member", "Viewer"]),
                name="membership_valid_role",
            ),
            models.CheckConstraint(
                condition=models.Q(state__in=["active", "removed"]), name="membership_valid_state"
            ),
        ]


class AuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org = models.ForeignKey(Organization, on_delete=models.PROTECT)
    actor_membership_id = models.UUIDField()
    action = models.CharField(max_length=80)
    resource_id = models.UUIDField()
    summary = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=django_timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["org", "id"], name="audit_org_id")]


class OutboxEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org = models.ForeignKey(Organization, on_delete=models.PROTECT)
    audit_id = models.UUIDField()
    topic = models.CharField(max_length=80)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=django_timezone.now)
    dispatched_at = models.DateTimeField(null=True)
    lease = models.UUIDField(null=True)
    last_error = models.CharField(max_length=40, default="")
    delivery_state = models.CharField(max_length=10, default="queued")
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=django_timezone.now, db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["org", "id"], name="outbox_org_id")]


class CreationRequest(models.Model):
    """Actor-scoped global entry point: no organization exists when the request starts."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    key = models.CharField(max_length=128)
    payload_hash = models.CharField(max_length=64)
    response = models.JSONField()
    expires_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["actor", "key"], name="org_create_actor_key")
        ]


class OrganizationUsage(models.Model):
    org = models.OneToOneField(Organization, primary_key=True, on_delete=models.PROTECT)
    active_members = models.PositiveIntegerField(default=1)
    member_limit = models.PositiveIntegerField(default=30)
    active_projects = models.PositiveIntegerField(default=0)
    project_limit = models.PositiveIntegerField(default=20)
    active_tasks = models.PositiveIntegerField(default=0)
    task_limit = models.PositiveIntegerField(default=10000)
    storage_bytes = models.PositiveBigIntegerField(default=0)
    storage_limit = models.PositiveBigIntegerField(default=1073741824)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(storage_bytes__lte=models.F("storage_limit")),
                name="usage_storage_capacity",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    active_members__gte=1, active_members__lte=models.F("member_limit")
                ),
                name="usage_member_capacity",
            ),
            models.CheckConstraint(
                condition=models.Q(active_projects__lte=models.F("project_limit")),
                name="usage_project_capacity",
            ),
            models.CheckConstraint(
                condition=models.Q(active_tasks__lte=models.F("task_limit")),
                name="usage_task_capacity",
            ),
        ]


class Invitation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org = models.ForeignKey(Organization, on_delete=models.PROTECT)
    email = models.EmailField(max_length=254)
    role = models.CharField(max_length=10)
    state = models.CharField(max_length=10, default="pending")
    token_hash = models.CharField(max_length=64)
    token_version = models.PositiveIntegerField(default=1)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(default=django_timezone.now)
    created_by_membership_id = models.UUIDField()
    email_event_id = models.UUIDField(null=True)
    accepted_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["org", "id"], name="invitation_org_id"),
            models.UniqueConstraint(
                fields=["org", "email"],
                condition=models.Q(state="pending"),
                name="invitation_one_pending",
            ),
            models.CheckConstraint(
                condition=models.Q(role__in=["Admin", "Member", "Viewer"]),
                name="invitation_non_owner_role",
            ),
            models.CheckConstraint(
                condition=models.Q(state__in=["pending", "accepted", "revoked", "expired"]),
                name="invitation_valid_state",
            ),
            models.CheckConstraint(
                condition=models.Q(email=models.functions.Lower(models.functions.Trim("email"))),
                name="invitation_normalized_email",
            ),
        ]


class TeamRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org = models.ForeignKey(Organization, on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    key = models.CharField(max_length=128)
    payload_hash = models.CharField(max_length=64)
    response = models.JSONField()
    expires_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["org", "id"], name="team_request_org_id"),
            models.UniqueConstraint(fields=["org", "actor", "key"], name="team_request_key"),
        ]
